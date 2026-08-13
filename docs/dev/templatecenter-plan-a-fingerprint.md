# 方案 A：Snapshot 重试场景下的 RequestID 指纹化修复

| | |
|---|---|
| 状态 | 待评审 |
| 关联 Issue | [#1105](https://github.com/TencentCloud/CubeSandbox/issues/1105) |
| 所属根因 | R3（指纹/Schema 不纯） |
| 影响范围 | CubeMaster（`pkg/templatecenter`） |
| 预估代码量 | ~80 行（含测试） |
| DB 变更 | 无 |
| 是否向后兼容 | 是 |

> 配套阅读：异常状态机总览见 [`templatecenter-design.md` §12](./templatecenter-design.md)，本方案只描述 R3 中 #1105 这一条的具体落地。

---

## 1. 问题陈述

### 1.1 现象

`CreateSnapshot` 重试时，同一"逻辑请求"被算成不同的 requestId，命中不了 dedup 逻辑，导致 Cubelet 端重复 build，结果：

- 同一份 layer 多次落盘，浪费 IO；
- 中间状态被并发 commit 覆盖，最终态不确定；
- PENDING→READY 的时间窗被拉长，模板可用性下降。

### 1.2 复现路径

```text
# 1. 第一次发起
CreateSnapshot(templateID=auto-7f3a, layers=[base@v1], srcImage=ubuntu:22.04)
  → req.Fingerprint = hash(annotation["appSnapshotTemplateID"]="auto-7f3a" + ...)
  → 写表 snapshot_create_request, fingerprint=F1
  → Cubelet 开始 build

# 2. 客户端 5s 没收到 ack，发起重试
CreateSnapshot(templateID=auto-7f3a, layers=[base@v1], srcImage=ubuntu:22.04)
  → 但是每次调用 annotation["appSnapshotTemplateID"]="auto-7f3a" 是同一个 ❌
  → 等等，看起来一样？
```

实际不一样。问题在 `templateID` 是 Master 端在第一次收到请求时**生成的随机 ID**（见 `snapshot_ops.go:165`），后续重试在客户端层面**用的是同一个 ID**，但 Master 侧反序列化的时候会把 annotation `appSnapshotTemplateID` 一并算进 fingerprint 哈希，**而 annotation 在客户端重试时**可能因为 SDK 包装层/中间代理对 header 顺序或大小写做了一次"规整"——具体表现就是同一个逻辑请求，**指纹漂移**。

### 1.3 根因

`fingerprint` 哈希输入里混入了 **请求元数据**（annotation `appSnapshotTemplateID`），而元数据本身是 Master 端逻辑回路产物（"我先把 ID 写进 annotation，再算 fingerprint"），不是请求的"业务身份"。

正确的"业务身份"应该是：
- 模板的逻辑属性（layers、srcImage、annotations 业务字段）
- **不包括** `appSnapshotTemplateID` 这类由 Master 端回填的控制字段

---

## 2. 修复方案

### 2.1 核心思路

把 fingerprint 的计算拆成"canonical 子集"——只哈希**真正决定这次快照产物应该长什么样的字段**，把 Master 自己回填的 annotation 排除掉。

### 2.2 新增 `canonicalFingerprint()` 帮助函数

**位置**：`CubeMaster/pkg/templatecenter/fingerprint.go`（新文件，约 35 行）

```go
// canonicalFingerprint computes a stable hash for snapshot create requests,
// excluding the Master-managed annotation that is filled in *after* the first
// call. This way retries from the client (which carry the same logical
// business intent) produce the same fingerprint as the original call.
//
// Fields included:
//   - srcImage
//   - layer digests
//   - business-level annotations (whitelisted by AnnotationFingerprintPrefix)
//   - request body bytes (after Marshal → Unmarshal into a stable struct)
//
// Fields excluded:
//   - appSnapshotTemplateID (Master-generated, changes on first call)
//   - any annotation whose key starts with "trpc.cube.internal/"
func canonicalFingerprint(req *CreateSnapshotRequest) (string, error) {
    c := canonical{
        SrcImage:     req.SrcImage,
        LayerDigests: digestsOf(req.Layers),
        Annotations:  filterBusiness(req.Annotations),
    }
    b, err := json.Marshal(c)
    if err != nil { return "", err }
    sum := sha256.Sum256(b)
    return hex.EncodeToString(sum[:16]), nil
}
```

### 2.3 修改调用点（2 处）

| 文件 | 行号 | 当前 | 改为 |
|---|---|---|---|
| `snapshot_ops.go` | 165-168 | 写入 `snapshot_create_request.fingerprint` 时直接 hash 整个 req | 调 `canonicalFingerprint(req)` |
| `snapshot_ops.go` | 1403 | 查询既有 fingerprint 时用同样的旧算法 | 同样调 `canonicalFingerprint(req)` |

两处必须**严格用同一份 canonical 化函数**，否则读写两边哈希不一致，dedup 直接失效。

### 2.4 测试

**位置**：`CubeMaster/pkg/templatecenter/fingerprint_test.go`（新文件，约 45 行）

| 用例 | 断言 |
|---|---|
| `TestCanonicalFingerprintStable` | 同一 req 调两次，结果相同 |
| `TestCanonicalFingerprintIgnoresTemplateID` | 仅 `appSnapshotTemplateID` 不同时，结果相同 |
| `TestCanonicalFingerprintSensitiveToLayers` | layers 变化时，结果变化 |
| `TestSnapshotCreateRequestIDIdempotency` | 端到端：发两次 req（templateID 不同），第二次命中既有 fingerprint，不重复 build |

---

## 3. 风险与回退

### 3.1 风险

- **历史 fingerprint 不兼容**：旧数据里 fingerprint 哈希值是按"含 templateID"算法算的。新请求上来后会查不到 dedup 记录，等于第一次重试会当作"全新请求"处理。
  - **影响面**：仅限升级后第一次重试，不影响功能正确性。
  - **缓解**：在 `fingerprint` 表新增一列 `algo_version`，老数据 `algo_version=0`、新数据 `algo_version=1`，查询时优先匹配 `algo_version=1`，匹配不到再 fallback 到 `algo_version=0`，下次清理老 fingerprint 时（建议 30 天后）统一回填。

### 3.2 回退

- 该改动只影响 Master 端的 dedup 行为，不影响产物本身。
- 如线上发现 fingerprint 误命中，关闭 dedup（`skip_dedup=true` flag）即可回到改前行为。

---

## 4. 不做的事

- 不改 `appSnapshotTemplateID` 的语义（仍然是 Master 端 ID，不暴露给 SDK 业务字段）。
- 不动 schema，不动 Cubelet，不动 SDK。
- 不做"客户端预生成 templateID"——那是另一个更大的改动，单独评估。

---

## 5. 验收标准

- [ ] 单测 `TestCanonicalFingerprintStable` / `TestCanonicalFingerprintIgnoresTemplateID` / `TestCanonicalFingerprintSensitiveToLayers` 通过
- [ ] 端到端 `TestSnapshotCreateRequestIDIdempotency` 通过
- [ ] 灰度观察：重试场景下 `snapshot_create_request` 表的新增条数降低 ≥ 80%
- [ ] 旧 fingerprint 数据回退路径可用（`algo_version=0` 仍可命中）

---

## 6. 后续

- 如果方案稳定，可以把 `canonicalFingerprint` 推广到 **template 更新/删除** 等其他有重试语义的接口。
- 长期：把 fingerprint 计算放到 SDK 端，Master 端只做"是否已经见过"的判断，职责更清晰（不在本方案范围）。
