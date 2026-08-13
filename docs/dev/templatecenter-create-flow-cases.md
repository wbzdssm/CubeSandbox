# Create 模板流程图 — 模板中心场景与状态设置说明

主图: [`templatecenter-create-flow-simple.drawio`](./assets/templatecenter-create-flow-simple.drawio) · mermaid 版: `templatecenter-create-flow-simple.mmd`

源文件: `CubeMaster/pkg/templatecenter/image_job_runner.go:21` `runTemplateImageJob`

## 边界说明

模板中心这一块**只管模板制作**，具体来说就是把一个外部镜像变成可复用的模板，并把 ext4 产物持久化到 cbs：
- 拉取镜像层、算 fingerprint、mkfs ext4、写 vda 持久化
- 状态写在 image_jobs / template_definitions / rootfs_artifacts 三张表（全部在 CubeMaster DB 里，TC 进程不持一行 DB）

**下发到各节点不是模板中心的内部 case**——这部分由 CubeMaster 通过 cube-shim / cubelet 完成后，写到 template_replicas 表里。所以主图里"分发 + 副本创建 + 下发汇总判定"那条链是 **CubeMaster 旁路**，跟模板中心制作状态机是分开的两条路。本文档主要看模板中心这一段的成功/失败、长什么样。

如果要从完整生命周期看，请配合看下发旁路的 `distribution.go:204-216` 与 `template_replicas` 表 schema。

---

## 一、成功场景

### 1.1 完全成功（def_READY）

每个步骤都顺利落地，过程里没写任何 status=Failed，summarizeStatus 三分支聚合到 case B（全部 ready）。

典型路径：
1. 拉镜像成功（registry 走得通、CA 算得出 fingerprint）
2. mkfs ext4 成功（磁盘够、cbs 容量足）
3. ext4 推到所有目标节点、每个节点都接收成功
4. def_PENDING → def_CREATING → 所有 replica 终态到达 → summarizeStatus 走 case B
5. 先 `claimAliasForReadyTemplate` 占 alias，再 `UpdateDefinitionStatus(def_READY)` —— 这个顺序是 publish-ordering 约束的关键，注释里特意强调过

终态：
- `template_definitions.status = READY`
- `image_jobs.status = Ready`
- `template_replicas` 全部 `READY`
- alias 已被占位，可被外部 alias 查找命中

### 1.2 部分成功（PARTIALLY_READY 中间态）

expected > 0 && ready > 0 && failed > 0 时落入 PARTIALLY_READY。

这是**中间态**，不是终态。它表示"有的节点 ok 有的节点没建好"，后续任一失败副本 retry 成功会触发 `refreshTemplateReplicaSummary` 重算：

- 重算结果仍是部分成功 → 维持 PARTIALLY_READY
- 重算后全部成功 → 升级 def_READY
- 重算耗尽 / 后台 reconciler 兜底 → 降级 def_FAILED

PARTIALLY_READY **不能直接发沙箱**，前端 / CLI 必须明确展示"基本可用但未全 ready"。

### 1.3 从 redo 重入成功

用户对 PARTIALLY_READY / def_FAILED 的模板 redo 时，`determineRedoResumePhase` 把 phase 折叠到上次的断点：

- 历史到 mkfs 阶段 → 重新跑 BUILDING_EXT4
- 历史到下发阶段 → 重新跑 DISTRIBUTING
- 历史到收尾阶段 → 重新跑 SNAPSHOTTING

也就是说不用从头跑，从断点继续即可。

### 1.4 cancel 后恢复

用户对 PENDING/RUNNING 的模板调 cancelJob → 标 CANCELLED。如果用户后续 redo，会按 redo 重入路径走；如果不 redo，则一直保持 CANCELLED。

---

## 二、失败场景（按根因分类）

模板中心内部的失败 case 按触发根因分 5 类。下面每条都列出"具体场景 + 触发点 + 模板中心怎么标 + 最终落地到哪张表"。

### 2.1 资源不足

这类失败的共同点：基础设施资源（磁盘、内存、节点数）不够用。模板中心侧会**主动检测**并标 Failed，不会一直挂着。

**具体场景 ① 磁盘满 —— ext4 mkfs / 写 cbs 占空间失败**
- 触发点：mkfs ext4 时剩余空间 < 1GB / cbs 配额已满 / vda passthrough 写不进
- 触发位置：`ensureRootfsArtifact` 内部
- 模板中心处理：写 `image_jobs(phase=BUILDING_EXT4, status=FAILED, artifact_status=FAILED)` + cleanup 失败时的 cbs 残留
- 落地表：`image_jobs` + `rootfs_artifacts`

**具体场景 ② 节点本地磁盘满 —— 多副本无法落地**
- 触发点：所有目标节点的 containerd-snapshotter 报 ENOSPC
- 触发位置：`distributeRootfsArtifact` 返回（属下发旁路，仅在主图下游作为参考）
- 模板中心处理：写 `image_jobs(phase=DISTRIBUTING, status=FAILED, error_message="expected=N ready=0")` + cleanup

**具体场景 ③ 目标节点 OOM / cgroup 限制**
- 触发点：节点 memory cgroup 限额太小，containerd-snapshotter 拒收
- 触发位置：同上

**具体场景 ④ 没有健康节点可用**
- 触发点：所有同 instance_type 的节点都不在 healthy 集合
- 触发位置：preflight 检查阶段
- 模板中心处理：preflight 阶段就 fail，写 `image_jobs(phase=PULLING, status=FAILED, error_message=preflight err)` + return，**不会进拉镜像**

**怎么观察**：客户端侧拉镜像阶段看不到进度条 5 → 20 → 70；长时间卡在某一步；image_jobs.error_message 是 "ENOSPC" / "no space" / "cgroup" / "preflight" 等关键词。

### 2.2 数据 / 网络不可达

这类失败是"想拿的东西拿不到"，可能是镜像仓库出问题、CA 证书损坏、镜像层变化了 fingerprint 也变了。

**具体场景 ① 拉镜像层失败**
- 触发点：registry 返回 401 / 镜像被删 / 网络断 / image-accel-service 缓存 miss 后远端 503
- 触发位置：`image.PrepareSource` 内部
- 模板中心处理：`pullProgress.flush(false)` 关进度 sink + 写 `image_jobs(phase=PULLING, status=FAILED, error_message=pull err)` + return

**具体场景 ② CA 证书加载失败**
- 触发点：`loadCubeEgressCA` 读 CA 文件时丢失 / 损坏 / 权限不够
- 触发位置：算 fingerprint 之前
- 模板中心处理：早 fail 在 PULLING 阶段 → `image_jobs(phase=PULLING, status=FAILED, error_message=ca err)` —— **故意不延后到 BUILDING_EXT4 阶段**，避免产物跟 image_jobs 记录 fingerprint 不一致（详见代码注释 :73）

**具体场景 ③ fingerprint hash 不一致（R3 #1105）**
- 触发点：当前产物跟历史 artifact_id 对应的 fingerprint 不匹配
- 触发位置：算完 fingerprint 后比对
- 模板中心处理：剥 `CubeAnnotationAppSnapshotTemplateID` annotation 后重算；本版 RETRY → 重新拉镜像；未来修复方向是 hash 前先剥 annotation 避免冲突

**具体场景 ④ 镜像层 hash 不一致**
- 触发点：层内容跟声明的 digest 对不上（镜像上传时被改过）
- 触发位置：`ensureRootfsArtifact` 内部
- 模板中心处理：写 `image_jobs(phase=BUILDING_EXT4, status=FAILED)` + cleanup

**怎么观察**：error_message 是 "401 unauthorized" / "context deadline exceeded" / "ca not found" / "fingerprint mismatch" 等关键词。

### 2.3 写 DB 失败

这类失败**最具迷惑性**：模板中心内的代码流程可以走完，但 image_jobs 表里的 status 没成功落地。模板中心侧通过悬空状态 + 后台 reconciler 兜底。

**具体场景 ① 第一次写 image_jobs 失败 —— 进入悬挂态**
- 触发点：进入函数第 27 行就要落 image_jobs，DB 主从切换 / 行锁冲突 / 磁盘满
- 触发位置：`image_job_runner.go:27-34`
- 模板中心处理：`logger.Errorf` + `return`，**不写 status=Failed**，所以 image_jobs 里既没有 Running 也没有 Failed
- 兜底：`template_reconciler` 每 10 分钟扫一次 PENDING/RUNNING 的行，超时未更新强制标 Failed
- 前 10 分钟观察：用户看不到这条 job 在 image_jobs 表里有任何记录

**具体场景 ② 中间写库失败 —— 步骤主动标 Failed**
- 触发点：每个 W 步骤里调 `updateTemplateImageJob` 时遇到 DB 异常
- 例如：W2 写 phase=UNPACKING 失败、W3 写 phase=Distributing 失败、W4 写 phase=CreatingTemplate 失败
- 模板中心处理：当前 step 自己走 err 路径写 status=Failed（这是"步骤内主动标"路径）

**具体场景 ③ 最终发布写 image_jobs 失败 —— 又是悬挂态**
- 触发点：第 222 行写 status=Ready/Failed 时 DB 出错
- 触发位置：`image_job_runner.go:222-229`
- 模板中心处理：`logger.Errorf` 不 return（流程已走完），**result_json / status 全丢**，status 卡在 Running phase=CreatingTemplate
- 兜底：同样靠 reconciler 10 分钟循环

**具体场景 ④ 行锁被别的事务持有**
- 触发点：同 template 跑两个 build 流程（典型场景：用户连续点两次 redo）
- 触发位置：进入 `ensureTemplateDefinitionWithOptions` / `withTemplateWriteLock` 后立刻拿到行锁冲突
- 模板中心处理：`withTemplateWriteLock` 阻塞 / `trySessionLock` 跨 master 副本互斥 → 失败的流程标 Failed
- 兜底：天然的串行化，没有悬挂态

**具体场景 ⑤ result_json 序列化失败**
- 触发点：info 含不能 JSON 化的字段（罕见，Go 指针循环 / 自定义 MarshalJSON）
- 触发位置：第 206 行 `json.Marshal(info)`
- 模板中心处理：仅 result_json 写入空字符串，**status/phase 不受影响**

**怎么观察**：进度条跳到 100 但 status=Failed phase=CreatingTemplate / 进度长时间不动但 def 已经查到 / 监控告警 "image_jobs 与 def 状态不一致"。

### 2.4 副本汇总判定为失败

**具体场景：summarizeStatus 走到 case A —— 全失败**
- 触发点：所有 replica 都没建好（即"目标节点全失败"的下发旁路）
- 触发位置：`summarizeStatus` 聚合
- 模板中心处理：写 `template_definitions.status = FAILED` + `template_definitions.last_error = 首个失败 replica.ErrorMessage`
- 但**前台 image_jobs** 走的是 E9（info.Status=Failed）路径，写 `status=Failed phase=CreatingTemplate`，**不**走 E8（finalize 失败）

**具体场景：PARTIALLY 重试耗尽**
- 触发点：refreshTemplateReplicaSummary 多次重算后 failed 副本全部失败
- 触发位置：reconciler 兜底
- 模板中心处理：降级 def_FAILED，由 image_jobs 标 Failed phase=CreatingTemplate

---

## 三、status 怎么变成 Failed

模板中心内部有两种"写 status=Failed"的机制，理解清楚才不会误判根因。

### 3.1 步骤内主动标（"应当发生的失败"）

这是模板中心代码里 90% 失败的处理方式：每个 W 步骤的 err 路径里调 `updateTemplateImageJob(status=Failed, phase=<当前 phase>, progress=100, error_message=err.Error())`。

触发条件：当前步骤有明确的 err 返回。代码格式：

```go
if err != nil {
    _ = updateTemplateImageJob(ctx, jobID, map[string]any{
        "status":        JobStatusFailed,
        "phase":         <当前 phase>,
        "progress":      100,
        "error_message": err.Error(),
    })
    return
}
```

特点：用户**立即**看到 status=Failed，不会有"进度卡住但 status 还是 Running"的诡异状态。进度条直接跳 100，phase 卡在失败的阶段。

### 3.2 步骤外被动标 / 悬挂态（"不应当发生的失败"）

模板中心也有少数情况**不会**主动标 Failed：

| 触发位置 | 现象 | 兜底机制 |
|---|---|---|
| `:27-34` W1 第一次写 image_jobs 失败 | image_jobs 里没记录 | reconciler 10 分钟扫 |
| `:222-229` 最终发布写库失败 | image_jobs 停在 Running phase=CreatingTemplate | reconciler 10 分钟扫 |
| `:206` result_json 序列化失败 | result_json 字段为空，其他不变 | 不需要兜底 |

这两类**应当被关注**：监控需要专门告警 "image_jobs 与 def 状态不一致" 来发现。

### 3.3 后台 reconciler 扫到超时

**触发条件**：
- 扫表：`image_jobs.status IN (PENDING, RUNNING)` 的所有行
- 超时判定：`now - updated_at > threshold`（默认 30 分钟，可配置）
- 跨副本互斥：`trySessionLock("tc_reconcile_building")` 保证多 master 副本只有一个真的跑

**处理**：单 SQL update 把这种悬挂态标 Failed。不调 cleanup 路径 —— 残留由 artifact_gc 兜底。

---

## 四、超时处理

### 4.1 步骤级超时

模板中心内的步骤级超时是**隐式**的：通过 HTTP 客户端 / DB 连接池 / grpc 调用各自的 timeout 间接生效。代码里没有显式的"步骤超过 N 秒就 fail"。

| 步骤 | 实际超时来源 |
|---|---|
| preflight | 立刻返回（不耗时长）|
| 拉镜像 | registry HTTP client 默认 timeout（数十秒）+ image-accel-service 内部超时 |
| mkfs ext4 | vda passthrough IO timeout（秒级）|
| 写 cbs | cbstore 客户端超时（秒级）|
| 副本写入 | cubelet RPC 超时（秒级）|

代码里没有"步骤超时 N 秒强制 fail"的逻辑 —— 长时间卡在某一步通常意味着有上游卡死，需要**后台 reconciler 兜底**。

### 4.2 后台 reconciler 超时（兜底主路径）

**`template_reconciler`** 每 10 分钟扫一轮：

```go
for {
    // 1. 抢锁 trySessionLock("tc_reconcile_building")
    // 2. 扫 image_jobs.status IN (PENDING, RUNNING) 且 updated_at + 30min < now
    // 3. updateTemplateImageJob(status=FAILED, error_message="reconciler timeout")
    // 4. 释放锁等下一轮
    time.Sleep(10 * time.Minute)
}
```

- 跨 master 副本**只有一个**真正跑（DB 全局锁互斥）
- 抢不到锁的本轮直接跳过
- 30 分钟阈值可配（`template_reconciler.go:50`）

### 4.3 artifact_gc 超时清理（兜底清残留）

失败处理里调的 `cleanupFailedRootfsArtifact` 自己可能也失败（cbs 远端挂），就 `log.Errorf` 不重试。后台 `artifact_gc.go` 每 10 分钟扫 `rootfs_artifacts.gc_deadline < now`：

- 标记 CleanupPending
- 调 cubelet 清理 ext4 文件
- 标 DELETED（软删）
- 失败**不重试**，下一轮再扫

**`gc_deadline` 计算**：默认 7 天 TTL（`defaultTemplateArtifactTTL = 7 * 24 * time.Hour`，`job_constants.go:66`）。失败的 artifact 在 7 天内清理，正常的则随 def_DELETED 软删。

### 4.4 用户主动 cancel

cancelJob API → 标 image_jobs.status=CANCELLED（独有的状态，不在主流程图里）。同时拒绝有 PENDING/RUNNING job 的模板被 delete（R1 修复）。

cancel 路径：
```go
// delete.go:205
if strings.EqualFold(job.Status, JobStatusPending) || strings.EqualFold(job.Status, JobStatusRunning) {
    return ErrTemplateActiveJobs  // 拒绝删除
}
```

CANCELLED 不会自动恢复；用户后续 redo 时按 redo 路径走。

### 4.5 超时配置总览

| 项目 | 默认值 | 配置入口 |
|---|---|---|
| image_jobs step timeout | 无显式配置（依赖上游）| — |
| reconciler 扫表周期 | 10 分钟 | 硬编码 |
| reconciler 标 FAILED 阈值 | 30 分钟 | 可配 |
| artifact TTL | 7 天 | `defaultTemplateArtifactTTL` |
| artifact_gc 扫表周期 | 10 分钟 | 硬编码 |
| distribution workers 数 | 4 | `defaultDistributionWorkers` |

---

## 五、成功状态判定细节

### 5.1 PARTIALLY_READY 是中间态

- expected > 0 时才可能落入 PARTIALLY_READY
- ready == 0 → 不算 PARTIALLY，直接失败
- failed == 0 → 不算 PARTIALLY，是 READY

### 5.2 claim alias vs def_READY 顺序

`claimAliasForReadyTemplate` 必须在 `UpdateDefinitionStatus(def_READY)` 之前调用：

- 反过来会导致 client 看到 READY 但 alias 还没占，找不到模板
- 这是 publish-ordering 约束，注释里（`store.go:735`）特意强调过
- 同样的约束在 redo.go:361（refreshTemplateReplicaSummary）也成立

### 5.3 失败清理路径

成功路径不调 cleanup。失败后调 cleanup（`cleanupFailedRootfsArtifact`）清理失败产物，但**仅当 builtFreshArtifact == true 时**清理；如果是重用的已有 artifact，就跳过 cleanup 避免误清。

---

## 六、调试提示表

| 现象 | 大概率原因 | 怎么查 |
|---|---|---|
| 进度卡在 PULLING 长时间不动 | 拉镜像失败但 progress sink 没 flush | 看 error_message + 拉镜像链路日志 |
| 进度跳到 100 但 status=Failed phase=PULLING | 第一次写 image_jobs 失败 | 检查 DB 主从是否切换 |
| def_READY 但 sandbox 启动失败 | PARTIALLY_READY 没真正准备好就发了 | 看 def.replicas 是否全部 READY |
| 进函数立刻 return 没任何记录 | W1 第一次写库失败 | 查 image_jobs 表里这个 job_id 有没有行 |
| 卡在 CreatingTemplate phase 长时间不动 | 最终发布写库失败（E_pub）| 看 reconciler 是否 10 分钟后接管 |
| status=Failed phase=PULLING 之前没 | DB 写库失败被吞了 | 看 Prometheus 指标 + reconciler 日志 |
| cancel 后再 redo 不生效 | 并发 redo 互斥没生效 | 看 `withTemplateWriteLock` 日志 |
| alias 找不到但 def_READY | claim alias 失败被吞了 | 看 CLAIM_WARNING 日志 |

---

## 七、阅读路径

| 你是... | 推荐读 |
|---|---|
| 想了解制作主流程 | §一 成功场景（特别是 1.1 完全成功） |
| 想排查失败 bug | §二 失败场景（5 类）+ §三 status 设置机制 + §六 调试提示 |
| 想看 status 怎么变成 Failed | §三.1 步骤内主动标 + §三.2 步骤外被动标 + §三.3 reconciler 兜底 |
| 想看超时处理 | §四（4.1 步骤级 + 4.2 reconciler + 4.3 artifact_gc + 4.5 配置总览） |
| 想了解成功状态的判定 | §五（5.1 PARTIALLY 中间态 + 5.2 claim alias 顺序）|
| 写代码加 case | §二 + §三 一起看（场景 + status 设置）|
