# 下发段（CubeMaster）— 全 case 详细说明

主图: [`templatecenter-distribute-flow.drawio`](./assets/templatecenter-distribute-flow.drawio)

承接: [`templatecenter-make-only.drawio`](./assets/templatecenter-make-only.drawio) — 模板中心制作完成 `def_BUILT` 后的下游

---

## 一、职责边界

下发段**只做产物分发到节点 + 副本管理 + summary 聚合**，跟模板中心段严格分开：

- **入口**：当模板中心完成 `def_BUILT`（产物 ext4 已落 cbs, image_jobs.status=Built, image_jobs.phase=Built）
- **下游触发**：CubeMaster 内部的 dispatcher 通过**事件订阅**方式发现 def_BUILT，主动调 `distributeRootfsArtifact` + 写 replicas
- **退出**：写 def_READY / def_PARTIALLY_READY / def_FAILED 中的一种
- **没跨过**：副本写库、节点视图监控、节点清理——这部分都"嵌"在分发过程里，不是独立阶段

模板中心**不**持有：
- template_replicas 表的写入权（master 分发）
- 节点心跳 / 节点视图读取（master 节点管理）
- 跨副本会话锁（除了 withTemplateWriteLock 同一 template 内串行）

模板中心**依然持有**：
- 失败重试入口（cancelJob / redo API）
- 整体状态对外暴露（GetTemplateInfo 还是 master 这一侧直读）

---

## 二、下发主线（按代码步骤）

### 第 1 步：dispatcher 订阅 def_BUILT 事件

模板中心完成 `def_BUILT` 后，这条事件被推到 dispatcher 的内部事件队列（不是外部消息系统，是 master 进程内事件循环）。

dispatcher 选 next = 这次要分发的模板，调 prepareForDistribution(req)。

### 第 2 步：resolveDistributionTargets

```go
targets := resolveDistributionTargets(instanceType)
if len(targets) == 0 {
    return ErrNoHealthyNodes  // def_BUILT 状态不会转 def_DISTRIBUTING
}
```

按 instance_type 从 `localcache.GetHealthyNodesByInstanceType` 拿健康节点。

- **健康节点 > 0** → 第 3 步
- **健康节点 = 0** → **E_targets：没有健康节点 (悬挂态，def_BUILT 永远不转 def_DISTRIBUTING)**

**E_targets 悬挂态**：def_BUILT 进了 image_jobs 就有，但 def 永远不会转。这种状态需要靠 reconciler 兜底（或者人工干预）。**这条 case 最容易被遗漏**——代码当前没有触发 reconcile 的逻辑，需要排查。

### 第 3 步：写 image_jobs phase=Distributing + def_DISTRIBUTING

```go
updateTemplateImageJob(jobID, {
    "phase":     JobPhaseDistributing,
    "progress":  85,
})
UpdateDefinitionStatus(templateID, DefinitionStatusDistributing, nil)
```

这一步定下来两个状态：
- image_jobs.phase = Distributing
- template_definitions.status = DISTRIBUTING (新引入状态)

**写库本身可能失败**：
- DB 主从切换
- 写 def 时被同一 template 的另一个事务（比如 redo）锁住

失败 → **E_w_distrib：写 def_DISTRIBUTING 失败（悬挂态）**

### 第 4 步：distributeRootfsArtifact（4 worker 并行）

```go
readyTargets, expected, ready, failed, firstErr := distributeRootfsArtifact(
    ctx, req, generatedReq, artifact, templateID, jobID,
)
```

这是 v3 拆出来"分发侧"的核心函数。细节：

- **4 worker 并行**（`defaultDistributionWorkers=4`）
- 每个 worker 调 `cubelet.CreateArtifactOnNode` — node 上 RPC 推送 ext4 + 写 containerd snapshotter
- 每个节点独立的 replica phase:

| replica.Phase | 含义 | 后续 |
|---|---|---|
| `DISTRIBUTING` | 推 ext4 进行中 | 等 cubelet 回包 |
| `DISTRIBUTED` | 接收成功 | 可以被 sandbox 用 |
| `READY` | 完全就绪（含 binding） | 可发沙箱 |
| `FAILED + CleanupRequired=true` | 接收失败，需要清理 ext4 | cleanupArtifactOnNodes |

- **失败的 3 类原因** — drawio 标了 A/B/C 三类：

**原因 A（最常见）— 节点 OOM / cgroup 限额**
- containerd snapshotter 接到 ext4 后要 mount 到容器 namespace，cgroup 限额太小，OOM killer 杀掉或者 snapshotter 拒收
- retCode != 0
- replica.Status = FAILED, ErrorMessage 写 OOM 字样

**原因 B — 节点本地磁盘满 (ENOSPC)**
- containerd snapshotter 写底层 image 内容时 ENOSPC
- 报错关键词 `ENOSPC` / `no space left`

**原因 C — 节点 cbs 配额满**
- 节点 passthrough 写 vda，远程 cbs 配额用完
- 报错关键词 `cbs quota exceeded`

**以上 3 类原因都不会让整个 job 失败**, 只是这一个 replica 标 FAILED。**只有**所有节点都失败的极端 case 才让 job 失败（即 case A，下文）。

### 第 5 步：写 template_replicas + localcache + Redis

```go
UpsertReplica(replica) // 写 DB
localcache.RegisterTemplateReplica(templateID, replica) // 进程内缓存
redis.SetReplica(...) // 备份缓存
```

每个节点一行 replicas 记录。template_replicas 表 + in-process cache + Redis 三处同时写（任何一处失败要补偿，另一处也回滚——目前代码用 errors.Join 累加 err）。

失败子场景 → **E_w_repl：写 template_replicas 失败** + 自动 cleanup 失败的 ext4。

### 第 6 步：summarizeStatus(replicas) 聚合

```go
status := summarizeStatus(replicas)
```

聚合 replicas 终态确定 def 状态：

```go
func summarizeStatus(replicas []ReplicaStatus) (status string, lastError string) {
    var successes, failures int
    for _, r := range replicas {
        if r.Status == ReplicaStatusReady { successes++ }
        else { failures++ }
    }
    if failures > 0 && failures == len(replicas) {
        // case A 全部失败
        status = DefinitionStatusFailed
        lastError = replicas[0].ErrorMessage
    } else if failures == 0 {
        // case B 全部成功
        status = DefinitionStatusReady
    } else {
        // case C 部分成功
        status = DefinitionStatusPartiallyReady
        lastError = <首个失败 ErrorMessage>
    }
    return
}
```

3 分支：

**case A：全部失败 (expected > 0 && ready == 0)**
- def_FAILED + image_jobs.status=Failed + phase=Distributing
- E_A：分发全失败
- 报错字段 `last_error = first failed replica.ErrorMessage`

**case B：全部成功 (ready == expected)**
- claimAliasForReadyTemplate → UpdateDefinitionStatus(def_READY)
- 顺序约束见下文
- 终态：def_READY + alias + 所有 replicas.Status=READY

**case C：部分成功 (ready > 0 && failed > 0)**
- def_PARTIALLY_READY（中间态）
- 部分节点 retry 成功 → refreshTemplateReplicaSummary 重算 → 升级 def_READY
- 部分节点 retry 耗尽 → 降级 def_FAILED

### 第 7 步：claim alias + UpdateDefinitionStatus (case B 专用)

```go
// 1) 先占 alias
if err := claimAliasForReadyTemplate(templateID, alias); err != nil {
    // 反向顺序导致的失败 → cleanup + cleanupArtifactOnNodes
    return err
}
// 2) 再写 def_READY
UpdateDefinitionStatus(templateID, DefinitionStatusReady, nil)
```

**顺序约束（publish-ordering）**：

如果反过来 — 先 UpdateDefinitionStatus(READY) 再 claimAlias — 会出现：
- client 看到 def_READY，立即去查 alias
- alias 还没占位 → 查不到 template
- 用户体验：template "突然消失"

代码 (`store.go:735`) 注释里特意强调过这条约束。

### 第 8 步：终态分发

| 终态 | 触发条件 | 在 def_READY 后发生什么 |
|---|---|---|
| **def_READY** | case B | 用户可发 sandbox, CLI 看到 alias 可解析 |
| **def_PARTIALLY_READY** | case C | 部分节点可发, refreshSummary 监听 retry |
| **def_FAILED** | case A | 用户必须 redo 才能重新走流程 |

---

## 三、replica 状态机子图

每个 replica 节点独立：

```
replica.Status=PENDING + Phase=DISTRIBUTING (分发开始)
        │
        ├─ RPC 成功 → replica.Status=READY + Phase=DISTRIBUTED
        │                                  ↓
        │                          bindGuestVersionToReplica
        │                                  ↓
        │                          replica.Phase=READY (完全就绪)
        │
        └─ RPC 失败 → replica.Status=FAILED + Phase=DISTRIBUTING + ErrorMessage
                                            +
                                     CleanupRequired=true
                                            ↓
                                   cleanupArtifactOnNodes
                                            ↓
                                  CleanupRequired=false
```

**`Phase=READY` 是 `Status=READY + binding 完成` 的标志**。只有 `Phase=READY` 的节点才认为该副本可用。

**主流程图如何给到 distribution.go:204**：`replica.Phase=ReplicaPhaseFailed` 在第 204 行写出，配套 Status 也写。

---

## 四、失败 case 全展开

每条都是 "场景 + 触发位置 + 处理 + 怎么观察"。

### 4.1 E_targets — 没有健康节点 (悬挂态最隐蔽)

**场景**：dispatcher 准备分发，但 `localcache.GetHealthyNodesByInstanceType(instanceType)` 返回空。可能：

- 这台 instance_type 还没启动节点
- 所有这 instance_type 的节点心跳都掉了
- 节点 cgroup 配置错误（虽然 health check 过，但接口超时）

**触发位置**：`distributeRootfsArtifact` 前的 `resolveDistributionTargets` 阶段。

**处理**：
```go
if len(targets) == 0 {
    return ErrNoHealthyNodes  // 当前代码可能直接 return, 不写 image_jobs
}
```

**最终落地**：def_BUILT 不会被改写，永远处于 "产物已就绪但未下发" 状态。image_jobs.status=Built 也保留。

**怎么观察**：
- 监控 "def_BUILT 长期 (>30 分钟) 没转 def_DISTRIBUTING"
- 监控 "image_jobs.phase=Built 长期没变"

**当前 bug**：代码可能没有自动 reconcile 这种悬挂态。要排查 `template_reconciler.go` 是否覆盖 def_BUILT 状态。

### 4.2 E_w_distrib — 写 def_DISTRIBUTING 失败

**场景**：第 3 步写库时 DB 异常。

**子原因**：
- DB 主从切换中
- row 已被锁定（同 template 的 redo 流程）
- connection pool 耗尽

**处理**：
```go
logger.Errorf("update def DISTRIBUTING fail: %v", err)
// 这里 "悬挂" 发生: def 仍是 BUILT, image_jobs phase=Built
```

悬挂态：def_BUILT + image_jobs.phase=Built 永远不变。

**怎么观察**：
- 监控 "def_BUILT 与 phase=Built 时间差过大"
- 监控 "DB 连接池 active 数量"

### 4.3 E_A — case A 全失败

**场景**：summarizeStatus 走到 case A — 所有 replica 都失败。

**触发位置**：`summarizeStatus` 聚合判定。

**处理**：
```go
UpdateDefinitionStatus(templateID, DefinitionStatusFailed, firstError)
// image_jobs (status=Failed, phase=Distributing)
return
```

**最终落地**：
- def_FAILED + image_jobs.status=Failed
- 失败的 ext4 文件由 cleanupArtifactOnNodes 清理
- 客户端 GetTemplateInfo 看到模板已 failed

### 4.4 E_w_repl — 写 template_replicas 失败

**场景**：第 5 步写 replicas 时出错。

**子原因**：
- 外键约束（template_id 在 def 表不存在了 — 极少）
- 字段值超长（node_id 超 64 字节）

**处理**：
```go
errors.Join(
    cleanupArtifactOnNodes(ctx, artifactID, instanceType, failedTargets),
    err,
)
// def 状态没动, 也不算 READY, reconcile 兜底
```

**怎么观察**：
- 监控 "template_replicas 写入失败率"
- 监控 "def_DISTRIBUTING 长期 (>30min) 没升级"

### 4.5 E_part_to_failed — PARTIALLY 全部 retry 失败

**场景**：def_PARTIALLY_READY 中间态后，所有失败副本重试都耗尽了。

**触发位置**：`refreshTemplateReplicaSummary` 重算时。

**处理**：
```go
// 命中 case A 路径
UpdateDefinitionStatus(templateID, DefinitionStatusFailed, lastError)
```

**最终落地**：def_FAILED, 跟 case A 类似。

---

## 五、PARTIALLY 中间态详解

PARTIALLY_READY 是**只有 v3 拆解后才会产生的中间态**：def 已经过了分发阶段，但只有部分副本就绪。

```
def_DISTRIBUTING (分发中)
        ↓
   case C 触发
        ↓
def_PARTIALLY_READY (中间态, 部分就绪)
        ↓
任一 replica 终态到达 → refreshTemplateReplicaSummary 重算
        ↓
┌─────────┴─────────┐
↓
case B                case A
↓
def_READY        ↓
                 def_FAILED
```

**refreshTemplateReplicaSummary** (`store.go:679`)：

```go
func refreshTemplateReplicaSummary(ctx, templateID, alias) (newStatus string, err error) {
    replicas := loadAllReplicas(templateID)
    summaries := summarizeStatus(replicas)
    err = UpdateDefinitionStatus(templateID, newStatus, ...)
    return summaries
}
```

**触发路径**：
1. 任一 replica 终态到达（成功 OR 失败）→ 触发信号
2. for_update 行锁（防并发 redo 同时改）
3. 重读所有 replicas
4. 重新聚合
5. 写 def 状态

**如果中间段出错**（如 refreshTemplateReplicaSummary 自己挂），**Partially 状态会静止**。Reconciler 不会主动改 Partial 状态（按当前设计），所以这是一种新的"伪悬挂"——def 在 PARTIALLY_READY 但 forever 不会动。

**观察途径**：
- 监控 "PARTIALLY_READY 长期不变"
- log 看 refreshTemplateReplicaSummary 调用次数

---

## 六、清理失败的兜底

replica.Status=FAILED 标 CleanupRequired=true 后，需要清理节点上的 ext4。代码 (`distribution.go:48-109`)：

```go
func destroyArtifactOnNode(ctx, artifactID, instanceType, target) (bool, error) {
    // 调 cubelet.DeleteArtifactImage (幂等)
    // 成功清理返回 true; 不需要清理 (没产物) 返回 true
    // RPC 失败返回 err
}
```

**CleanupRequired 失败的影响**：
- 单个 cleanupFailedNode 不阻塞其他节点分发
- 失败次数累积 → GC 兜底

**兜底路径**：
- 后台 `artifact_gc.go` 每 10 分钟扫，标 CleanupPending → 调 cubelet 清理 → 标 DELETED
- 失败不重试，下一轮再扫

---

## 七、监控与调试

| 现象 | 大概率原因 | 怎么查 |
|---|---|---|
| def_BUILT 永远不转 def_DISTRIBUTING | E_targets 没健康节点 | 看节点心跳 + 监控 |
| def_BUILT + image_jobs Built 长期不一致 | E_w_distrib 悬挂 | 看 DB 主从切换 + reconciler |
| def_DISTRIBUTING 长期不变 | 节点分发超时 + 没有 retry 信号 | 看 cubelet RPC 日志 |
| def_PARTIALLY_READY 长期不变 | refreshTemplateReplicaSummary 卡住 | 看 reload replica 行 + for_update 日志 |
| image_jobs.status=Failed + phase=Distributing | case A 全失败 | 看 replicas 表 + 看预期/实际副本数 |
| def_READY 但 sandbox 启动失败 | PARTIALLY_READY 时副本没全部就绪就发了 | 检查 replica.Phase=READY 的节点数 |

---

## 八、上下游接口契约

| 接口 | 模板中心给分发段 | 分发段给外部 |
|---|---|---|
| 事件 | def_BUILT (写一行 def) | def_READY / def_PARTIALLY_READY / def_FAILED (写一行 def) |
| 数据库 | def, image_jobs, rootfs_artifacts | def, image_jobs, template_replicas |
| 状态机拥有者 | 模板中心段 | CubeMaster 下游 |
| 失败 retry 入口 | cancelJob (任意阶段) | redo (case 缺陷) |
| 用户观察终点 | def_BUILT | def_READY (用户可发沙箱) |

**模板中心段不需要知道下游的 replica 状态。分发段不感知模板中心的 phase=BuildingExt4 之类的内部状态。两侧都是 def 状态作为契约。**

---

## 九、阅读路径

| 你是... | 推荐读 |
|---|---|
| 想了解整体流程 | §一 + §二 |
| 想排查分发 bug | §四 5 个失败 case + §七 调试提示 |
| 想理解中间态 | §五 PARTIALLY_READY 中间态详解 |
| 想了解上下游契约 | §八 接口契约 |
| 想理解清理失败影响 | §六 清理失败的兜底 |
