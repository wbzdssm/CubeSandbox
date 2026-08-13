# 镜像中心 Create 模板 — 状态机流程

> **主图**：[`docs/dev/assets/templatecenter-create-job-flow.drawio`](./assets/templatecenter-create-job-flow.drawio)
> **数据源**：`CubeMaster/pkg/templatecenter/image_job_runner.go:21 runTemplateImageJob`
> **关联**：[`templatecenter-state-summary.md`](./templatecenter-state-summary.md) · [`templatecenter-state-overview.drawio`](./assets/templatecenter-state-overview.drawio) · [`templatecenter-state-readwrite.drawio`](./assets/templatecenter-state-readwrite.drawio)

![Create 模板状态机流程](./assets/templatecenter-create-job-flow.drawio)

---

## 一、图谱结构

整张图是一个**拓扑视图**：

```
入口(1) ─→ 中间动作(13 步顺序) ─→ 出口(2)
   A1         A2..A14              N0 / N0f
              ├ 失败分叉(F1-F9 + F3r 旁路)
              └ 旁支(N4/N5/N6/N7)
```

| 元素 | 符号 | 含义 |
|---|---|---|
| **A1-A14** | 蓝/黄/橙/绿 圆角 | 14 个顺序动作（覆盖代码全部执行步骤） |
| **F1-F9** | 红边圆角 | 9 个失败处置节点 |
| **F3r** | 橙边圆角 | R3 #1105 fingerprint hash 不一致 → RETRY 旁路 |
| **N0** | 绿椭圆 | **成功出口** — def_READY + alias claimed |
| **N0p** | 黄椭圆 | **部分成功中间态** — def_PARTIALLY_READY |
| **N0f** | 红椭圆 | **失败出口** — def_FAILED |
| **N4-N7** | 橙椭圆 | 旁支终点（CANCELLED / RETRY / 重算 / 定时扫）|

---

## 二、14 个动作节点（按代码 1:1 顺序执行）

### A1：用户入口

**接收**：`POST /v1/templates`（包含 image 源 / CA / 目标节点列表）

### A2：参数合法性校验

**做什么**：检查 `req/template_id` 是否合法、字段完整性、权限。

**case 触发**：
- `case invalid`：字段缺失 / 权限不足 → **F1**（直接返回错误，未触达 status 字段）

### A3：CA / 镜像源 preflight

**做什么**：校验 source_image CA 证书（避免运行时 hash 不一致）、确认节点可达、确认 instance_type 可用。

**case 触发**：
- `case preflight fail`：CA 过期 / 节点不可达 → **F2**（`phase=PULLING status=FAILED progress=100`）

### A4：PrepareSource 拉取镜像源

**做什么**：调 `image.PrepareSource`，从镜像仓库拉层（经 `image-accel-service` 缓存）。

**case 触发**：
- `case pull fail`：网络断开 / 镜像不存在 / sha256 不匹配 → **F3**（`phase=PULLING status=FAILED progress=100`）

**旁支**：
- `case user.abort` → **N4** CANCELLED

### A5：算 fingerprint + artifactID

**做什么**：以 `req + source_image_digest + CA` 计算 `template_spec_fingerprint`，反推 `artifact_id`（产物唯一标识）。

**case 触发**：
- `case hash mismatch` (R3 #1105)：剥 `CubeAnnotationAppSnapshotTemplateID` 后重算仍不等 → **F3r** 旁路（→ RETRY 后回到 A4）
- `case fingerprint equal`：匹配 → 进入 A6

### A6：写 image_jobs 状态变更（PULLING → UNPACKING）

**做什么**：落库 `template_image_jobs`：
- `phase = UNPACKING`
- `progress = 20`
- 字段：`artifact_id`、`template_spec_fingerprint`、`source_image_digest`

（5 点原则：master 进程内 gorm 直写，TC 进程不直接连 DB）

### A7：写 vda + 持久化到 cbs，mkfs ext4

**做什么**：
1. avm `PrepareImageJob` 内部 unmarshal 镜像 → ext4 文件
2. mkfs ext4 写入本机 `vda` 块设备（经 virtio 透传）
3. `vda` 数据通过 passthrough 落到 `cbs`（远程块存储，本期唯一持久化路径）
4. 算 ext4 文件 sha256

**Dockerless 模式备注**：当不经过 `image.PrepareSource` 直接用本地层时，本节点还会去拉 `image`（私有源）。这一路失败也走 **F4**。

**case 触发**：
- `case artifact fail`：写 cbs / mkfs 失败 → **F4**（`phase=BUILDING_EXT4 artifact_status=FAILED progress=100` + 调 `cleanupFailedRootfsArtifact`）
- `case artifact ok`：进入 A8

**旁支**：
- `case user.abort` → **N4** CANCELLED

### A8：分发 ext4 到各节点

**做什么**：
1. `phase = DISTRIBUTING`、`progress = 70`
2. `distributeRootfsArtifact` 把 ext4 推到各目标节点（mvm 侧的 containerd-snapshotter 接收）
3. 写 `template_replicas`（节点 ID / status / size_bytes） + `localcache.RegisterTemplateReplica` + Redis 备份

**case 触发**：
- `case all-dist-fail`：`expected > 0 && ready == 0`（多副本全部没成功）→ **F5**（`phase=DISTRIBUTING status=FAILED progress=100` + 调 `cleanupFailedRootfsArtifact`）
- `case dist ok`：进入 A9

**旁支**：
- 分布式 retry → **N6** refreshReplicaSummary（每个副本终态到达都触发一次重算）

**旁支**：
- `case user.abort` → **N4** CANCELLED

### A9：进度更新 + 副本统计

**做什么**：写入 `image_jobs` 表，准备进入 `CreatingTemplate` phase：
- `phase = CreatingTemplate`
- `progress = 85`
- 字段：`expected`、`ready`、`failed`（节点副本计数）

### A10：序列化存储请求

**做什么**：`normalizeStoredTemplateRequest` 把 `generatedReq` 反序列化为"存储用请求"格式。

**case 触发**：
- `case normalize fail`：序列化失败 / 字段格式异常 → **F6**（`phase=CreatingTemplate template_status=FAILED progress=100`）

### A11：写入 template_definitions

**做什么**：调 `ensureTemplateDefinitionWithOptions`：
- case `def_PENDING` 已存在（PT 重用）：复用同一行
- case 不存在：插入新行（status 默认 `CREATING`）

**case 触发**：
- `case def create fail`：DB 写入失败 → **F7**（`phase=CreatingTemplate template_status=FAILED progress=100`）

### A12：为每个目标节点创建副本

**做什么**：
- 对每个目标节点创建副本
- 写 `template_replicas` + `localcache` 更新
- case 写失败 → 调用 `cleanupFailedRootfsArtifact` 清理本地 ext4

**case 触发**：
- `case createOK`：进入 A13
- `case persistErr`：副本写失败 → **F8**（`phase=CreatingTemplate template_status=FAILED` + `cleanupFailedRootfsArtifact`）

**旁支**：
- `case user.abort` → **N4** CANCELLED

### A13：占 alias + summarizeStatus 聚合

**做什么**（**严格顺序，不能颠倒**）：

1. **先** 调 `claimAliasForReadyTemplate` 把 def 行的 `alias` 字段先占好
2. 再调 `summarizeStatus(replicas)` 按 3 分支聚合：
   - `successes == 0` → `def_FAILED`
   - `failures == 0`  → `def_READY`
   - default         → `def_PARTIALLY_READY`
3. **后** 调 `UpdateDefinitionStatus` 写入最终 status

> **关键约束**：`claimAliasForReadyTemplate` 必须在 `UpdateDefinitionStatus(def_READY)` 之前调用（store.go:735）。这是为了避免 client 拿到 def_READY 后 alias 还没占到的 publish-ordering 竞态。

**case 触发**：
- `case finalizeErr`：摘要写失败 → **F8**（`phase=CreatingTemplate template_status=FAILED`）

### A14：发布最终状态

**做什么**：最后一次同步写 `image_jobs` 表，写入最终字段：
- `status = Ready / Failed`
- `progress = 100`
- `template_status`
- `result_json`（成功时存产物路径 / metadata）
- `error_message`（失败时存错误）

**case 触发**：
- `case info.Status == StatusFailed`：def 状态判定为 FAILED → **F9**（`jobStatus=Failed jobPhase=CreatingTemplate` + `cleanupFailedRootfsArtifact`）
- `case B 全部成功`：finalize 报告 READY → **N0** 成功出口
- `case C 部分成功`：PARTIALLY_READY 中间态 → **N0p**

**旁支**：
- `RefreshTask` 定时任务（10 min 周期）扫 `image_jobs.status IN (Pending, Running)` 超时 → **N7** 后兜底

---

## 三、9 个失败节点对照表（F1-F9 + F3r）

每个 F 节点在代码里都精确映射到一处 `errors.Join`/`errors.As` 分支。下表是"代码错误 → 节点 → 写入语义"三列对照：

| 节点 | 错误位置 (代码) | 触发 case | image_jobs 写入 | rootfs_artifacts 写入 | template_definitions 写入 |
|---|---|---|---|---|---|
| **F1** | `validate req` | 字段缺失 / 权限不足 | (未写入) | — | — (直接返回 error) |
| **F2** | `EnsureArtifactBuildPreflight` | CA 过期 / 节点不可达 | `phase=PULLING status=FAILED progress=100` | — | (未变) |
| **F3** | `PrepareSource` | 拉层失败 / sha 不匹配 | `phase=PULLING status=FAILED progress=100` | — | (未变) |
| **F3r** | `buildTemplateSpecFingerprint` | R3 #1105 hash 不一致 | (RETRY 状态，未标 FAILED) | — | (未变) |
| **F4** | `ensureRootfsArtifact` | mkfs ext4 / 写 cbs 失败 | `phase=BUILDING_EXT4 artifact=FAILED progress=100` | `status=FAILED` | (未变) |
| **F5** | `expected > 0 && ready == 0` | 多副本全失败 | `phase=DISTRIBUTING status=FAILED progress=100` | `status=CleanupPending` (via cleanupFailedRootfsArtifact) | (未变) |
| **F6** | `normalizeStoredTemplateRequest` | 序列化失败 | `phase=CreatingTemplate template_status=FAILED progress=100` | — | (未变) |
| **F7** | `ensureTemplateDefinitionWithOptions` | def_PENDING 写入失败 | `phase=CreatingTemplate template_status=FAILED progress=100` | — | (未变) |
| **F8** | `createTemplateReplicasOnNodes` 或 `finalizeTemplateReplicas` | 副本创建/收尾失败 | `phase=CreatingTemplate template_status=FAILED progress=100` | `status=CleanupPending` | — |
| **F9** | `info.Status = StatusFailed` 终态判定 | def_FINAL_FAILED | `jobStatus=Failed jobPhase=CreatingTemplate progress=100` | `status=CleanupPending` | — |

---

## 四、3 个出口（最终状态）

### N0 — 成功出口 ✅

```
def_READY + alias 已 claim + status=Ready + progress=100 + result_json
```

**何时到达**：A14 收到 `case B 全部成功`，且无任何 F 触发。

**关键约束**：
- `claimAliasForReadyTemplate` 必须在 `UpdateDefinitionStatus(def_READY)` 之前（store.go:735）
- 写入 `template_definitions.status = READY`、`template_image_jobs.status = Ready`

### N0p — 部分成功中间态 ⚠️

```
def_PARTIALLY_READY + result_json
```

**何时到达**：A14 收到 `case C 部分成功`。

**何时离开**：
- `refreshTemplateReplicaSummary` 重算（N6 旁支触发）→ 如果失败副本 retry 成功 → **N0**
- 同次重算 → 如果失败副本耗尽 retry 次数 → **N0f**

### N0f — 失败出口 ❌

```
def_FAILED + status=Failed + progress=100 + error_message
```

**何时到达**：F1-F9 任一个触发。

**注意**：F9 是"finalize 报告 FAILED 但最终同步写时挂掉"的边缘 case——def 已经 FAILED 但 image_jobs.status 没及时更新，所以补一次专门写。

---

## 五、旁支节点对照

| 节点 | 触发路径 | 终点 | 备注 |
|---|---|---|---|
| **N4** | `cancelJob` API 在 A2-A14 任一中间动作收到 | CANCELLED 终态 | 任意中间阶段可触发，立刻跳出；不写 image_jobs.status=FAILED，写特殊 CANCELLING/CANCELLED |
| **N5** | R3 #1105 fingerprint hash 不一致 (A5) | RETRY → A4 | 重新跑 PrepareSource（剥 `CubeAnnotationAppSnapshotTemplateID` 后再算） |
| **N6** | `refreshReplicaSummary` 任一 replica 终态到达 | 触发 A14 → N0 / N0p / N0f | 每次重算都可能改变 def 状态 |
| **N7** | `RefreshTask` 定时任务 | 扫超时 PENDING/RUNNING 行 → 标 FAILED | 10 分钟周期兜底，避免中间阶段永远卡死 |

---

## 六、DB 写库函数落地（5 点原则第 1 条：TC 进程不直连）

每个动作都映射到 master 进程内的具体 gorm 写函数：

| 动作 | 写库函数 | 源码位置 | 表 |
|---|---|---|---|
| A6 / A9 / A14 | `updateTemplateImageJob` | `job_repo.go:24` | `template_image_jobs` |
| A7 → artifact ok | `updateRootfsArtifact(status=BUILDING→READY, ext4_sha256)` | `artifact_build.go` | `rootfs_artifacts` |
| A8 → replicas | `UpsertReplica` + `localcache.RegisterTemplateReplica` | `cache.go:235` | `template_replicas` |
| A11 → def create | `EnsureTemplateDefinition` (insert / reuse PENDING) | `store.go:1042` | `template_definitions` |
| A13 → claimAlias + status | `claimAliasForReadyTemplate` **必须**在前<br>`UpdateDefinitionStatus(status=READY/PARTIALLY/FAILED, last_error)` | `store.go:735`<br>`store.go:790` | `template_definitions.alias`<br>`template_definitions` |

**附**：所有失败节点都隐含一次 `cleanupFailedRootfsArtifact`（`builtFreshArtifact=true` 的情况），它会反向置 `rootfs_artifacts.status=CleanupPending`，等下次 `artifact_gc` 拉走。

---

## 七、与 v3 其它 Case 的交叉引用

| Case | 状态机图 | 与 Create 的关系 |
|---|---|---|
| Create | **本图（主图）** | 完整 14 步 + 9 个失败 + 4 旁支 + 3 出口 |
| Redo | `templatecenter-state-redo-ha.drawio` 上半 | 复用 **A5-A8 部分**（fingerprint + Distributing） |
| Delete | `templatecenter-state-overview.drawio` Case 3 | def_READY → def_DELETING，**绕过 A1-A14** |
| Self-Heal (R2) | `templatecenter-state-image-create.drawio` 底部 | 不在主流程，独立分支 |
| Build Reconcile | `templatecenter-state-overview.drawio` Case 5 | 后台定时，覆盖 **N7 RefreshTask** |

---

## 八、阅读路径

1. **想看 if-else 路径 / 错误 → 处置** → 本文件 §二（14 个动作）+ §三（9 个失败对照）
2. **想看 DB 写入详情** → 本文件 §六 + `templatecenter-state-readwrite.drawio`
3. **想看 9 phase Job 状态机（汇总视图）** → `templatecenter-state-image-create.drawio` Page 1
4. **想看 5 个 Case 全景** → `templatecenter-state-overview.drawio`
5. **想看运行时架构** → `templatecenter-runtime-mvm-avm.drawio`（mvm/avm 分工）
</content>
<parameter name="path">/Users/silencegao/Workspace/Github/wbzdsssm/CubeSandbox/docs/dev/templatecenter-create-flow.md