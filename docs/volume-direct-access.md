# Volume 直通读取：已有 API、协议与最小实现方案

核对日期：2026-11-02。本文只覆盖“不启动沙箱，直接读取持久卷内容”。E2B 协议基准为 `e2b-dev/E2B@8cc9ed1e`；完整协议参考 [E2B Volume 合同](e2b-volume-contract.md)。

## 1. 目标与边界

目标：给定已有 `volume_id`、合法卷内路径和内容访问凭证，调用 SDK 直接获取文件内容。

硬性要求：

- 不调用 `Sandbox.create`，不创建临时、隐藏或辅助沙箱。
- 不启动 VM、容器、envd，也不通过沙箱命令或 `sandbox.files` 间接读文件。
- 复用已有卷管理 API 和插件，不重做创建、查询、删除卷。
- 对本期读取能力，保持 E2B 的方法入参、HTTP 路由和成功返回结构。
- 已有部署可能有仓外网关；本文的“已有/缺失”仅依据当前仓库代码，不代表全部线上环境。

“不启动沙箱”不等于“不需要存储连接”：NFS、S3 文件系统等卷可能仍需服务端建立宿主机挂载或复用已挂载路径，这是存储访问，不是沙箱启动。

本期：读取文件、列目录、查询元数据、判断存在。

不纳入本期：文件写入/删除/改权限、完整异步 SDK、模板/执行 API、沙箱挂载测试，以及 E2B 全 SDK 对齐。SDK 中已有写方法不代表本期必须实现写网关。

## 2. 哪些能力已经有了

| 能力 | 仓库现状 | 本期处理 |
|---|---|---|
| 创建卷 `POST /volumes` | 已有 CubeAPI 路由 | 复用；读取已有卷时不需要调用 |
| 卷列表 `GET /volumes` | 已有 CubeAPI 路由 | 复用 |
| 卷详情 `GET /volumes/{volumeID}` | 已有 CubeAPI 路由 | 复用，用于连接与查询卷资源 |
| 删除卷 `DELETE /volumes/{volumeID}` | 已有 CubeAPI 路由 | 保留，不因直通读取而新增 |
| Python/Node/Go 内容客户端 | 已有；会发送 `/volumecontent` 请求 | 复用，不用辅助沙箱实现 |
| 插件宿主机挂载 `Attach/Detach` | 已有，可返回 `HostPath` | 可复用，但需独立读取租约适配 |
| 内容 HTTP 读取服务 | 未找到仓内生产实现 | 本期需要补齐的主体 |
| 可验证的卷级内容凭证 | 未找到完整仓内签发/验证链路；默认 S3 插件 token 为空 | 需补齐或接入已有可信实现 |

代码依据：

- 管理路由：[CubeAPI/src/routes.rs](../CubeAPI/src/routes.rs)，`build_volume_routes`。
- 插件接口：[Cubelet/plugins/volume/interface.go](../Cubelet/plugins/volume/interface.go)，`VolumePlugin.Attach/Detach`。
- 挂载请求/返回：[Cubelet/plugins/volume/context.go](../Cubelet/plugins/volume/context.go)，`AttachRequest/AttachResult`。
- 插件调用与节点引用计数：[Cubelet/plugins/volume/manager.go](../Cubelet/plugins/volume/manager.go)。
- 现有沙箱绑定的集成层：[Cubelet/storage/pluginvolume.go](../Cubelet/storage/pluginvolume.go)。本期不走其向 VM 暴露卷的步骤。
- SDK 客户端：`sdk/python/cubesandbox/_volume_content.py`、`sdk/node/src/volume-content.ts`、`sdk/go/volume_content.go`。

注意：现有 `AttachRequest` 包含 `SandboxID`，现有节点/跨节点引用统计也按沙箱挂载设计。不能直接伪造沙箱 ID，或认为独立网关调用插件就自动具备正确的跨节点租约。

## 3. 本期对齐 E2B 的读取协议

### 3.1 鉴权与地址

- 卷管理请求：`X-API-Key`，沿用现有管理 API。
- 直通内容请求：`Authorization: Bearer <volume-token>`。
- 读取请求不自动携带管理 key 或沙箱 access token。
- 路由前缀：`/volumecontent/{volumeID}`。
- 内容 base URL 必须指向真正提供这些路由的服务。管理服务和内容服务可以同域部署，但不能因为域名相同就推定路由已实现。
- 不能把管理 API key、空插件 token 或任意非空字符串当成有效卷内容凭证。

### 3.2 HTTP 入参与返回

| 能力 | HTTP 请求 | 入参 | 成功返回 |
|---|---|---|---|
| 读取文件 | `GET /volumecontent/{volumeID}/file` | 查询 `path` | 200；`application/octet-stream`；原始字节 |
| 列目录 | `GET /volumecontent/{volumeID}/dir` | 查询 `path`、可选 `depth` | 200；JSON 元数据数组，无 `entries` 包装 |
| 查询元数据 | `GET /volumecontent/{volumeID}/path` | 查询 `path` | 200；JSON `VolumeEntryStat` |
| 判断存在 | 复用上述查询元数据接口 | 同上 | SDK 返回 bool/boolean；没有额外 `/exists` 路由 |

`path` 是卷内绝对路径，例如 `/dataset/a.bin`；不是宿主机路径。使用查询参数编码，不能直接拼接到 URL 路由。`depth` 未指定时省略；显式 `depth=0` 必须保留。

`VolumeEntryStat` 线上字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| name | string | 条目名称 |
| type | string | `unknown/file/directory/symlink` |
| path | string | 卷内路径 |
| size | int64 | 大小 |
| mode、uid、gid | uint32 | 模式、用户和组 |
| atime、mtime、ctime | date-time string | 时间 |
| target | string，可选 | 符号链接目标 |

Python 时间返回为 `datetime`，Node 返回为 `Date`；文本与字节格式是 SDK 对原始响应的转换，不改变 HTTP 文件下载协议。

### 3.3 SDK 调用与返回

| 能力 | Python | Node | 返回 |
|---|---|---|---|
| 连接已有卷 | `Volume.connect(volume_id, **opts)` | `Volume.connect(volumeId, opts?)` | Volume 句柄，不启动沙箱 |
| 文本读取 | `vol.read_file(path)` | `vol.readFile(path)` | str / Promise<string> |
| 二进制读取 | `vol.read_file(path, format="bytes")` | `vol.readFile(path, {format: "bytes"})` | bytes / Promise<Uint8Array> |
| 流式读取 | `vol.read_file(path, format="stream")` | `vol.readFile(path, {format: "stream"})` | Iterator<bytes> / Promise<ReadableStream<Uint8Array>> |
| 列目录 | `vol.list(path, depth=None)` | `vol.list(path, {depth})` | 元数据列表 |
| 查询状态 | `vol.get_info(path)` | `vol.getInfo(path)` | VolumeEntryStat |
| 判断存在 | `vol.exists(path)` | `vol.exists(path)` | bool / Promise<boolean> |

Python 类级 `Volume.get_info(volume_id)` 是已有管理 API；实例级 `vol.get_info(path)` 是内容 API。Go 提供同协议的 Go 风格内容客户端，不宣称方法签名与 Python/Node 相同。

连接参数、token、网关地址等通过 SDK 配置/逐次选项提供，凭证只从环境或安全配置注入，不硬编码。

### 3.4 错误约定

按当前对齐的 E2B 内容客户端契约：

- 内容 HTTP 404：路径不存在；`exists` 返回 false，不要求 JSON 错误码。
- 401/403：鉴权失败；不能转换为 false。
- 其他服务错误、网络错误：向调用方抛出，不返回空文件或空目录。
- 禁止认证请求自动重定向；取消、结束或提前关闭流时释放连接和读取租约。

兼容代价：裸路由 404 也会被当作路径不存在，因此不能用 `exists` 检查网关健康。部署时应单独确认路由与服务可用性；健康检查不属于 E2B 内容 API。

## 4. 最小实现链路：完全不经过沙箱

推荐新增一个逻辑上的“卷内容读取服务”，部署位置可以后续选定；本文不预设必须新增独立进程。

链路：SDK → 内容入口 → 校验卷凭证与归属 → 取得卷记录/存储来源 → 获取独立读取租约 → 插件挂载或复用宿主机路径 → 安全读取文件并返回 → 释放请求引用。

关键约束：

1. 管理层解析 `volumeID` 对应的租户、驱动和插件私有数据，不允许调用方提交任意宿主机 `HostPath`。
2. 复用插件 `Attach/Detach` 的存储能力，但建立非沙箱的租约持有者和回收机制；不调用 VM/容器创建流程。
3. 流传输期间保持租约；租约释放不必等于立刻卸载，可用有界缓存复用挂载。
4. 删除卷需要识别直通读取的活动引用，防止边读边删除/卸载。
5. 默认 S3 卷需要现有插件提供文件系统视图，或者专门适配；不能简单把对象列表当成完整 POSIX 元数据与目录语义。
6. 文件访问必须根目录约束和符号链接安全。客户端路径检查不能代替服务端防穿越，也不能只用字符串前缀检查宿主机路径。

## 5. 本期需要补齐什么

| 工作项 | 状态 | 最小要求 |
|---|---|---|
| 已有管理 API | 已有 | 不重复实现，仅确认鉴权与卷归属可复用 |
| 内容读取客户端 | 已有 | 保持 E2B 读取入参/返回，禁止辅助沙箱 |
| 三个 GET 内容路由 | 待补齐 | 文件字节、目录数组、元数据对象 |
| 内容凭证 | 待补齐或接入现有实现 | 可验证、绑定租户/卷、具有读权限和有效期 |
| 非沙箱挂载租约 | 待补齐 | 并发复用、失败回滚、重启回收、与删除协调 |
| 安全文件读取 | 待补齐 | 无宿主机路径泄露、跨卷拒绝、符号链接隔离 |
| 不启动沙箱验收 | 待补齐 | 全流程 VM/容器/沙箱创建调用数为 0 |

不新增不必要的公开 token API；若可以扩展已有 create/get 的内部签发逻辑，就保持 E2B 原有返回字段。token 的来源和验证链路要真实接通，不能只在响应里填字符串。

若你已有仓外内容 API，可直接映射其地址与协议，先做接口验收；不重复造服务。

## 6. 验收标准

本期不以沙箱挂载测试作为核心验收。测试准备一个有已知文件的卷，读取链路不能为了取得数据再创建沙箱。

- 已有卷可连接；可读取文本、含 NUL/非 UTF-8 的二进制、空文件和大文件流。
- 列表与元数据的字段、时间类型、深度和响应形状与固定 E2B 合同相符。
- 路径不存在：read 抛路径异常，exists 返回 false；无权限时二者均报鉴权错误。
- 跨卷、跨租户、过期凭证、路径穿越和逃逸符号链接访问被拒绝。
- 并发读取不重复错误挂载；流取消/提前关闭后引用正确释放；卷删除不会破坏活动读取。
- 记录/断言读取流程 `Sandbox.create`、VM 和容器创建均未触发。允许存储插件执行必要的挂载。

当前 SDK 的本地 HTTP 测试已经覆盖部分客户端协议；仓内真实读取服务、租约及安全隔离尚未实现，不能把这些本地测试当作直通链路已经可上线的证据。

端到端执行顺序、环境准备、安全专项和判定标准见
[整体验证流程](volume-direct-verification.md)。其中“未启动沙箱”必须同时具备客户端
拦截证据和服务端观测证据，缺一不可。

## 7. 结论

已有卷管理 API 保留，内容 SDK 保留。真正需要补齐的是“卷级授权 + 非沙箱存储访问 + 三个读取 GET 路由”，而不是启动沙箱作为中转，也不是继续扩展整套 E2B SDK。
