# E2B Volume 协议、SDK 入参返回与 Cube 实现

## 1. 基准与范围

核对日期：2026-11-02。固定源码基准：`e2b-dev/E2B@8cc9ed1e`，不是漂移的 `main`，也不是已完成的发布版本兼容认证。

来源：

- [Python 同步 Volume](https://github.com/e2b-dev/E2B/blob/8cc9ed1e/packages/python-sdk/e2b/volume/volume_sync.py)
- [Python 返回模型](https://github.com/e2b-dev/E2B/blob/8cc9ed1e/packages/python-sdk/e2b/volume/types.py)
- [Python 内容连接配置](https://github.com/e2b-dev/E2B/blob/8cc9ed1e/packages/python-sdk/e2b/volume/connection_config.py)
- [Node Volume](https://github.com/e2b-dev/E2B/blob/8cc9ed1e/packages/js-sdk/src/volume/index.ts)
- [Node 选项与返回类型](https://github.com/e2b-dev/E2B/blob/8cc9ed1e/packages/js-sdk/src/volume/types.ts)
- [Node 内容连接配置](https://github.com/e2b-dev/E2B/blob/8cc9ed1e/packages/js-sdk/src/volume/client.ts)
- [管理协议 schema](https://github.com/e2b-dev/E2B/blob/8cc9ed1e/packages/js-sdk/src/api/schema.gen.ts)
- [内容协议 schema](https://github.com/e2b-dev/E2B/blob/8cc9ed1e/packages/js-sdk/src/volume/schema.gen.ts)

范围仅为 Volume，不包含 E2B 的执行、模板、进程等其他 SDK 功能。

目标是按 E2B 的公开入参、返回模型和 HTTP 协议实现，不要求用户改成 Cube 专用的调用形式。当前已经补齐以下同步 Python / Node 核心契约；**尚不能宣布全量、所有边界行为完全一致**，未完成项见第 8 节。

## 2. E2B 提供什么

- 卷管理：创建、列表、查询详情、连接已有卷、销毁。
- 独立内容访问：读取、写入、列目录、创建目录、查询状态、修改元数据、判断存在、删除路径。
- 文件格式：Python 文本、字节、同步字节迭代器；Node 文本、Uint8Array、Blob、ReadableStream。
- 文件上传：Python 字符串、bytes、可读 IO；Node 字符串、ArrayBuffer、Blob、ReadableStream。
- 内容连接参数：独立地址、域名、debug、token、超时、headers、logger、proxy、HTTP 版本。
- Python 同时提供同步 `Volume` 和异步 `AsyncVolume`；Node 方法返回 Promise。
- 卷可挂载到沙箱；内容客户端不通过创建辅助沙箱实现读写。

## 3. HTTP 管理协议

管理地址与内容地址分别解析，不能把管理 API key 当成卷 token。

| 方法 | 路由 | 入参 | 成功状态 | JSON 返回 |
|---|---|---|---|---|
| POST | `/volumes` | JSON：`{"name":"..."}` | 201 | 卷详情及 token |
| GET | `/volumes` | 无 | 200 | 卷基础信息数组 |
| GET | `/volumes/{volumeID}` | 路径参数 `volumeID` | 200 | 卷详情及 token |
| DELETE | `/volumes/{volumeID}` | 路径参数 `volumeID` | 204 | 无响应体 |

管理请求使用 `X-API-Key`。`connect` 不引入新 HTTP 路由，只查询详情再创建句柄。`destroy` 成功返回 true/True，404 返回 false/False。

管理层线上的字段名为 `volumeID`，SDK 映射为 Node `volumeId`、Python `volume_id`。

基础信息：

```json
{"volumeID":"vol-example","name":"example"}
```

详情：

```text
volumeID: string
name: string
token: string
domain?: string
```

`domain` 必须来自真实响应或显式连接配置，不能假装 Cube 服务已提供内容地址。列表 SDK 返回只包含 ID 和 name，不包含空 token、domain 或内部扩展字段。

## 4. HTTP 内容协议

内容请求使用 `Authorization: Bearer <volume-token>`。默认不发送管理 `X-API-Key` 或沙箱 `X-Access-Token`。

前缀：`/volumecontent/{volumeID}`。`path` 是卷内路径，作为查询参数 URL 编码，不拼进路由，也不是宿主机绝对路径。

| 方法 | 后缀 | 查询参数 | 请求体 | 成功状态 / 返回 |
|---|---|---|---|---|
| GET | `/file` | `path` | 无 | 200，`application/octet-stream`，原始文件字节 |
| PUT | `/file` | `path, uid?, gid?, mode?, force?` | `application/octet-stream`，原始字节，不是 multipart | 201，JSON `VolumeEntryStat` |
| GET | `/dir` | `path, depth?` | 无 | 200，JSON `VolumeEntryStat[]`，无包装层 |
| POST | `/dir` | `path, uid?, gid?, mode?, force?` | 无 | 201，JSON `VolumeEntryStat` |
| GET | `/path` | `path` | 无 | 200，JSON `VolumeEntryStat` |
| PATCH | `/path` | `path` | JSON `uid?, gid?, mode?` | 200，JSON `VolumeEntryStat` |
| DELETE | `/path` | `path` | 无 | 204，无响应体 |

- `uid/gid/mode`：schema 为 uint32；`size` 为 int64。
- `force`：布尔值；写文件表示强制覆盖，创建目录表示创建不存在的父目录。
- `depth`：目录深度；SDK 未指定时省略，E2B 注释默认 1。
- 不发送未指定参数；必须保留 `uid=0/gid=0/mode=0/force=false/depth=0`。
- 内容接口的 **任何 404** 都映射成路径不存在，不依赖响应中有错误码。部分 E2B operation 的 404 schema 本身没有响应体。
- `exists` 通过查询路径状态实现，路径不存在返回 false/False，其余失败不吞掉。

## 5. SDK 入参与返回

### 5.1 管理方法

| Python | Node | 返回 |
|---|---|---|
| `Volume.create(name, **opts)` | `Volume.create(name, opts?)` | 可进行内容操作的 Volume 句柄 |
| `Volume.connect(volume_id, **opts)` | `Volume.connect(volumeId, opts?)` | Volume 句柄 |
| `Volume.list(**opts)` | `Volume.list(opts?)` | 基础信息列表 |
| `Volume.get_info(volume_id, **opts)` | `Volume.getInfo(volumeId, opts?)` | `VolumeAndToken` |
| `Volume.destroy(volume_id, **opts)` | `Volume.destroy(volumeId, opts?)` | bool / boolean |

Node 原式调用不再要求把连接配置嵌套到 `{config: ...}`，`create` 也不再要求对象式 `{name: ...}`。Python 支持直接 `api_url/api_key/request_timeout` 关键词。

Cube 原有 `config`、`driver`、空名称创建等入口保留为额外兼容入口，不属于 E2B 的公开契约，也不会在标准 E2B 创建请求中自动添加 driver。

### 5.2 内容方法

| Python | Node | 返回 |
|---|---|---|
| `read_file(path, format="text", stream_idle_timeout=None, **opts)` | `readFile(path, opts?)` | 见格式表 |
| `write_file(path, data, uid=None, gid=None, mode=None, force=None, **opts)` | `writeFile(path, data, opts?)` | `VolumeEntryStat` |
| `list(path, depth=None, **opts)` | `list(path, opts?)`，depth 在 opts 中 | `VolumeEntryStat[]` |
| `make_dir(path, uid=None, gid=None, mode=None, force=None, **opts)` | `makeDir(path, opts?)` | `VolumeEntryStat` |
| `get_info(path, **opts)` | `getInfo(path, opts?)` | `VolumeEntryStat` |
| `update_metadata(path, uid=None, gid=None, mode=None, **opts)` | `updateMetadata(path, metadata, opts?)` | `VolumeEntryStat` |
| `exists(path, **opts)` | `exists(path, opts?)` | bool / boolean |
| `remove(path, **opts)` | `remove(path, opts?)` | None / void |

Python 类级 list/get_info 是管理操作，实例级带路径 list/get_info 是内容操作。

| 格式 | Python 同步返回 | Node 返回 |
|---|---|---|
| text（默认） | str | Promise<string> |
| bytes | bytes | Promise<Uint8Array> |
| blob | 不支持 | Promise<Blob> |
| stream | Iterator<bytes> | Promise<ReadableStream<Uint8Array>> |

### 5.3 返回模型

| 模型 | Python | Node |
|---|---|---|
| `VolumeInfo` | dataclass：volume_id、name | 普通对象：volumeId、name |
| `VolumeAndToken` | VolumeInfo 加 token、domain=None | VolumeInfo 加 token、domain? |
| `VolumeEntryStat` | name、type、path、size、mode、uid、gid、atime、mtime、ctime、target=None | 同名字段，target 可选 |

`VolumeEntryStat` 的线上时间是 date-time 字符串；Python 转为 datetime，Node 转为 Date。type 是 `unknown/file/directory/symlink` 四种枚举值。SDK 不应把响应里其他内部字段扩展到返回对象中。

### 5.4 构造器

Python：`Volume(volume_id, name, token=None, domain=None, debug=None, proxy=None, *, http_version=None)`。

Node：`new Volume(volumeId, name, token, domain?, debug?)`。

Cube 的绑定 Config 构造方式保留为兼容扩展。句柄保存 domain/debug/proxy/HTTP 版本，不把管理请求 headers 自动复用到内容请求。

## 6. 连接选项与默认值

| 内容选项 | Python | Node |
|---|---|---|
| token 覆盖 | token | token |
| 地址覆盖 | api_url | apiUrl |
| 域名 | domain | domain |
| 调试模式 | debug | debug |
| 请求超时 | request_timeout（秒） | requestTimeoutMs（毫秒） |
| 附加头 | headers | headers |
| 日志 | logger | logger |
| 代理 | proxy | proxy |
| HTTP 版本 | http_version：`1.1`/`2` | httpVersion：`1.1`/`2` |
| 流空闲超时 | stream_idle_timeout；E2B 同步实现忽略此参数，使用传输 read 超时 | streamIdleTimeoutMs |
| 用户取消 | 同步无对应 signal | signal：AbortSignal |

E2B 内容地址优先级：逐次 api_url/apiUrl → `E2B_VOLUME_API_URL` → debug 时 `http://localhost:8080` → `https://api.<domain>`。domain 默认 `E2B_DOMAIN` 或 `e2b.app`。创建/连接时，响应有效 domain 优先于调用配置。

Cube 额外支持显式 `Config.volume_api_url` 和 `CUBE_VOLUME_API_URL`，Cube 配置优先于 E2B 环境变量。管理地址使用独立 api_url/apiUrl 或 `CUBE_API_URL/E2B_API_URL`，不能把它隐式当作内容网关地址。

普通请求默认 60 秒；缓冲文件读取/写入默认一小时；超时 0 表示禁用。Node 流式上传按 E2B 不设置总请求超时，由用户 signal 取消；下载流握手后使用独立的分块空闲超时，0 可禁用空闲计时。HTTP2 默认启用协商，HTTP1.1 可显式选择。

显式传入 Cube Config 时保留原有配置预算。HTTPX 超时是传输阶段/空闲超时；Python 显式 wall-clock 总传输期限尚未实现，不能把这两者说成完全一致。

## 7. 当前实现与验证

本轮在 `feat/sdk-volume-direct` 的改动：

- Node 添加 create(name, opts)、位置参数构造、原式管理 opts、VolumeAndToken 和 E2B 同名内容选项类型。
- Python 分离 VolumeInfo / VolumeAndToken，支持原式管理 kwargs、位置参数构造和逐次内容配置。
- 两种 SDK 保留详情 domain；列表只返回基础信息。
- 管理鉴权与内容 Bearer 分开；管理和内容请求均禁止自动重定向。
- Python / Node 支持 proxy、HTTP 版本、headers 和 logger；日志不输出 token。
- 同步 404 与超时 0 行为；Go 的内容 404 也对齐该协议。
- 文件返回元数据，修改/删除不自动刷新 token 或重放请求。

测试包含：管理请求方法/JSON/鉴权、精确列表/详情字段、domain 保留、构造入参、逐次 token/地址/headers、二进制与流、零值参数、404、禁止重定向、取消及 0 超时。

这些是本地协议/契约回归测试，不是把 E2B 和 Cube SDK 同时连接真实服务运行的差分结果。实际通过数量以本轮执行结果为准。

## 8. 尚未完成：不能标记为“完全一致”

1. 当前任务明确只实现同步 Python；`AsyncVolume` 不在本次范围，也没有异步或原生传输新增依赖。
2. Python 保留 HTTPX 同步栈，未实现 E2B 的 wall-clock 总传输期限；管理和内容 kwargs 已有 TypedDict/Unpack 声明，但不宣称完整类型级差分认证。
3. Cube 保留了 HTTPS、卷内路径、禁止根删除、严格元数据等额外校验；部分非法入参/畸形成功响应的异常与 E2B 不同。Node 通用错误还兼容 Cube ApiError 继承，异常层级不是逐类完全相同。
4. 真实代理、TLS HTTP2、全部 HTTP 错误类与 E2B 的自动重试边界，仍需固定版本差分测试；不能靠本地成功用例推断全部行为相同。
5. Go 是协议对齐和 Go 风格 API，不宣称与上述 Python/Node 方法签名相同。
6. Cube 仓内仍没有独立 `/volumecontent` 网关；默认 S3 插件也不签发这种内容能力凭证。SDK 不创建临时沙箱来掩盖缺失。

服务端还需实现真实卷鉴权、挂载租约、并发安全和符号链接根目录隔离。只增加转发路由或接受任意非空 token，不是可上线的 E2B 等价实现。

本期直通验收要求固定版本同步 API/类型契约、同请求同返回差分测试、错误/超时/关闭测试，以及真实网关读取已有卷且不触发沙箱/VM/容器创建。沙箱挂载共享验证属于另一条已有链路，不是直通测试前置条件。
