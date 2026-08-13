# Create 模板 — 模板中心制作 (到 def_BUILT) — 全 case 详细说明

主图: [`templatecenter-make-only.drawio`](./assets/templatecenter-make-only.drawio) · mermaid 版: `templatecenter-make-only.mmd`

源文件: `CubeMaster/pkg/templatecenter/image_job_runner.go:21` `runTemplateImageJob`

相关设计图:
- [`templatecenter-distribute-flow.drawio`](./assets/templatecenter-distribute-flow.drawio) — CubeMaster 下发段（从 def_BUILT → def_READY 等）
- [`templatecenter-distribute-timing.drawio`](./assets/templatecenter-distribute-timing.drawio) — 时序补充

---

## 一、职责边界与新状态

模板中心这一段**只做制作**，具体是把一个外部镜像变成"ext4 产物 + metadata 全部到位"的稳定状态。新状态 `def_BUILT` 专门表示：

- `template_definitions.status = BUILT`
- `rootfs_artifacts.status = READY`（ext4 + sha 都已落 cbs）
- `image_jobs.status = Built / phase = Built / progress = 70`

**制作完成即 def_BUILT，下发不归模板中心管**——下发由 CubeMaster 下游（cubelet + replica 写入流程）独立负责。下游根据分发结果写 `def_READY` / `def_PARTIALLY_READY` / `def_FAILED`。

**模板中心内部不持有**:
- 各节点的 template_replicas 记录（这由下游分发写）
- 节点状态 / 心跳信息
- 任何跨副本互斥（除 `withTemplateWriteLock(templateID)`）

**模板中心唯一持有的状态写入权限**：image_jobs / rootfs_artifacts / template_definitions 三张表。

---

## 二、制作主线 (按代码步骤顺序)

下面是主图的"成功路径"，每一步都列出"做什么 + 写什么 + 失败会发生什么（前置引用到 §三）"。

### 第 1 步：请求进入 (POST)

用户 POST `/v1/templates`，body 里有：

```
image: <image-ref>
template_id: <auto-uuid>
instance_type: <optional>
download_base_url: <optional>
```

### 第 2 步：V0 验证请求参数

`runTemplateImageJob` 函数入口处先用入参校验。
- 通过 → 进入第 3 步
- 不通过 → **E0：参数校验失败** (§三.1)

### 第 3 步：W1 第一次写 image_jobs

```go
updateTemplateImageJob(jobID, map[string]any{
    "status":   JobStatusRunning,
    "phase":    JobPhasePulling,
    "progress": 5,
})
```

这一步把 job 行落到 image_jobs 表，状态置 `Running phase=PULLING progress=5`。

**这是整个流程里最先要做的 DB 写入**，如果 DB 这一下就拒绝了，后面所有判断都没意义。

- 写入成功 → 第 4 步
- 写入失败 → **E_w1：第一次写库失败** (§三.2)

### 第 4 步：C1 preflight 通过？

调 `image.EnsureArtifactBuildPreflight` 做前置检查：

1. CA 证书是否能读
2. 目标节点 pool 是否健康（按 instance_type）
3. 用户传的 `instance_type` 是否支持

- 通过 → 第 5 步
- 不通过 → **E1：preflight 不通过** (§三.3)

### 第 5 步：P1 从镜像仓库拉层

调 `image.PrepareSource(SourceSpec{...})` 从镜像仓库拉层。

拉取过程中用 `pullProgressSink(ctx, jobID)` 持续把进度（百分比 + 速度）写到 image_jobs 表。

- 拉取成功 → 第 6 步
- 拉取失败 → **E2：拉取镜像失败** (§三.4)

### 第 6 步：CA 加载 Egress CA 证书

调 `loadCubeEgressCA` 读 CubeEgress 的 CA 证书文件，用来算后续的 fingerprint。

- 成功 → 第 7 步
- 失败 → **E_ca：CA 证书加载失败** (§三.5)

### 第 7 步：FP 算 fingerprint + artifact_id

```go
fingerprint := buildTemplateSpecFingerprintWithCA(req, source.Digest, caFingerprint)
artifactID  := buildArtifactID(fingerprint)
```

剥 `CubeAnnotationAppSnapshotTemplateID` annotation 后，用 `请求 + digest + CA` 计算 `template_spec_fingerprint`。再用 fingerprint 反推 `artifact_id`（同一指纹对应同一产物）。

- hash 相等 → 第 8 步
- R3 fingerprint 不等 → **R1：RETRY** (§四.1)

### 第 8 步：W2 写 image_jobs phase=UNPACKING

```go
updateTemplateImageJob(jobID, map[string]any{
    "artifact_id":               artifactID,
    "template_spec_fingerprint": fingerprint,
    "source_image_digest":       source.Digest,
    "phase":                     JobPhaseUnpacking,
    "progress":                  20,
})
```

写入 fingerprint / artifact_id / 源镜像 digest，phase 转 UNPACKING progress=20。

### 第 9 步：MK mkfs ext4 + 算 sha + 写 cbs

调 `ensureRootfsArtifact`：

1. **mkfs**: 创建 ext4 文件系统（占 vda 路径）
2. **算 sha**: 对整个 ext4 文件算 sha256
3. **持久化**: 把 ext4 写到 cbs（vda passthrough → cbs 块存储）

这一步是"制作"最重的 IO 动作，可能耗时数秒到数分钟，取决于镜像大小。

- 成功 → 第 10 步
- 失败 → **E3：mkfs / cbs 写失败** (§三.6)

### 第 10 步：W3 写 image_jobs + rootfs_artifacts (新引入 phase=Built)

引入新 `phase = JobPhaseBuilt`（新增），明确标识"ext4 产物已就绪"。这一步写：

```go
updateTemplateImageJob(jobID, map[string]any{
    "phase":    JobPhaseBuilt,  // 新值
    "progress": 70,
})
updateRootfsArtifact(ctx, artifactID, map[string]any{
    "status":       ArtifactStatusReady,  // READY (注意: 不是 BUILDING, 是 READY)
    "ext4_sha256":  ext4SHA,
    "cbs_blob_ref": blobRef,
    "size_bytes":   size,
})
```

W3 在哪都可能失败（DB 已经过了一轮，第二次写可能更脆弱）：

- 成功 → 第 11 步
- 失败 → **E_w3：W3 写库失败** (§三.7)

### 第 11 步：def_built 写 template_definitions status=BUILT

调 `ensureTemplateDefinitionWithOptions` 写入 def 行：

```go
ensureTemplateDefinitionWithOptions(ctx, templateID, TemplateStatusBuilt, ...)
```

把 status 从 BUILT 写出去。这一步完成后整段流程结束，模板中心职责交付。

- 写成功 → 第 12 步（终态）
- 写失败 → **E_built：写 def_BUILT 失败** (§三.8)

### 第 12 步：DONE def_BUILT 终态

```
template_definitions.status = BUILT     ← 新增状态
rootfs_artifacts.status     = READY     ← ext4 + sha 都在 cbs
image_jobs.status           = Built     ← 与 def 对齐
image_jobs.phase            = Built
image_jobs.progress         = 70
```

下游 CubeMaster 看到 `def_BUILT` 后会主动触发分发流程（这是另一段流程图 `distribute-flow.drawio`）。

---

## 三、失败 case 全展开

每个失败 case 都按照 "**场景** + **触发点** + **模板中心处理** + **最终落地** + **怎么观察**" 5 段描述。

### 3.1 E0 — 参数校验失败

**场景**：用户 POST 缺字段（缺 `image` / 缺 `template_id`）/ 字段格式非法（image-ref 不符合 URI 规范）/ 权限不够（mTLS 失败 / 不在 ACL 中）。

**触发位置**：`runTemplateImageJob` 函数入口。

**模板中心处理**：直接 `return`，**不写 image_jobs**（避免脏数据）。

**最终落地**：image_jobs 表里没有这一行；HTTP 返回 4xx。

**怎么观察**：监控看 HTTP 4xx 增加；客户端拿到 4xx 后再 POST。

### 3.2 E_w1 — 第一次写库失败（悬挂态）

**场景**：W1 这一步 `updateTemplateImageJob` 写 DB 失败。可能原因：
- DB 主从切换中（主不可写，从延迟生效）
- 行锁冲突（同 template_id 被另一个事务锁住）
- DB 磁盘满 / 临时磁盘满
- 连接池耗尽（master 进程重启中）

**触发位置**：`image_job_runner.go:27-34`（进函数第 27 行就要落 image_jobs）。

**模板中心处理**：
```go
logger.Errorf("update job start fail: %v", err)
return
```
**关键：这时 image_jobs 表里既没有 Running 也没有 Failed 记录**。也就是说这是个**悬挂态**——用户调 GetJobStatus 看不到这条 job_id，但后台代码还在跑（继续往下走也没意义，所以 return）。

**最终落地**：image_jobs 表没有这行；后台 10 分钟内看不到任何记录；reconciler 10 分钟循环才会发现这条丢失。

**怎么观察**：
- 监控告警 "image_jobs 新建率突然下降"
- 客户端收到 5xx 然后重试
- 等 10 分钟后查 image_jobs 表里的悬挂行 → reconciler 会兜底

**为什么 reconciler 能兜底**：因为悬挂态至少 30 分钟内不会过去（reconciler 扫的是 `updated_at` 老的），所以兜底有延时。

### 3.3 E1 — preflight 不通过

**场景**：preflight 阶段检查失败。

**具体子场景**：
- **CA 证书过期** — `image.EnsureArtifactBuildPreflight` 读 CA 文件，证书已过 `not_after` 日期
- **节点不可达** — 当前健康节点集合里没有同 instance_type 的
- **实例类型不支持** — instance_type 不在模板中心支持的枚举里

**触发位置**：`image.EnsureArtifactBuildPreflight(ctx)`，在 W1 → C1 之间。

**模板中心处理**：
```go
updateTemplateImageJob(jobID, map[string]any{
    "status":        JobStatusFailed,
    "phase":         JobPhasePulling,
    "progress":      100,
    "error_message": err.Error(),
})
return
```
**phase 写 PULLING 而不是 PREFLIGHT** —— 因为旧版没单独的 phase。后续监控看 `phase=PULLING status=FAILED error="preflight"` 这种组合就知道是 preflight 报错。

**最终落地**：image_jobs 里 phase=PULLING status=FAILED error_message=preflight err。

**怎么观察**：监控告警 "PULLING phase Failed 但 user 在 preflight 之前没看到进度" / 客户端收到 `image_jobs.phase=PULLING status=FAILED`。

### 3.4 E2 — 拉取镜像失败

**场景**：拉层阶段出错。

**具体子场景**：
- **网络断** — 拉层时连接断（registry tcp 断了）
- **401 Unauthorized** — registry 返回 401（一般是 image-accel-service 没拿到对应 registry 的 credential）
- **镜像被删 / digest 改了** — digest 跟人传的不一致
- **远端 503** — image-accel-service 缓存 miss，落到远端 registry，远端也 503

**触发位置**：`image.PrepareSource` 内部。

**模板中心处理**：
```go
// 1) flush progress sink (false=失败)
if !pullProgressFlushed {
    pullProgress.flush(false)
}
// 2) 写 image_jobs Failed
updateTemplateImageJob(jobID, map[string]any{
    "status":        JobStatusFailed,
    "phase":         JobPhasePulling,
    "progress":      100,
    "error_message": err.Error(),
})
return
```
**为什么先 flush(false)**：pullProgress 是后台 goroutine 持续推进度的；如果拉取失败时不调 flush(false) 关掉，它会继续把脏进度推到 image_jobs 表里。flush(false) 等于告诉它"停下来，记最后一次失败进度"。

**最终落地**：image_jobs 里 phase=PULLING status=FAILED，error_message 是具体的 pull err 字符串。

**怎么观察**：监控统计 "拉层失败率"；按 registry 域名 + 错误码 group by 看（registry vs image-accel 失败率分开）。

### 3.5 E_ca — CA 证书加载失败（早暴露）

**场景**：算 fingerprint 之前要读 CA 证书才能算，但 CA 加载失败。

**具体子场景**：
- 证书文件丢失（运维误删 / 部署时漏拷）
- 证书损坏（文件 inode 还存在但内容截断或乱码）
- 权限不够（master 进程拿不到证书文件读权限）

**触发位置**：`loadCubeEgressCA` 在算 fingerprint 之前。

**模板中心处理**：
```go
updateTemplateImageJob(jobID, map[string]any{
    "status":        JobStatusFailed,
    "phase":         JobPhasePulling,  // 注意: 在 PULLING, 不是 BUILDING
    "progress":      100,
    "error_message": "CA cert load failed: " + err.Error(),
})
return
```

**为什么刻意在 PULLING 而不是 BUILDING 阶段 fail？**

根据代码注释 (`:73`)：
> 早暴露 CA 加载失败。如果延后到 BUILDING 阶段，CA fingerprint 算不出来会让 artifact_id 生成失败，导致后面 `ensureRootfsArtifact` 用错 fingerprint 重算时产物跟 image_jobs 记录的 fingerprint 不一致。

换句话说：CA 加载在 PULLING 阶段 fail 是**故意的设计**，确保用户立刻知道是配置问题（CA 文件丢了）而不是镜像本身问题。

**最终落地**：image_jobs 里 phase=PULLING status=FAILED error="CA cert load failed"。

**怎么观察**：运维监控 "CA 文件 mtime 是否被更新" / 告警 "PULLING 阶段 Failed, error 含 'CA'"。

### 3.6 E3 — mkfs / cbs 写失败（多个子原因）

**场景**：`ensureRootfsArtifact` 流程里失败。这是制作段最重的 IO，子原因最多。

**具体子场景**：

| 子原因 | 触发细节 | error_message 关键词 |
|---|---|---|
| **磁盘满** | mkfs 占用 vda 失败 / cbs 写磁盘配额满 | `ENOSPC` / `no space left` |
| **cbs 配额满** | cbs 服务侧拒绝写入（quota 用完） | `cbs quota exceeded` |
| **vda IO 错** | passthrough 写 vda 时内核层 IO 错 | `I/O error` / `passthrough write failed` |
| **sha 计算结果不一致** | 写完后算 sha 跟入参 fingerprint 对不上 | `sha256 mismatch` (极少) |
| **mkfs 命令退出非零** | mkfs.ext4 二进制报错（vda 损坏） | `mkfs exit code 1` |

**触发位置**：`ensureRootfsArtifact` 内 (`image_job_runner.go:99-110`)。

**模板中心处理**：
```go
// 1) 写 image_jobs 失败 status
updateTemplateImageJob(jobID, map[string]any{
    "status":           JobStatusFailed,
    "phase":            JobPhaseBuildingExt4,
    "progress":         100,
    "artifact_status":  ArtifactStatusFailed,
    "error_message":    err.Error(),
})
// 2) cleanup 失败时的残留（如果是新建产物）
if builtFreshArtifact {
    cleanupFailedRootfsArtifact(...)
}
return
```

**关键细节：`builtFreshArtifact` 标志**

`ensureRootfsArtifact` 返回时如果原本就有现成的 artifact 可复用（同一 fingerprint 已经被建过），`builtFreshArtifact` 是 false。这种情况下**不调 cleanup**，避免误删已被其他引用方使用的产物。

如果是这次全新创建的（`builtFreshArtifact=true`），调 cleanupFailedRootfsArtifact 清掉 ext4 文件 + 释放 cbs 配额。

**清理失败的兜底**：如果 cleanupFailedRootfsArtifact 自己又失败了（cbs 远端挂），就 `log.Errorf` 不重试——后台 `artifact_gc.go` 每 10 分钟扫 `gc_deadline < now` 的 artifact，标 CleanupPending → 调 cubelet 清理 → 标 DELETED。

**最终落地**：image_jobs (phase=BUILDING_EXT4, status=FAILED, artifact_status=FAILED)；rootfs_artifacts 表里是 status=FAILED（或刚 cleanup 完就 CleanupPending）。

**怎么观察**：
- 监控 "BUILDING_EXT4 phase Failed 率"
- cbs 配额剩余空间告警
- 系统内核 log 看 vda IO error

### 3.7 E_w3 — W3 写库失败（另一个悬挂态）

**场景**：第 10 步写 image_jobs + rootfs_artifacts 时 DB 写失败。

**子原因同 E_w1**：DB 主从切换 / 行锁冲突 / 磁盘满 / 连接池。

**触发位置**：`image_job_runner.go` 中 W3 步骤。

**模板中心处理**：
```go
logger.Errorf("W3 update fail: %v", err)
// 这里按代码当前的实现继续往下走 (不 return)
// 让 def_built 这步尝试, 如果 def_built 写成功, 则状态变成 def_BUILT
// 但 image_jobs 里 phase 还是 BuildingExt4 → 不一致
```

**这是新的悬挂态**：image_jobs 停留在 phase=BUILDING_EXT4，但 def 已经是 BUILT。reconciler 10 分钟循环扫 image_jobs.status 仍为 Running/Phase=BuildingExt4 + 30 分钟没更新 → 标 Failed。但 def 已经是 BUILT 状态不会被 reconciler 改回来，会留下 **def_BUILT + image_jobs.status=Failed** 这种不一致组合。

**最终落地**：
- image_jobs (status=Failed phase=BUILDING_EXT4)
- rootfs_artifacts 状态没写进去（DB 没接受）
- template_definitions (status=BUILT)

**怎么观察**：监控 "image_jobs 与 def 状态不一致比率"；alert "def_BUILT 但 image_jobs status=Failed"。

**后续修复方向**：W3 写失败时应该 `return`，反而不应该继续到 def_built。这是个潜在 bug 候选，可以新增 R 系列 issue 跟踪。

### 3.8 E_built — 写 def_BUILT 失败（最隐蔽的悬挂态）

**场景**：最后一步 `ensureTemplateDefinitionWithOptions` 写 `template_definitions.status = BUILT` 失败。

**子原因**：
- DB 连接断
- def 行已被另一个事务锁住（同 template 的 redo 流程）
- def 字段过长 / 触发 schema 限制

**触发位置**：`image_job_runner.go` 最后阶段，`UpdateDefinitionStatus` 之前。

**模板中心处理**：
```go
logger.Errorf("update def to BUILT fail: %v", err)
// 这里也是不 return 的写法 — 因为 image_jobs 已经写过 status=Built
// 流程全做完
```

**最终落地**：
- image_jobs (status=Built phase=Built progress=70) — 这一行已写好
- template_definitions 仍然是 PENDING / 上个状态（def 没更新）

**这是模板中心段最严重的悬挂态**：因为已经走过所有步骤，但 def 还是 PENDING，下游分发流程不会触发（因为 def 不在 BUILT 状态），用户**永远看不到这个模板**，但其 image_jobs 是 Built。

**怎么观察**：
- 监控 "def_BUILT 与 image_jobs Built 不一致"
- 监控 "def 长期 PENDING 但 image_jobs 已 Built"
- 报警阈值: image_jobs.UpdatedAt > 30 分钟 但 def 仍 PENDING

**后续修复方向**：跟 E_w3 一致，失败应该 `return`；或者新引入"补偿事务"在 W3 之后重试 def 写入。

---

## 四、旁支 case 全展开

### 4.1 R1 — R3 fingerprint hash 不一致的 RETRY（已存在）

**场景**：第 7 步算 fingerprint 后比对（跟当前 artifact_meta 比对），出现 R3 #1105 根因。

**触发位置**：FP → W2 之间，已经在 BUILT 主流程之前完成。

**模板中心处理**：
```go
if !fingerprintsEqual(fingerprint, existingFingerprint) {
    // 剥 CubeAnnotationAppSnapshotTemplateID annotation
    fingerprint = rebuildFingerprintWithoutAnnotation(...)
    if !fingerprintsEqual(fingerprint, existingFingerprint) {
        // 还不等，进入 RETRY
        return errors.New("R3 fingerprint mismatch - retry")
    }
}
```

RETRY 流程回到 P1（第 5 步：从镜像仓库拉层），重新拉一次让镜像层上来后用新 fingerprint。

**怎么观察**：
- 监控 "R3 RETRY 触发率"（如果持续高说明 source_image 有问题）
- image_jobs 表里同一个 jobID 出现多条记录（每次 RETRY 一条）

### 4.2 用户主动 cancel（任意 W 步骤触发）

**场景**：用户在 W1 / W2 / W3 任意一步中调 `cancelJob` API。

**模板中心处理**：调 `cancelJob` API 后，模板中心的 `withTemplateWriteLock` 解锁 + 状态写 `JobStatusCancelled`（CANCELLED 独有的 image_jobs status）。

**怎么观察**：image_jobs 表里 status=CANCELLED；HTTP 接口立刻返回成功给客户端。

### 4.3 用户主动 redo（从 def_BUILT / def_FAILED 重入）

**场景**：模板处于 `def_BUILT`（未分发完）/ `def_FAILED` 时用户调 redo API。

**触发位置**：`redo.go` 入口。

**模板中心处理**：`determineRedoResumePhase` 把 phase 折叠回 Build 阶段的某一步：
- 历史到 MK → 从 MK 重跑
- 历史到 W3 → 从 W3 重跑
- 历史到 def_built → 从 def_built 重写

**这是模板中心的正常 redo 入口**，不走 cancel 路径。

### 4.4 并发 retry 重入（同 template 的 redo / cancel 同时跑）

**场景**：两个客户端同时 redo 同一个模板；或一个 redo 一个 cancel。

**模板中心处理**：`withTemplateWriteLock(templateID, func() error {...})` 串行化同 template 的写入（`cache.go:82`）。

跨 master 副本再加 `tc_build_<artifact_id>` 会话锁互斥（分布式锁，master 端 GET_LOCK）。

**怎么观察**：监控 "redo 阻塞时间"；log 看 `withTemplateWriteLock` 等待时长。

---

## 五、def_BUILT 是怎么发布的

模板中心主图写完 def_BUILT 后，**不算 end**——CubeMaster 下游的 dispatch 流程会主动扫到这个状态然后触发分发。这部分不在本图范围内，详见：

- `distribute-flow.drawio` — CubeMaster 下发段（mock）
- `distribute-timing.drawio` — 时序说明

模板中心 vs CubeMaster 下游的接口（事件式，不是 RPC）：

| 状态 | 含义 | 谁写 |
|---|---|---|
| `def_PENDING` | 刚开始准备 | 模板中心 def_built 这步的初态 |
| `def_BUILT` | 产物就绪, 等分发 | 模板中心（def_built 这步写） |
| `def_DISTRIBUTING` | 分发中（replicas 写入中）| CubeMaster 下游 |
| `def_READY` | 分发全部成功 | CubeMaster 下游 |
| `def_PARTIALLY_READY` | 部分成功, 部分失败 | CubeMaster 下游 |
| `def_FAILED` | 制作失败 或 分发全部失败 | 模板中心 (制作失败) / CubeMaster (分发失败) |
| `def_DELETING` | 正在删除 | 模板中心 delete API |

也就是说 **def_BUILT 是模板中心的终点，但只是全局流程的起点**。下面的流程由 CubeMaster 下游推进。

---

## 六、调试提示表

| 现象 | 大概率原因 | 怎么查 |
|---|---|---|
| 客户端立刻 4xx | E0 参数校验失败 | 看 HTTP 日志 + body 校验逻辑 |
| 进度卡在 PULLING 长时间不动, 最终 Failed | E2 拉层失败 + progress sink 没 flush | 看 error_message + pull 日志 |
| status=Failed phase=PULLING + error="preflight" | E1 preflight 不通过 | 看 instance_type / 健康节点数 / CA mtime |
| status=Failed phase=PULLING + error="CA" | E_ca CA 加载失败 | 看 CA 文件权限 + 文件 hash |
| 进度跳到 100 但 status=Failed phase=BUILDING_EXT4 | E3 mkfs 失败 | 看 error + cbs 配额 + vda IO 日志 |
| 进度跳到 70% 长时间不动 | E_w3 / E_built 写库失败 (悬挂态) | 看 DB 主从状态 + reconciler 日志 |
| def 长期 PENDING 但 image_jobs 已到 Built | E_built 悬挂 | 看 def 行 updated_at + 监控告警 |
| status=Failed phase=Distributed progress=70 | 制作完成但 retry 写 status 失败 | 看 error_message (理论上不该出现) |

---

## 七、状态前后对比

| 旧版 (def_CREATING 一直挂着) | 新版 (def_BUILT 是模板中心段的终点) |
|---|---|
| def_CREATING 表示"制作 + 分发都在进行" | def_BUILT 表示"制作完成, 分发未开始" |
| def_CREATING 没有显式边界，下游不知道何时介入 | def_BUILT 是显式 trigger point，发布即可订阅 |
| summarize + def_READY 在 master 一个流程里走完 | def_READY 是下游独立推进的事件 |
| 无法解耦模板中心和下发责任 | 模板中心 = 做出来, 下游 = 分发到节点 |
| 失败时多个 phase 混淆 | 每个 phase 对应到清晰的边界 |

---

## 八、阅读路径

| 你是... | 推荐读 |
|---|---|
| 想了解模板中心制作主流程 | §二 (第 1-12 步) |
| 想排查制作 bug | §三.1-§三.8 (8 个失败 case) |
| 想了解 status 怎么变 Failed | §三 (按代码步骤, 每个失败都是 status=Failed 路径) |
| 想看悬挂态 (bug 来源) | §三.2 E_w1 / §三.7 E_w3 / §三.8 E_built |
| 想看 RETRY / cancel / redo 旁支 | §四.1-§四.4 |
| 想区分模板中心和下发 | §一 / §五 (边界说明 + 事件接口) |
| 想监控 / E2E 测试 | §六 调试提示表 |
| 设计代码改动时 | §七 前后对比 |
