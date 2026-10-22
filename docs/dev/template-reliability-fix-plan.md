# 模板可靠性与 ext4 直连下载：需求及修复方案

> 状态：待实施、待评审，不代表代码已完成或已发布。
> 整理日期：2026-10-21。
> 交接分支：`fix/artifact-download-s3-direct`。
> 当前基线：`74207466`，继承 `feat/template-backend-api` 的模板 backend 参数工作。
> 范围：五项修复；不新增下载模式开关，不执行提交或推送。

## 1. 需求总览与边界

| 编号 | 需求 | 完成标准 |
|---|---|---|
| R1 | 所有模板 ext4 记录稳定远端信息，Cubelet 直连 S3/MinIO 或 TC 下载 | 新链路的 ext4 字节不经过 CubeMaster；本地盘、CBS、CFS 由 TC HTTP 服务提供 |
| R2 | 下载授权由 CubeMaster 签发，TTL 默认 1h，可配置为 2h 等合法时长 | 不再使用长期 artifact token 或默认七天签名；支持过期重新授权与有界重试 |
| R3 | `cubemastercli tpl ls/list` 改为真正数据库分页 | 查询与关联聚合均有边界，CLI 默认有限页，不再全表加载后切片 |
| R4 | 修复已有 template rootfs 导致创建/redo 不可重试 | 区分完整对象、半成品、引用和并发；安全复用或新 generation 发布，不盲删 |
| R5 | 修复退役节点副本阻塞模板/快照删除及虚假 READY（#1965） | 永久退役与暂时不可达可区分；当前地址清理；部分失败可收敛；状态真实 |

### 1.1 必须区分的两个 backend

- 创建模板的 `backend=xfs|s3` 是沙箱 CoW 后端，决定运行、暂停及快照的数据存储行为。
- 本文的 artifact 存储是构建出来的 ext4 放在哪里：已有 `RootfsArtifact.StorageBackend` 使用 `s3`、`fs` 或空值（历史记录）。
- 本地盘、CBS、CFS 都可以作为 TC 的文件存储介质，统一通过 TC 下载；不要把它们重新塞进 CoW backend 枚举。
- S3/MinIO 的 artifact 存储已配置，不代表节点 CubeS3lvol 已启用；这不是同一个配置。

### 1.2 明确不做

- 不新增 `proxy/direct/auto`、`CUBE_ARTIFACT_DOWNLOAD_REDIRECT` 或 `redirect=1` 之类行为开关。
- 不在直连失败后静默回落为 Master 中转，也不把 S3 上传失败伪装为本地存储成功。
- 不向 Cubelet 下发 S3 AK/SK、Master↔TC 构建/删除共享密钥。
- 不要求 Cubelet 挂载 CBS/CFS，也不直接暴露 TC 文件系统路径。
- 不通过改双 `tpl-` 前缀、全量删除同名对象、手工删 DB 行来替代幂等和引用一致性修复。
- 不新增独立 prune 命令或 force 开关来绕过正常删除流程。
- `BUILDING_EXT4` 长步骤心跳问题是关联风险，不属于本次五项需求，不能宣称已被直连下载解决。

## 2. 已核实的现状与交接风险

### 2.1 下载与模型

- 当前下载入口在 `CubeMaster/pkg/service/httpservice/cube/template_from_image.go`。
- 本地文件分支由 Master 开文件后 `http.ServeContent` 返回；不是 Master 从 TC 拉流再转发。现有部署依赖 Master/TC 共享 artifact 目录。
- 对象存储分支仍可能由 Master 代理 HTTP 流或调用 blobstore 读取后返回，造成控制面承担大文件传输。
- `CubeMaster/pkg/base/db/models/template_image.go` 的 `RootfsArtifact` 已有 `StorageBackend`、`ObjectKey`、`ArtifactURL`、`DownloadToken`、大小和 SHA256 字段。应增量扩展，不能重复新增同义模型。
- `ArtifactURL` 历史上保存构建时的 presigned URL；`artifact_url_refresh.go` 提供签名刷新及 blobstore 访问。
- `Cubelet/internal/cube/server/images/ext4image/utils.go` 已发送 `X-Cube-Artifact-Token`，但 `rewriteDownloadHost` 当前会改写非 S3 签名 URL 的 host；TC URL 若沿用该逻辑，会被错误改回 Master。

### 2.2 列表并非完全没有分页

- CLI `TemplateListCommand` 已支持 `list`/`ls`、`--limit`、`--offset`，但默认 `limit=0` 表示全量。
- HTTP 已接受分页参数；`ListTemplatesWithOptions` 实际先读全量缓存或 DB，再执行 `sliceTemplateInfos`。
- 冷缓存的 `listTemplatesFromDB` 全量读取 definitions、CREATE/REDO 历史 jobs、隐藏快照关系，合并后再缓存。
- 返回集合还包括没有 definition 的 job-only 模板。只给 definition 查询加 Limit 会漏掉这些条目，并导致 total、排序不一致。
- HTTP 副本来源查询已有批量查询实现，不应未经证据将问题归因于 N+1 或全量 Preload。

### 2.3 rootfs 与删除

- `cowTemplateRootfsName` 使用 `tpl-%s-rootfs`，template ID 自带 `tpl-` 时形成双前缀；这不等于冲突根因。
- `CommitTemplateRootfs` 对已存在 snapshot 主动拒绝，已有测试断言不能覆盖删除旧对象；需保留其保护有效数据的意图。
- 当前模板/artifact 清理已有三阶段与 `CLEANUP_PENDING`，不是从零建立 GC。
- 已读取路径仍有优先使用历史 NodeIP 的逻辑；不能假定已实现退役节点过滤和当前地址优先。

### 2.4 分支上有未完成且不符合最终要求的改动

核实工作区有两个未提交修改文件：

- `CubeMaster/pkg/service/httpservice/cube/template_from_image.go`。
- `CubeMaster/pkg/templatecenter/artifact_url_refresh.go`。

草稿新增 S3 重定向、`CUBE_ARTIFACT_DOWNLOAD_REDIRECT`、`redirect=1` 和代理回落。它既未覆盖 TC 直连，也未实现短 TTL 与重新授权。实施者应有针对性地替换这些草稿，不做整仓 reset，不丢弃其他用户改动；保留原分支的 backend API 工作。

## 3. R1：ext4 数据流与控制流分离

### 3.1 目标链路

| 存储场景 | 目标数据路径 | 部署及定位要求 |
|---|---|---|
| TC 本地盘 | `Cubelet → 持有 artifact 的 TC HTTP 下载接口` | 记录实例/存储身份；路由到确实持有文件的 TC |
| TC 使用 CBS | `Cubelet → 挂载 CBS 的 TC HTTP 下载接口` | 不多节点直接挂载普通 ext4；重挂载后更新 TC 服务位置 |
| TC 使用 CFS/NFS | `Cubelet → 可读取该共享存储的 TC HTTP 下载接口` | 相同 storage_ref 的健康 TC 可提供读取，无需 Cubelet 挂载共享盘 |
| S3/MinIO | `Cubelet → 对象存储签名 URL` | endpoint 对节点可达，TLS 证书可信；不能改写已签名 host |

CubeMaster 只负责节点鉴权、artifact 状态/权限校验、定位解析、临时授权及分发编排。TC HTTP 直连的收益是剥离 Master 的长连接和字节搬运，不是承诺减少同机场景网络跳数或提高磁盘上限。

TC 构建与下载仍可能竞争 CPU/磁盘/网卡；需下载并发限额、流式读取和资源隔离。CFS 可以按共享存储扩展下载实例，本地盘/CBS 不能随意加无数据副本后负载均衡。

### 3.2 稳定定位数据

推荐语义如下；字段名为建议，落地时结合已有模型和迁移规范确定：

| 字段/实体 | 含义 |
|---|---|
| `storage_backend` | 复用已有 artifact 类型 `s3`/`fs`；历史空值做明确兼容解析 |
| `storage_ref` | 存储实例或配置标识，解决多存储、配置变化和跨实例定位；不是凭证 |
| `object_key` | 复用已有稳定对象 key；TC 文件也使用受控相对 key |
| `bucket` / endpoint 引用 | S3 对象所在桶与节点可达 endpoint；关联稳定存储描述，不只依赖进程当前全局配置 |
| `owner_tc_id` | 本地盘/CBS artifact 的持有者身份；共享文件存储按 storage_ref 解析可读 TC |
| TC 实例注册记录 | 实例 ID、节点可达下载地址、storage_ref、健康/租约状态；地址可更新 |
| 大小、SHA256、generation | 产物完整性及不可变版本；复用已有完整性字段 |

DB 记录实际已发布的远端事实。临时 URL、token、expires_at 是签发结果，不是稳定定位来源；不把 AK/SK 或私钥写进 artifact 行。

已有 `Ext4Path` 可继续作为 TC 内部文件定位或历史兼容字段，但不得直接作为 Cubelet 下载地址。不要继续用 `MasterNodeIP` 冒充 TC 持有者。

### 3.3 构建发布与位置变化

1. TC 在临时目录构建、校验大小/SHA256，产物以不可变 generation 发布。
2. 文件存储采用同文件系统原子发布；S3 上传成功并确认对象可用后发布定位元数据。
3. TC 上报 READY/BUILT 的同时提交 storage_ref、object key、持有者及完整性信息；不可先把未发布产物标 READY。
4. TC 重启/CBS 重挂载后，健康注册更新地址，Master 从存储关系解析当前位置，不重复修改 artifact 的不可变内容身份。
5. 本地盘持有者永久丢失且无副本时，明确产物不可用并重建；不能随机换一个无文件 TC。
6. 选定远端存储的上传/发布失败时，明确失败，不静默转换另一种介质。

### 3.4 Master 下载授权接口（建议协议，尚未实现）

建议使用已鉴权的内部 `POST /internal/template/artifacts/:artifact_id/download-ticket`，或等价的现有节点 RPC。最终接入点需核实并复用当前节点身份体系，不能用知道 artifact_id 就能匿名取票的接口。

请求：artifact ID、预期 generation、下载用途；node_id 必须来自可信调用身份，不相信 body 自报身份。

响应：

| 内容 | 说明 |
|---|---|
| artifact ID / generation | 下载期间绑定同一份不可变产物 |
| source type | `tc` 或 `s3`，用于明确决定目标请求的鉴权方式 |
| URL | TC 只读接口地址或 S3 presigned GET URL |
| TC ticket | 仅 TC 下载时提供；S3 使用 URL 自身签名 |
| expires_at / server_time | 实际有效期和签发时间 |
| SHA256 / size | 端到端完整性与长度约束 |

创建/redo 分发优先由 Master 下发以上结构；过期时 Cubelet 用节点身份重新申请。普通模板列表/详情不能顺带暴露 ticket、签名 URL 或 DownloadToken。

### 3.5 TC 只读接口

建议 `GET/HEAD /tc/api/v1/artifacts/:artifact_id/download`：

- ticket 校验失败返回 401/403，不采用 HTTP 200 包裹"下载失败"造成 Cubelet 写入错误文件。
- 只访问 READY 的指定 generation；拒绝已撤销或待清理产物。
- 支持 Range、If-Range、206/416、HEAD；正确返回长度与强 ETag，完整性 SHA256 与 ETag 语义清晰。
- artifact ID/key 从可信元数据解析；禁止任意路径、目录遍历、符号链接逃逸。
- 不开放目录列表、写入、构建、上传或删除权限；内部控制 API 仍使用独立鉴权。
- 下载入口需 TLS/可信内网入口、网络白名单；只开放下载路由，不因下载把全部内部 API 暴露给计算节点。
- 流式发送、不读完整文件入内存；连接头/空闲超时、并发与带宽资源有边界，避免健康大文件被短总超时截断。
- 同机 one-click 可使用部署生成的可达地址；Kubernetes 可用 Service/内网 LB，但 ClusterIP 是否对 Cubelet 网络可达必须实际验证，不能直接当作默认事实。

### 3.6 Cubelet 改造

- 新协议按 source type 直接请求 TC/S3，不能对所有非 SigV4 URL 统一改写 host。
- 新 TC URL 从授权结果使用原始地址；历史 Master URL 的 host rewrite 仅适用于识别出的旧下载协议/路径。
- TC 请求带 `X-Cube-Artifact-Token`；S3 请求不带 TC ticket、Master 授权 header。
- 优先显式取票后直接 GET，而非依赖跨 host 自动重定向；历史入口兼容时必须控制 header 转发，防止凭证泄漏。
- 写临时文件，校验大小/SHA256 后原子替换；并发下载复用节点内去重机制。
- 弱网重试有指数退避、最大次数和整体任务预算；不能无限循环。
- 续传校验 generation/ETag 与 Content-Range。远端忽略 Range 返回 200 时重新开始，不把完整响应追加到半文件；generation 改变不得拼接旧数据。
- S3 GET 签名不能拿去 HEAD。以授权元数据提供大小/SHA256，必要时 Range GET 探测，或扩展独立 HEAD 签名能力；不得直接重定向 HEAD 到 GET 签名。

## 4. R2：短期下载授权

### 4.1 TTL 配置

建议统一配置名 `CUBE_ARTIFACT_DOWNLOAD_TOKEN_TTL`，默认 `1h`，支持 `2h` 及满足后端限制的合法正 duration。此名称为新方案建议，并非现有配置。

- Master 签发时统一读取和验证；非法、零或负值启动/配置加载时报错，不能静默永久有效。
- S3 签名传入相同 TTL，但临时 STS 凭证可能更早到期；返回实际可保证的 expires_at，超过协议/凭证限制明确处理，不能谎报有效期。
- TC 校验票内 expires_at，不根据自身默认 TTL 再延长；容忍小范围时钟偏差，节点/服务端保持时钟同步。
- 数据定位与凭证寿命分离；不周期性给 DB 全表重写 token，不继续签默认七天下载授权。

### 4.2 TC ticket 设计

推荐复用现有可用签名设施；若无合适实现，采用标准库/成熟库的非对称签名票据（例如 Ed25519），Master 持有私钥，TC 只持有验签公钥。不要自创加密算法或把构建共享密钥复用为节点下载权限。

必要 claims：artifact_id、generation、storage_ref、audience、允许 GET/HEAD、iat、exp、kid；jti 可用于审计/撤销。若绑定 node_id，要以 mTLS/可信入口等校验真实节点身份，仅写 claim 不能证明请求者就是该节点。

- key 由部署 Secret 管理；多副本 Master 使用一致签发体系，TC 公钥分发可信。
- 轮换期间保留旧公钥至其票据 TTL 到期；私钥/票据不能出现在日志或普通 API 响应。
- ticket 是短期 bearer 授权，可在有效期内用于重试/Range，不称为"一次使用即失效"。
- 取票时验证节点是否仍被允许分发该 artifact；TC 每次读请求验证产物生命周期，删除/撤销能阻止后续新读取。
- 健康传输开始时校验到期时间，不因流式下载跨过 exp 主动截断；重连/续传需要有效新票。
- 活跃下载与物理 GC 之间要有安全保护，避免边下载边删造成平台差异或失败。

### 4.3 重新授权与错误分类

| 错误 | 行为 |
|---|---|
| TC 明确 ticket 过期 / S3 明确签名过期 | 向 Master 重新取票，在整体预算内续传 |
| 未授权、票据篡改、节点已退役 | 终止；不能靠不停取票绕过权限 |
| 普通 S3 403 | 区分时钟偏差、签名/region/凭证错误；不能一律当过期 |
| 网络超时、连接断开、可重试 5xx | 同一 generation 下有界重试 |
| 404、产物已撤销、generation 冲突 | 明确报错；由控制面确认缺失/重建，不静默切换来源 |
| 长度或 SHA256 不匹配 | 丢弃不可信半成品并报完整性错误，重试有上限 |

日志和 trace 仅记录 artifact/node ID、generation、source、耗时、重试数及错误类别；TC header 和 S3 query 签名都脱敏。标准 presigned URL 仍是敏感 bearer 凭证，不是"query 符合规范就不存在泄漏风险"。

## 5. R3：模板列表数据库分页

### 5.1 修复核心

不是再加一遍 `--limit/--offset`，而是把 `ListTemplatesWithOptions → 全量缓存/全量 DB → 内存切片` 换成有限查询，并保留历史列表的资源语义。

### 5.2 查询设计

1. 先定义统一可见模板集合：definition 为主，加入尚无 definition 的 CREATE/REDO job-only 模板；每个 template_id 只出现一次。
2. 删除/删除中的隐藏快照、pause 内部产物、权限/租户过滤在分页和 count 之前完成，不能分页后在 Go 中过滤导致短页/错误 total。
3. 使用统一排序字段及 template_id 唯一 tie-breaker。可优先保留 `updated_at DESC, template_id ASC`，但 definition/job-only 必须用同一规则。
4. DB 对集合做 Limit/Offset；通过兼容 MySQL/PostgreSQL 的 UNION/子查询/窗口或等价方案实现，按支持的数据库版本选择，不硬编码单一方言。
5. 只按本页 template IDs 聚合关联数据。最新 CREATE/REDO job 在 DB 按 attempt_no DESC、id DESC 选一条，不能读取每个模板全部历史 jobs 后再挑。
6. 精简 Select，仅读取显示必需字段；若 image_info 需从 JSON 提取，限制为本页，不加载全表大 ResultJSON/RequestJSON。
7. 复用已有批量副本查询并应用 R5 的同一可用性投影；节点注册表不可用时明确状态未知，不伪装所有节点退役。
8. total 为同一可见集合的 COUNT，不靠 len(全量列表)。为关联与排序条件补索引，并用 EXPLAIN 验证。
9. 移除列表请求对全量聚合缓存的依赖；如需要缓存，按查询/页粒度限容，并检查后台刷新是否仍周期性全表加载。

### 5.3 CLI / API 约定

- `tpl ls` 与 `tpl list` 共用逻辑；建议默认 `--limit 50 --offset 0`，最大页大小 200，最终常量与现有 API 约定统一。
- API 缺省及 limit=0 也必须有限返回；CLI 对负数、非法值不能悄悄省略参数退化为全量。
- 保留已有 data/total 响应字段，新增 limit、offset、has_more 等信息；打印翻页提示。
- 如提供 `--all`，它是显式 CLI 便利功能，不是下载模式开关。按页读取并立即输出；JSON 使用流式序列化或明确 NDJSON 格式，不能累积全部响应。
- 默认由全量变分页是可见行为变化。更新 CubeAPI/SDK/内部调用方，避免旧消费者误把第一页当全集；不能以兼容为理由保留无上限全量入口。
- 稳定排序保证静态数据翻页一致；并发更新时 Offset 分页本身可能漂移，不能承诺跨请求强一致。需要无重复遍历时，配套 keyset cursor/固定读取快照，不能只宣称 ORDER BY 即可。
