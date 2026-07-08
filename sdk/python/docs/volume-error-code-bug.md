# Bug 记录：Volume 接口业务错误码全部塌缩为 HTTP 500

- 状态：**待修复（后端）**
- 影响范围：`CubeMaster` volume 处理器 + `CubeAPI` volume 服务
- 发现分支：`coolli/volume-plugin-framework`（本地 `master` 工作树无这些文件）
- 结论一句话：**CubeAPI 的错误码映射只认规范码（`130404` / `130409` / `400`），其余一律映射为 `HTTP 500`；而 CubeMaster 的 volume 处理器把所有业务错误都硬编码成 `retErr(-1)`，导致 404 / 409 / 400 / 405 等语义全部塌缩为 500。**

---

## 1. 根因（以 CubeAPI 映射为准）

`CubeAPI/src/services/volumes.rs` — `cm_err_to_app`：

```rust
fn cm_err_to_app(e: CubeMasterError) -> AppError {
    match &e {
        CubeMasterError::Api { ret_code, .. } if *ret_code == 404 || *ret_code == 130404 => {
            AppError::NotFound(e.to_string())      // → HTTP 404
        }
        CubeMasterError::Api { ret_code, .. } if *ret_code == 409 || *ret_code == 130409 => {
            AppError::Conflict(e.to_string())      // → HTTP 409
        }
        CubeMasterError::Api { ret_code, .. } if *ret_code == 400 => {
            AppError::BadRequest(e.to_string())    // → HTTP 400
        }
        _ => AppError::Internal(anyhow::anyhow!("{e}")),   // → HTTP 500  ← 所有 -1 落这里
    }
}
```

`ret_code` 来源：CubeAPI 的 `parse_response` 从 CubeMaster 响应体的 `ret.ret_code` 直接取值。CubeMaster 发的是 `-1`，因此**永远命中兜底分支 `_ => Internal`**。

规范码定义在 `CubeMaster/pkg/errorcode/error.go`：`ErrorCode_NotFound = 130404`、`ErrorCode_Conflict = 130409`。仓库其它模块（如 sandbox）都用这套码，**只有新增的 volume 处理器没用**——这是偏差点。

---

## 2. CubeMaster 侧硬编码 `-1` 的全部位置

文件：`CubeMaster/pkg/service/httpservice/cube/volume.go`

| 行号 | 错误场景 | 当前 ret_code | CubeAPI 映射结果 | 期望 HTTP |
|---|---|---|---|---|
| 109 | method not allowed | `-1` | **500** | 405 |
| 122 | method not allowed | `-1` | **500** | 405 |
| 136 | db error (list) | `-1` | 500 | 500 ✅（本就该 500）|
| 151 | invalid request（参数解析失败）| `-1` | **500** | 400 |
| 166 | （create 参数校验）| `-1` | **500** | 400 |
| 176 | no volume plugin registered | `-1` | 500 | 500 / 400 视语义 |
| 183 | unknown driver | `-1` | **500** | 400 |
| **192** | **volume already exists（重名）** | `-1` | **500** | **409** |
| 194 | db error checking duplicate | `-1` | 500 | 500 ✅ |
| 199 | plugin create error | `-1` | 500 | 500 ✅ |
| 211 | db create error | `-1` | 500 | 500 ✅ |
| 224 | volumeID is required（get）| `-1` | **500** | 400 |
| **234** | **volume not found（get 不存在）** | `-1` | **500** | **404** |
| 236 | db error (get) | `-1` | 500 | 500 ✅ |
| 249 | volumeID is required（delete）| `-1` | **500** | 400 |
| **259** | **volume not found（delete 不存在）** | `-1` | **500** | **404** |
| 261 | db error (delete) | `-1` | 500 | 500 ✅ |
| 266 | unknown driver (delete) | `-1` | **500** | 400 |
| 270 | plugin destroy error | `-1` | 500 | 500 ✅ |
| 275 | db delete error | `-1` | 500 | 500 ✅ |

> 加粗行是最关键的语义错误：**not found 应 404、already exists 应 409、参数类错误应 400**，现在全是 500。

---

## 3. 对 SDK 的连带影响：`VolumeNotFoundError` 是死代码

`sdk/python/cubesandbox/_volume.py` 的 `_check_response` 依赖 HTTP 状态码分流：

- `404` → `VolumeNotFoundError`
- `401/403` → `AuthenticationError`
- 其余 → `ApiError`

由于后端对"卷不存在"实际返回 **500**，`VolumeNotFoundError`（404 分支）**永远不会被触发**——用户 `Volume.get()` / `Volume.delete()` 一个不存在的卷时，拿到的是笼统的 `ApiError(500)`，而不是可辨识的 `VolumeNotFoundError`；`Volume.create()` 重名时拿到的也是 500 而非 409。

> SDK 侧本身实现正确：字段名与后端 serde 完全一致（请求 `{name, driver}`、响应 `{volumeID, name, token}`、挂载 `{name, path}`）。问题纯粹在后端 `ret_code` 发错。

---

## 4. 修复建议（后端，3 处关键 + 其余按语义补齐）

在 `volume.go` 用规范错误码替换硬编码 `-1`（需 `import` `errorcode` 包）：

```go
// L192 重名 → 409
return &singleVolumeRes{Ret: retErr(int(errorcode.ErrorCode_Conflict), "volume already exists: "+volumeID)}

// L234 / L259 不存在 → 404
return &singleVolumeRes{Ret: retErr(int(errorcode.ErrorCode_NotFound), "volume not found: "+volumeID)}
```

建议同时补齐（可选，提升 API 语义正确性）：

- 参数类错误（L151/L166/L183/L224/L249/L266）→ `400`
- method not allowed（L109/L122）→ CubeAPI 增加 `405` 映射，或后端返回 `400`

修复后：`Volume.get/delete` 缺失卷 → 404 → 正确抛 `VolumeNotFoundError`；`Volume.create` 重名 → 409。

---

## 5. 附带发现：集成脚本持久化用例假阳性

`sdk/python/tests/integration_test_volume.py` 的 Test 5（persistence smoke test）在**同一个沙箱内**写完立即读。即使卷底层根本没持久化，该用例也会 PASS——它只证明"挂载点可写"，未证明"数据跨沙箱/跨重启存活"。

真正的持久化用例应为：**沙箱 A 写入 → 销毁 A → 沙箱 B 挂同一卷 → 读回**（这也是 e2b Volume 的核心卖点：跨沙箱共享）。
