# Template Center 部署形态

> 摘自 [`templatecenter-design.md`](./templatecenter-design.md) §10。本文件是该 design 文档唯一对外的部署参考。

---

## 10. 部署形态

### 10.1 template center

| 项 | 值 |
|---|---|
| 副本数 | **N**（无状态多副本，9）。基础设施不支持共享存储时退化为 1，代码不变 |
| 升级策略 | `RollingUpdate`（多副本时）/ `Recreate`（退化为单副本 + RWO 时） |
| 存储 | **对象存储或 RWX 共享存储**（多副本硬前提，9.7） |
| 探针 | `GET /health`，**必须有实际语义**（查一次数据库），ready 条件含"节点视图已加载"。原因见 9.8 的 A5：进程卡死时会话锁不会自动释放，只能靠探针触发重启 |
| Service | 普通负载均衡，**不需要 session affinity**（9.8） |
| 资源 | 按构建负载给，CPU 和临时磁盘是瓶颈。多副本下每个副本都要留够临时构建空间 |
| 外部依赖 | 数据库（含会话锁）、Redis（拉取进度）、Cubelet gRPC、CubeMaster 内部端点 |

**优雅退出**要处理：收到 SIGTERM 时正在构建的产物没法在几十秒内完成，所以策略是**不等待**，直接退出并让 7.6 的 reconcile 清理脏行。`terminationGracePeriodSeconds` 给个小值（30 秒足够），让 HTTP 层把在途请求处理完就行。

不要试图做"构建任务转移到其他副本"——那需要把构建的中间状态外部化，复杂度远超收益。构建本来就是可重试的。

### 10.2 CubeMaster（收益方）

| 项 | 改前 | 改后 |
|---|---|---|
| 副本数 | 1 | **N** |
| 升级策略 | `Recreate` | **`RollingUpdate`** |
| 存储 | RWO PVC（模板产物） | **无** |

这是本次改造最大的收益。移除 PVC 前要再扫一次确认没有别的写入方（已核实只有三处引用，都在模板代码里），旧文件保留 7 天。

### 10.3 配置项

| 组件 | 新增配置 |
|---|---|
| CubeAPI | template center 上游地址（Service 名，不是 Pod IP） |
| cubemastercli | template center 地址（**不能复用 `serverList`**，那是 CubeMaster 列表） |
| Cubelet | `MetaServerEndpoint` 指向 template center 的 Service |
| template center | 数据库连接、Redis 连接、产物存储配置、Cubelet gRPC 端口、CubeMaster 内部端点地址 |

注意 CubeAPI 和 Cubelet 都要配 **Service 名而不是 Pod IP**，多副本下才能负载均衡。

### 10.4 单机 / 一体化部署

代码独立不代表部署必须独立。单机场景下 template center 和 CubeMaster 可以同机部署，共用一块盘，只是进程分开。此时 template center 副本数为 1，`MetaServerEndpoint` 指向 localhost。

9 那套机制在单副本下不会失效，只是退化：会话锁永远抢得到、缓存不一致不存在、节点视图只有一份。**代码路径完全相同**，这是刻意的——避免单机和生产两套逻辑。
