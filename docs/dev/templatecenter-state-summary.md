# 镜像中心状态机总结

**v3 架构下镜像中心一共有 3 张状态机图，覆盖 5 个 case。** 全部状态由 master 写 DB，TC 进程不直连（5 点原则第 1 条）。

---

## Page 1 — Image 模板 Create（image_job_runner.go）

### Job 层（template_image_jobs 表，按 step 落库）

| Phase | 说明 |
|---|---|
| `JOB_PENDING` | master 入队，未 dequeue |
| `PULLING` | 拉 layer |
| `UNPACKING` | 解压 layer |
| `BUILDING_EXT4` | mkfs ext4 |
| `GENERATING_JSON` | 生成 template config |
| `DISTRIBUTING` | 推送 replica 到节点 |
| `CREATING_TEMPLATE` | 创 template 记录 |
| `SNAPSHOTTING` | 快照 |
| `REGISTERING` | 注册 |
| `JOB_READY` ✅ | 终态 |
| `JOB_FAILED` ❌ | 终态 |

**旁支**：
- 任一 step 失败 → `JOB_FAILED`
- 用户 cancel → `CANCELLING → CANCELLED`（R1 #66）
- PULLING/UNPACKING 时 fingerprint hash 不一致 → `RETRY`（R3 #1105）

### Definition 层（template_definitions 表，replica 维度聚合）

```
def_PENDING → def_CREATING → summarizeStatus(replicas) → {A:FAILED, B:READY, C:PARTIALLY_READY}
```

`summarizeStatus`（store.go:637-658）按 replica 终态分桶：

| 分支 | 触发 | def 终态 | lastError |
|---|---|---|---|
| A | 所有 replica 都 Failed | `def_FAILED` | 首个失败的 ErrorMessage |
| B | 所有 replica 都 Ready | `def_READY` (+ claimAliasForReadyTemplate) | "" |
| C | 混合 | `def_PARTIALLY_READY` (中间态) | 首个失败的 ErrorMessage |

`PARTIALLY_READY` 是真中间态：失败副本 retry 成功 → 再次 summarize → 跳 `def_READY`；重试耗尽 → 跳 `def_FAILED`。每次任一 replica 终态到达都触发 `refreshTemplateReplicaSummary` 重算。

**旁支**：
- 用户 abort → `def_CANCELLING → cleanupTemplate → def_FAILED`
- 任意态 DeleteTemplate → `def_DELETING → def_DELETED`（终态）

### SELF_HEALING（Page 1 底部，R2 修复方案 C）

`OpenRootfsArtifact` 入口加 sha 比对：本机 FS sha256 vs 本机 DB `ext4_sha256`，**不等 → 拿 artifact 全局锁 → SELF_HEALING → 异步重算写回 DB**，失败标记 `SH_FAILED` 走人工。

---

## Page 3 — Redo + R2 元数据/物理不一致（redo.go:187-400）

### 上半 Redo 流程

```
NEEDS_REDO → SubmitRedo → ensureRootfsArtifact → createTemplateReplicasOnNodes
              (BUILDING)       (DISTRIBUTING)
                                          ↓
                              refreshTemplateReplicaSummary
                                          ↓
                       {def_FAILED, def_READY, def_PARTIALLY_READY}
```

**关键属性**：
- 多 replica，**走 `summarizeStatus` 聚合**（同 Page 1 Definition 层）
- READY 分支 → `finalizeArtifact(updateRootfsArtifact)` 写本机 DB `ext4_sha256` → `cleanupTemplateReplicasOnNodes` 清旧副本
- PARTIALLY 中间态同 Page 1
- `expected > 0 && ready == 0` → `JOB_FAILED (Phase=DISTRIBUTING)`

### 下半 R2 根因 + 修复方案 C

**根因**：HA 部署下，master A redo 跑在本机，本机 FS 通过 DRBD/GlusterFS 同步给 master B；**但 DB（MySQL/PG）不跨 master 同步**，所以 master B 本机 DB 还是旧 ext4_sha256。compute node 连 master B 拿到 want=旧、got=新 → mismatch 失败。关联 issue #1005、#852、#989、#499、#578。

**修复方案 C 流程**（`OpenRootfsArtifact` 入口加 self-heal）：

```
OpenRootfsArtifact (任意节点入口)
   ↓ 算本地 ext4 sha256
比对 sha ← 本地 FS vs 本机 DB
   ├─ 一致 → 直接返回文件
   └─ 不等 → SELF_HEALING (拿 artifact 全局锁)
                ↓ 异步重算 sha
            sha 写回 DB + self-heal 日志
                ├─ 成功 → 清 SH 状态
                └─ 失败 → SH_FAILED → 人工介入
```

**解决**：#1005（redo 后 SHA mismatch）、#852（Pod 重启后文件丢失）— **同源问题**：元数据（本机 DB）与物理文件（本机 FS）不在同一存储。

---

## 5 个 Case × 3 张图 总览

| Case | 状态机图 | 路径摘要 |
|---|---|---|
| 1 Create | Page 1 | Job 层 9 phase + Definition 层 summarizeStatus 3 分支 + 终态 READY/FAILED/CANCELLED/DELETED |
| 2 Redo | Page 3 上半 | NEEDS_REDO → ensureRootfsArtifact → createTemplateReplicasOnNodes → summarizeStatus（复用 Page 1 Definition 判定） |
| 3 Delete | Page 1 右侧 | 任意 def_* → def_DELETING → def_DELETED；拒绝 def_PENDING / 有 active job |
| 4 Self-Heal (R2) | Page 1 底部 + Page 3 下半 | OpenRootfsArtifact 比对 sha + SELF_HEALING 异步重算写回 |
| 5 Build Reconcile | diagram 顶部 | trySessionLock(DB 全局锁) 抢抢 tc_reconcile_building → 扫 PENDING/RUNNING 超时 + artifact_gc |

---

## 关联

- 详细流转：[`templatecenter-state-image-create.drawio`](./assets/templatecenter-state-image-create.drawio)  ·  [`templatecenter-state-redo-ha.drawio`](./assets/templatecenter-state-redo-ha.drawio)  ·  [`templatecenter-state-rootsum.drawio`](./assets/templatecenter-state-rootsum.drawio)（5 大根因映射）
- 状态读写路径：[`templatecenter-state-readwrite.drawio`](./assets/templatecenter-state-readwrite.drawio)
- 状态机总览（5 Case 一页签）：[`templatecenter-state-overview.drawio`](./assets/templatecenter-state-overview.drawio)
- 设计文档：[`templatecenter-design.md`](./templatecenter-design.md) §5 状态机章节
- 源码入口：`image_job_runner.go:21`（Create）· `redo.go:187`（Redo）· `delete.go`（Delete）· `snapshot_reconciler`（R2 self-heal）
</content>
<parameter name="path">/Users/silencegao/Workspace/Github/wbzdsssm/CubeSandbox/docs/dev/templatecenter-state-summary.md