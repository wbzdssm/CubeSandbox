# 方案 B：模板 PENDING / BUILDING 阶段用户主动取消

| | |
|---|---|
| 状态 | 待评审 |
| 关联 Issue | [#66](https://github.com/TencentCloud/CubeSandbox/issues/66) |
| 所属根因 | R1（状态机不完整：缺取消态） |
| 影响范围 | CubeMaster（`pkg/templatecenter` + `api/services/templatecenter/v1`） |
| 预估代码量 | ~200 行（5 个改动块） |
| DB 变更 | 1 张新表 + 1 个枚举值 |
| 是否向后兼容 | 是（无 cancel 调用方不受影响） |

> 配套阅读：异常状态机总览见 [`templatecenter-design.md` §12](./templatecenter-design.md)，本方案只描述 R1 中 #66 这一条的具体落地。

---

## 1. 问题陈述

### 1.1 现状

- 模板提交后立刻进入 `PENDING → BUILDING`，**没有用户主动终止的入口**。
- 用户唯一能做的就是 `DELETE` 模板，但 `DELETE` 在状态机里只接受 `READY/FAILED`，BUILDING 阶段会直接 409 拒绝。
- 结果：传错镜像、写错参数后只能等几十分钟跑完，或者联系运维改库，**用户体验差**。

### 1.2 关键约束（必须保留）

- **不丢盘**：用户在 cancel 时已经下载的 layer、写了一半的 ext4 文件要保留——cancel 不等于回滚，恢复后能继续 build。
- **不强杀 Cubelet 进程**：Cubelet 端是 fire-and-forget 的 RPC，被动等 Master 下一步指令，不要做"我让你停你必须立刻停"。
- **状态可解释**：cancel 之后用户看到 `CANCELLED`，不能是 `FAILED`（FAILED 通常意味着系统问题）。

---

## 2. 状态机扩展

### 2.1 新增 2 个 Job 状态 + 1 个 Definition 状态

| 实体 | 新增状态值 | 含义 |
|---|---|---|
| `t_cube_template_image_job.status` | `CANCELLING` | Master 已接受 cancel 请求，正在清理/等待 Cubelet 完成当前阶段 |
| `t_cube_template_image_job.status` | `CANCELLED` | 终态：产物已清理，模板定义转为 FAILED |
| `t_cube_template_image_definition.status` | `FAILED`（已有，但新增 `last_error` 字段约定） | 由 cancel 触发的 FAILED 在 `last_error` 写 `cancelled by user: <reason>` |

### 2.2 状态转移图（新增部分）

```text
            ┌──────────────────────────┐
            │                          │
            │       PENDING/RUNNING    │  ←── 用户调 POST /template/{id}/cancel
            │                          │       CAS: status ∈ {PENDING, RUNNING} → CANCELLING
            └────────────┬─────────────┘
                         │
                         │  cancel accepted
                         ▼
            ┌──────────────────────────┐
            │       CANCELLING         │  ←── 后台 reconcile 每 30s 扫一次
            │                          │       检测到 Cubelet 返回 retCode≠0 / 镜像未完成 / commit 失败
            └────────────┬─────────────┘
                         │
                         │  cleanup done
                         ▼
            ┌──────────────────────────┐
            │       CANCELLED          │  终态，definition.status=FAILED(last_error="cancelled by user: ...")
            │                          │  物理文件保留 24h 后由 artifact_lifecycle GC
            └──────────────────────────┘
```

### 2.3 关键不变量

| # | 不变量 | 保证机制 |
|---|---|---|
| I1 | `CANCELLING` 只能由 `PENDING` 或 `RUNNING` 转入 | CAS 更新（条件写在 SQL WHERE） |
| I2 | 终态（`CANCELLED` / `FAILED` / `READY`）不能再 cancel | API 入口检查 definition.status |
| I3 | cancel 不删物理文件 | artifact_lifecycle 的 cleanup 流程保持不变，只在 GC 阶段判断 |
| I4 | cancel 不删 replica | 用户的 replica 关联在 build 失败时本就不写盘，无需额外处理 |
| I5 | Snapshot 类型拒绝 cancel（必须等 commit 完成） | API 入口按 `template_type=snapshot` 直接 400 |

---

## 3. 改动清单（5 个块）

### B1. 枚举与常量

**位置**：`CubeMaster/pkg/templatecenter/job_constants.go`

```go
// 新增
JobStatusCancelling = "CANCELLING"
JobStatusCancelled  = "CANCELLED"

// Cancel reason 约定
CancelReasonUserRequested = "cancelled by user: %s"  // %s = 客户端传入的 reason
```

### B2. 新增 API

**位置**：`CubeMaster/api/services/templatecenter/v1/templatecenter.proto`

```proto
rpc CancelTemplate(CancelTemplateRequest) returns (CancelTemplateResponse) {
  option (google.api.http) = {
    post: "/v1/template/{template_id}/cancel"
    body: "*"
  };
}

message CancelTemplateRequest {
  string template_id = 1;
  string reason = 2;       // 可选，记录在 last_error
}

message CancelTemplateResponse {
  // 202: 已接受，正在清理
  // 400: snapshot 类型 / 已终态
  // 404: 模板不存在
  // 409: 正在 CANCELLING 中（幂等返回）
  int32 code = 1;
  string message = 2;
  string current_status = 3;  // 便于客户端展示
}
```

**handler**：`CubeMaster/pkg/templatecenter/cancel_handler.go`（新文件，约 50 行）

| 步骤 | 行为 | 失败码 |
|---|---|---|
| 1 | 校验 `template_id` 存在 | 404 |
| 2 | 校验 `definition.status ∈ {PENDING, RUNNING}` | 400 |
| 3 | 校验 `template_type ≠ snapshot` | 400 |
| 4 | CAS 更新 `job.status: PENDING\|RUNNING → CANCELLING` | 409（已是 CANCELLING 幂等成功） |
| 5 | 写 `cancel_log` 表（template_id, requested_at, reason, requester） | — |
| 6 | 返回 202 | — |

### B3. 状态机入口检查（8 处）

**位置**：`CubeMaster/pkg/templatecenter/snapshot_ops.go`、`artifact_lifecycle.go`

在每个状态推进函数开头加 cancel 信号检查：

```go
func proceedToNextPhase(ctx, jobID) error {
    // 8 个 phase 入口都要加这个
    if isCancellationRequested(ctx, jobID) {
        return ErrJobCancelled
    }
    // ... 原有逻辑
}
```

**8 个 phase 入口**（从 `snapshot_ops.go` 的 phase machine 数出来）：

1. `phaseDownloadLayers` —— 下载 layer 之前
2. `phaseExtractLayers` —— 解压 layer 之前
3. `phaseCreateImage` —— mkfs 之前
4. `phaseMountImage` —— mount 之前
5. `phaseCommitSnapshot` —— commit 之前（注意：snapshot 类型到这一步 cancel 已无意义，但这里仍然检查，给 image 类型用）
6. `phaseCleanup` —— 清理之前
7. `phaseUpdateStatus` —— 改 READY 之前
8. `phaseNotifyUser` —— webhook 之前

CANCELLED 的 transition 由 `phaseCleanup` 触发（看到 ErrJobCancelled → 走 cancel cleanup 路径）：

```go
// phaseCleanup 改造
func phaseCleanup(ctx, jobID) error {
    if isCancellationRequested(ctx, jobID) {
        return transitionToCancelled(ctx, jobID)  // 不删物理文件
    }
    // 原 cleanup 逻辑
}
```

### B4. 后台 reconcile

**位置**：`CubeMaster/pkg/templatecenter/cancel_reconciler.go`（新文件，约 40 行）

```go
// 每 30s 扫一次 job.status = CANCELLING 的记录
func (r *CancelReconciler) Run(ctx) {
    ticker := time.NewTicker(30 * time.Second)
    for {
        select {
        case <-ticker.C:
            r.reconcileOnce(ctx)
        case <-ctx.Done():
            return
        }
    }
}

func (r *CancelReconciler) reconcileOnce(ctx) {
    jobs := listJobsByStatus(JobStatusCancelling)
    for _, j := range jobs {
        // 等 Cubelet 当前 RPC 完成（最多等 5 分钟）
        if isCubeletBusy(ctx, j.TemplateID) {
            if time.Since(j.UpdatedAt) > 5*time.Minute {
                forceFailJob(ctx, j.JobID, "cancel timeout: cubelet unresponsive")
            }
            continue
        }
        // Cubelet 已空闲，推进到 CANCELLED
        transitionToCancelled(ctx, j.JobID)
    }
}
```

### B5. DB 变更

**新表**：`t_cube_template_cancel_log`

```sql
CREATE TABLE t_cube_template_cancel_log (
    id            BIGSERIAL PRIMARY KEY,
    template_id   TEXT NOT NULL,
    job_id        TEXT NOT NULL,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    requester     TEXT NOT NULL,            -- user_id / api_key
    reason        TEXT,                     -- 用户传入
    final_status  TEXT,                     -- CANCELLED / timeout-failed
    INDEX idx_template_id (template_id)
);
```

**表结构变更**：

- `t_cube_template_image_job.status`：枚举值增加 `CANCELLING`、`CANCELLED`（如果是 `text` 类型无需 DDL，如果是枚举类型需 migration）。
- `t_cube_template_image_definition.last_error`：加 `cancelled by user: ...` 字符串约定（不改字段类型）。

---

## 4. 风险与回退

### 4.1 风险

| 风险 | 缓解 |
|---|---|
| 8 个 phase 入口漏改一个 | 改完后用 `grep isCancellationRequested snapshot_ops.go` 验证 ≥ 8 处；CI 加一个静态检查统计调用点 |
| CANCELLING 卡住不前进 | reconciler 5 分钟兜底强制 fail |
| 用户 cancel 后立刻重建同名模板 | 模板 ID 是 Master 生成（auto-7f3a），不会撞名；用户层用 template_name 撞名是业务侧自己处理 |
| 旧 SDK 看到新 status 值 | SDK 解析失败时 fallback 到 `UNKNOWN`，不影响调用 |

### 4.2 回退

- `CancelTemplate` API 是新增的，下线即可回退（不会影响现有调用方）。
- CANCELLING/CANCELLED 是新状态值，删除即可（配合一次 DB 清理：把残留 CANCELLING 强制改回 FAILED）。
- 不影响现有任何 PENDING/RUNNING 路径的代码（只在入口加了一个早返回）。

---

## 5. 不做的事

- **不做"秒级强杀"**：cancel 最多等 5 分钟，不立刻 kill Cubelet。理由是 fire-and-forget RPC 中途掐断会让 Cubelet 端资源泄露。
- **不做"取消并立即重试"**：用户想重试请先 cancel，再 POST 一次新的 CreateTemplate。
- **不做批量 cancel**：单模板单接口足够，需要批量请用 list + filter 自己组合。
- **不动 artifact 物理文件**：cancel 不删已下载的 layer，cleanup 阶段也不删——24h 后由 GC 自然清理。

---

## 6. 验收标准

- [ ] 单测：8 个 phase 入口在 cancel 信号下都返回 ErrJobCancelled
- [ ] 单测：snapshot 类型 cancel 返回 400
- [ ] 单测：终态（READY/FAILED/CANCELLED）cancel 返回 400
- [ ] 集成：PENDING 阶段 cancel，5 分钟内变 CANCELLED
- [ ] 集成：BUILDING 阶段 cancel，等当前 Cubelet RPC 返回后变 CANCELLED
- [ ] 集成：cancel 后 definition.status = FAILED, last_error 包含 "cancelled by user: ..."
- [ ] 集成：cancel 不删物理文件（24h 后 GC 仍在）
- [ ] 灰度：监控 `t_cube_template_cancel_log` 表增长，异常 spike 告警

---

## 7. 后续

- 考虑加 `GET /v1/template/{id}/cancel-log` 暴露 cancel 历史（不在本方案范围）。
- 长期：把 cancel 能力下沉到 SDK（客户端可以传 `cancel_token`），现在先做服务端单向能力。
- 跨模板级联 cancel（一个 template cancel 后关联的依赖同步 cancel）——独立 feature，单独评估。
