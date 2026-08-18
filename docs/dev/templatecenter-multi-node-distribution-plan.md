# TemplateCenter 多节点下发完整方案

**版本**：v0.1.0-alpha  
**日期**：2026-08-18  
**范围**：多节点下发状态追踪 + TC → Master 中间态 + Reconciler 恢复 + 重试单节点 + 允许重复下发

---

## 一、实施清单（全部做）

| 步骤 | 文件 | 改动 | 优先级 |
|---|---|---|---|
| 1 | `CubeMaster/pkg/templatecenter/distribution.go` | 记录单节点状态到 `template_replicas` | P0 |
| 2 | `CubeMaster/pkg/service/httpservice/cube/internal_template.go` | 加 Distributing 中间态 | P0 |
| 3 | `CubeMaster/pkg/templatecenter/image_job_runner.go` | 下发完成后标记 Ready | P0 |
| 4 | `CubeMaster/pkg/templatecenter/template_reconciler.go` | 扫描 Distributing 状态的 job 重新下发 | P1 |
| 5 | `CubeMaster/pkg/templatecenter/distribution.go` | 支持"重试单个节点" | P1 |
| 6 | `CubeMaster/pkg/templatecenter/distribution.go` | 支持"允许重复下发"（跳过 Ready 节点） | P1 |

---

## 二、状态机（完整版）

```
┌─────────┐
│ Pending │ (CubeMaster 写入, 等待 TC 构建)
└────┬────┘
     │
     ▼
┌─────────┐
│ Running │ (TC 开始构建)
└────┬────┘
     │
     ▼
┌──────────┐
│ PULLING  │ (TC 拉镜像)
└────┬─────┘
     │
     ▼
┌───────────┐
│ UNPACKING │ (TC 解压)
└────┬──────┘
     │
     ▼
┌───────────────┐
│ BUILDING_EXT4 │ (TC mkfs ext4)
└────┬──────────┘
     │
     ▼
┌───────┐
│ Built │ (TC 构建完成, 回调 master)
└────┬──┘
     │
     ▼
┌──────────────┐
│ Distributing │ (Master 下发到 Cubelet, 中间态)  ← 新增
└────┬─────────┘
     │
     ▼
┌───────┐
│ Ready │ (Master 下发完成, 可用)
└───────┘

失败路径:
任何阶段 → FAILED (记录 error_message + error_phase)

恢复路径:
Distributing → (Reconciler 扫描) → 重新下发 → Ready
```

---

## 三、单节点状态机

```
┌─────────────┐
│ Distributing │ (Master 开始下发到该节点)
└──────┬──────┘
       │
       ▼
   ┌───────┐
   │ Ready │ (该节点下发成功)
   └───────┘

   或

   ┌────────┐
   │ Failed │ (该节点下发失败, 记录 error_message)
   └────────┘

恢复路径:
Failed → (重试单节点) → Distributing → Ready
```

---

## 四、关键改动详解

### 4.1 步骤 1: 记录单节点状态（distribution.go）

**当前**：
```go
func distributeRootfsArtifact(...) {
    for _, target := range targets {
        go func() {
            rsp, err := cubelet.CreateImage(...)
            if err != nil {
                failed++
                return
            }
            ready++
        }()
    }
    return ready, failed, ...
}
```

**改成**：
```go
func distributeRootfsArtifact(...) {
    for _, target := range targets {
        go func() {
            // Step 1: 标记节点状态为 Distributing
            replica := &models.TemplateReplica{
                TemplateID: templateID,
                NodeID:     target.Meta.NodeID,
                NodeIP:     target.Meta.InternalIPV4,
                Status:     "Distributing",
                Phase:      "Distributing",
                ArtifactID: artifact.ArtifactID,
                LastJobID:  jobID,
            }
            upsertReplica(ctx, replica)

            // Step 2: 调用 cubelet 下发
            rsp, err := cubelet.CreateImage(...)
            
            // Step 3: 更新节点状态
            if err != nil {
                replica.Status = "Failed"
                replica.Phase = "Failed"
                replica.ErrorMessage = err.Error()
            } else {
                replica.Status = "Ready"
                replica.Phase = "Ready"
            }
            upsertReplica(ctx, replica)
        }()
    }
    
    // Step 4: 汇总状态
    replicas := listReplicas(ctx, templateID)
    ready := countReady(replicas)
    failed := countFailed(replicas)
    return ready, failed, ...
}
```

### 4.2 步骤 2: Distributing 中间态（internal_template.go）

**当前**：
```go
func handleInternalTemplateJobStatus(c *gin.Context) {
    UpdateTemplateImageJob(ctx, jobID, fields)
    if fields["status"] == "Built" {
        go distributeRootfsArtifact(...)
    }
}
```

**改成**：
```go
func handleInternalTemplateJobStatus(c *gin.Context) {
    // Step 1: 更新 image_jobs 状态
    UpdateTemplateImageJob(ctx, jobID, fields)
    
    // Step 2: 如果 status=Built, 标记为 Distributing (中间态)
    if fields["status"] == "Built" {
        UpdateTemplateImageJob(ctx, jobID, map[string]any{
            "phase":    "Distributing",
            "progress": 80,
        })
        
        // Step 3: 异步下发
        go distributeRootfsArtifact(...)
    }
}
```

### 4.3 步骤 3: 下发完成标记 Ready（image_job_runner.go）

**当前**：
```go
func distributeRootfsArtifact(...) {
    // ... 下发逻辑 ...
    return ready, failed, ...
}
```

**改成**：
```go
func distributeRootfsArtifact(...) {
    // ... 下发逻辑 ...
    
    // 下发完成, 标记为 Ready
    UpdateTemplateImageJob(ctx, jobID, map[string]any{
        "status":   "Ready",
        "phase":    "Ready",
        "progress": 100,
    })
    
    return ready, failed, ...
}
```

### 4.4 步骤 4: Reconciler 恢复（template_reconciler.go）

**新建**：
```go
package templatecenter

import (
    "context"
    "time"
    "github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
)

// StartTemplateReconciler starts the background reconciler that scans for
// jobs stuck in Distributing state and re-triggers distribution.
//
// Runs every 10 minutes. Only one replica runs at a time (DB session lock).
func StartTemplateReconciler(ctx context.Context) {
    ticker := time.NewTicker(10 * time.Minute)
    defer ticker.Stop()
    
    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            if err := reconcileDistributingJobs(ctx); err != nil {
                log.G(ctx).Errorf("reconcile distributing jobs fail: %v", err)
            }
        }
    }
}

// reconcileDistributingJobs scans for jobs stuck in Distributing state
// (updated_at > 30 minutes ago) and re-triggers distribution.
func reconcileDistributingJobs(ctx context.Context) error {
    // Step 1: Acquire DB session lock (only one replica runs)
    lockName := "tc_reconcile_distributing"
    acquired, err := trySessionLock(lockName)
    if err != nil {
        return err
    }
    if !acquired {
        log.G(ctx).Infof("another replica is reconciling, skip")
        return nil
    }
    defer releaseSessionLock(lockName)
    
    // Step 2: Find stuck jobs (status=Distributing AND updated_at > 30min ago)
    jobs, err := findStuckDistributingJobs(ctx, 30*time.Minute)
    if err != nil {
        return err
    }
    
    log.G(ctx).Infof("found %d stuck distributing jobs", len(jobs))
    
    // Step 3: Re-trigger distribution for each job
    for _, job := range jobs {
        log.G(ctx).Infof("re-triggering distribution for job_id=%s", job.JobID)
        
        // Re-run distributeRootfsArtifact
        go func(jobID string) {
            if err := redistributeArtifact(ctx, jobID); err != nil {
                log.G(ctx).Errorf("redistribute artifact fail: job_id=%s err=%v", jobID, err)
            }
        }(job.JobID)
    }
    
    return nil
}
```

### 4.5 步骤 5: 重试单个节点（distribution.go）

**新建**：
```go
// RetryNodeDistribution retries distribution to a specific node.
// Used when a single node fails to receive the artifact.
func RetryNodeDistribution(ctx context.Context, templateID, nodeID string) error {
    // Step 1: Find the replica
    replica, err := findReplica(ctx, templateID, nodeID)
    if err != nil {
        return fmt.Errorf("find replica: %w", err)
    }
    
    // Step 2: Check if node is in Failed state
    if replica.Status != "Failed" {
        return fmt.Errorf("node %s is not in Failed state (current: %s)", nodeID, replica.Status)
    }
    
    // Step 3: Re-trigger distribution to this node
    target, err := findNodeByNodeID(ctx, nodeID)
    if err != nil {
        return fmt.Errorf("find node: %w", err)
    }
    
    // Step 4: Call distributeRootfsArtifact for this single node
    return distributeRootfsArtifactToNode(ctx, templateID, replica.ArtifactID, target, replica.LastJobID)
}
```

### 4.6 步骤 6: 允许重复下发（distribution.go）

**改 `distributeRootfsArtifact`**：
```go
func distributeRootfsArtifact(...) {
    for _, target := range targets {
        go func() {
            // Step 1: Check if node already has the artifact (skip if Ready)
            existing, err := findReplica(ctx, templateID, target.Meta.NodeID)
            if err == nil && existing.Status == "Ready" {
                log.G(ctx).Infof("node %s already has artifact %s, skip", target.Meta.NodeID, artifact.ArtifactID)
                return
            }
            
            // Step 2: Mark as Distributing
            replica := &models.TemplateReplica{
                TemplateID: templateID,
                NodeID:     target.Meta.NodeID,
                NodeIP:     target.Meta.InternalIPV4,
                Status:     "Distributing",
                Phase:      "Distributing",
                ArtifactID: artifact.ArtifactID,
                LastJobID:  jobID,
            }
            upsertReplica(ctx, replica)
            
            // Step 3: Call cubelet
            rsp, err := cubelet.CreateImage(...)
            
            // Step 4: Update status
            if err != nil {
                replica.Status = "Failed"
                replica.Phase = "Failed"
                replica.ErrorMessage = err.Error()
            } else {
                replica.Status = "Ready"
                replica.Phase = "Ready"
            }
            upsertReplica(ctx, replica)
        }()
    }
    
    // ... 汇总状态 ...
}
```

---

## 五、测试 Case

### 5.1 正常 Case：多节点下发

```bash
# 1. 提交构建
cubemastercli tpl create-from-image --image nginx:latest --alias test-multi-node --detach

# 2. 查询进度
# 期望: Pending → Running → PULLING → UNPACKING → BUILDING_EXT4 → Built → Distributing → Ready

# 3. 查询单节点状态
docker exec cube-mysql mysql -ucube -pcube_pass cube_mvp -e \
  "SELECT node_id, node_ip, status, phase FROM template_replicas WHERE template_id='tpl-xxx'"

# 期望:
# +---------+-----------+--------+--------+
# | node_id | node_ip   | status | phase  |
# +---------+-----------+--------+--------+
# | node-1  | 10.0.0.1  | Ready  | Ready  |
# | node-2  | 10.0.0.2  | Ready  | Ready  |
# | node-3  | 10.0.0.3  | Ready  | Ready  |
# +---------+-----------+--------+--------+
```

### 5.2 失败 Case：单节点下发失败

```bash
# 1. 提交构建
cubemastercli tpl create-from-image --image nginx:latest --alias test-node-fail --detach

# 2. 模拟 node-2 下发失败 (停掉 node-2 的 cubelet)

# 3. 查询单节点状态
docker exec cube-mysql mysql -ucube -pcube_pass cube_mvp -e \
  "SELECT node_id, node_ip, status, phase, error_message FROM template_replicas WHERE template_id='tpl-xxx'"

# 期望:
# +---------+-----------+--------+--------+----------------+
# | node_id | node_ip   | status | phase  | error_message  |
# +---------+-----------+--------+--------+----------------+
# | node-1  | 10.0.0.1  | Ready  | Ready  |                |
# | node-2  | 10.0.0.2  | Failed | Failed | connect refuse |
# | node-3  | 10.0.0.3  | Ready  | Ready  |                |
# +---------+-----------+--------+--------+----------------+

# 4. 重试单个节点
cubemastercli tpl retry-node --template-id tpl-xxx --node-id node-2

# 5. 再次查询
# 期望: node-2 状态变为 Ready
```

### 5.3 失败 Case：Master 挂了恢复

```bash
# 1. 提交构建
cubemastercli tpl create-from-image --image nginx:latest --alias test-master-crash --detach

# 2. 等到 Distributing 状态
while true; do
  PHASE=$(curl -s http://localhost:8089/cube/template/build/$JOB_ID/status | jq -r .job.phase)
  [ "$PHASE" = "Distributing" ] && break
  sleep 2
done

# 3. 杀掉 master
pkill -9 cubemaster

# 4. 重启 master
./build/cubemaster -conf=conf.yaml &

# 5. 等待 reconciler 扫描 (10 分钟)
sleep 600

# 6. 查询 job 状态
# 期望: reconciler 发现 Distributing 状态的 job, 重新下发, 状态变为 Ready
curl -s http://localhost:8089/cube/template/build/$JOB_ID/status | jq .
```

### 5.4 失败 Case：重复下发（允许跳过 Ready 节点）

```bash
# 1. 提交构建
cubemastercli tpl create-from-image --image nginx:latest --alias test-redistribute --detach

# 2. 等待构建完成 (status=Ready)

# 3. 再次提交同样的构建 (同 image + instance_type)
cubemastercli tpl create-from-image --image nginx:latest --alias test-redistribute-v2 --detach

# 4. 查询单节点状态
# 期望: 已经有 Ready 状态的节点被跳过, 不重复下发
docker exec cube-mysql mysql -ucube -pcube_pass cube_mvp -e \
  "SELECT node_id, status, phase FROM template_replicas WHERE artifact_id='art-xxx'"

# 期望:
# +---------+--------+--------+
# | node_id | status | phase  |
# +---------+--------+--------+
# | node-1  | Ready  | Ready  |  (跳过, 不重复下发)
# | node-2  | Ready  | Ready  |  (跳过, 不重复下发)
# | node-3  | Ready  | Ready  |  (跳过, 不重复下发)
# +---------+--------+--------+
```

---

## 六、DB Schema 变更

**不需要变更**，`template_replicas` 表已经有所有需要的字段：
- `node_id` / `node_ip`（节点标识）
- `status` / `phase`（节点状态）
- `artifact_id`（产物标识）
- `last_job_id`（最后一次构建 job_id）
- `error_message`（错误信息）

---

## 七、监控指标

**新增 Prometheus 指标**：

```go
// distribution.go
var (
    nodeDistributionTotal = prometheus.NewCounterVec(
        prometheus.CounterOpts{
            Name: "template_node_distribution_total",
            Help: "Total number of node distributions",
        },
        []string{"node_id", "status"},
    )
    
    nodeDistributionDuration = prometheus.NewHistogramVec(
        prometheus.HistogramOpts{
            Name: "template_node_distribution_duration_seconds",
            Help: "Duration of node distribution",
        },
        []string{"node_id"},
    )
    
    reconcilerScanTotal = prometheus.NewCounter(
        prometheus.CounterOpts{
            Name: "template_reconciler_scan_total",
            Help: "Total number of reconciler scans",
        },
    )
    
    reconcilerStuckJobs = prometheus.NewGauge(
        prometheus.GaugeOpts{
            Name: "template_reconciler_stuck_jobs",
            Help: "Number of stuck distributing jobs found by reconciler",
        },
    )
)
```

**查询**：
```bash
# 单节点下发成功率
curl http://localhost:8089/metrics | grep template_node_distribution_total

# Reconciler 扫描次数
curl http://localhost:8089/metrics | grep template_reconciler_scan_total

# 当前 stuck job 数
curl http://localhost:8089/metrics | grep template_reconciler_stuck_jobs
```

---

**完成！** 这份文档包含了完整的多节点下发方案，所有 6 个步骤都会做。
