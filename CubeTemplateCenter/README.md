# CubeTemplateCenter

模板中心（Template Center）独立服务进程。本目录是 **第一版 MVP**：让 TC 作为独立进程跑起来，业务逻辑通过复用 `CubeMaster/pkg/templatecenter` 提供。

## 现状

| 项 | 状态 |
|---|---|
| 代码归属 | `CubeMaster/pkg/templatecenter/`（共享代码，TC 直接 require） |
| 独立进程 | ✅ `cmd/templatecenter/main.go` |
| HTTP server | ✅ gin + 中间件 + 超时 + graceful shutdown |
| 模板路由 | ✅ 13 个端点（见下表） |
| `/health` 探针 | ✅ ready = nodemeta.Ready() && templatecenter.IsReady() |
| `/metrics` | ✅ Prometheus |
| Init 拆分 | ✅ `InitForTemplateCenter`（不注册快照钩子 / 不跑 snapshot reconciler） |
| DB schema | ✅ 复用 `CubeDB/migrate`（与 CubeMaster 共享 schema 所有权） |
| 构建锁 | ⚠️ 当前依赖 CubeMaster `artifact_build.go:56` 的进程内 `sync.Mutex`（**单副本够用**，多副本要 DB 会话锁——`pkg/lock/` 已备好但未接入） |
| 业务端点 | ✅ 全部走 CubeMaster 现有 handler |

## 启动

```bash
cd CubeTemplateCenter
make build
./build/templatecenter -conf=conf.yaml
```

默认监听 `:8090`（区别于 CubeMaster `:8089`）。

## API 端点（13 个）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/cube/template` | 创建模板（用户驱动） |
| GET | `/cube/template` | 查询模板 |
| DELETE | `/cube/template` | 删除模板 |
| GET | `/cube/template/compat` | 查询兼容矩阵 |
| POST | `/cube/template/compat` | 更新兼容矩阵 |
| POST | `/cube/template/redo` | 重做模板（fingerprint 不变时强制重建） |
| GET | `/cube/template/build/:build_id/status` | 查构建进度 |
| GET | `/cube/template/from-image` | 查 from-image 模板 |
| POST | `/cube/template/from-image` | 从镜像创建模板（核心入口） |
| GET | `/cube/template/artifact/download` | Cubelet 拉 ext4（数据面） |
| HEAD | `/cube/template/artifact/download` | 同上（探大小） |
| GET | `/cube/ca/:filename` | 拉 CA 证书 |
| HEAD | `/cube/ca/:filename` | 同上 |
| GET | `/cube/rootfs-artifact` | 查 rootfs artifact 状态 |

内部端点：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 探针（ready = nodemeta + templatecenter store 初始化） |
| GET | `/metrics` | Prometheus 指标 |

## 与 CubeMaster 的关系

**原则**：完全不改 CubeMaster 业务代码，TC 仅作为"影子服务"运行。

代码层依赖：

```
CubeTemplateCenter
├── require github.com/tencentcloud/CubeSandbox/CubeMaster  (replace ../CubeMaster)
├── require github.com/tencentcloud/CubeSandbox/CubeDB      (replace ../CubeDB)
├── require github.com/tencentcloud/CubeSandbox/Cubelet     (replace ../Cubelet)
└── require github.com/tencentcloud/CubeSandbox/cubelog     (replace ../cubelog)
```

业务逻辑由 `CubeMaster/pkg/templatecenter` 直接提供：

- `templatecenter.InitForTemplateCenter(ctx)` — 模板侧启动（不注册快照钩子）
- `templatecenter.SubmitTemplateFromImageWithEnvdPayload` — POST from-image 入口
- `templatecenter.GetTemplateImageJobInfo` — 进度查询
- `templatecenter.OpenRootfsArtifact` — Cubelet 数据面下载

**共享 CubeMaster 的两处小改动**：

1. `CubeMaster/pkg/service/httpservice/cube/routes.go` 新增 `RegisterTemplateRoutes(g)` 导出函数（**新增，不修改** `RegisterCubeRoutes`，向后兼容）
2. `CubeMaster/pkg/templatecenter/store.go` 新增 `IsReady()` 导出 + `InitForTemplateCenter`（原 `Init` 保留，向后兼容）

## 已就位的辅助设施

### `pkg/lock/`（DB 会话锁）

跨副本分布式锁（MySQL `GET_LOCK` / PG `pg_advisory_lock`），复制自 `CubeMaster/pkg/templatecenter/artifact_gc.go:38-124`。**未接入业务代码**——PR 5 迁 `runTemplateImageJob` 时统一接入。

样例（已写好但未被调用）：
- `WithBuildLock(ctx, db, artifactID, fn)` — 构建互斥（替换 `artifact_build.go:56` 的 `sync.Map`）
- `WithReconcileLock(ctx, db, fn)` — 全局 reconcile 互斥

### `pkg/httpservice/server.go`

沿用 CubeMaster 的 server 结构（`internalHttp` + gin + timeouts + config watcher + graceful shutdown），只注册模板路由。

## 后续演进

按 `docs/dev/templatecenter-design.md` §3.3 的 6 个 PR 顺序：

- ✅ 当前：PR 0 影子进程 + Init 拆分 + /health
- ⏳ PR 1：Store 结构体化（CubeMaster 侧）
- ⏳ PR 2：cache 收进 Store
- ⏳ PR 3：共享文件移到 `CubeMaster/pkg/templatebase/`
- ⏳ PR 4：摘 wiring（`SubmitTemplateFromImageWithEnvdPayload` 入口加 DB 会话锁；reconciler 兜底）
- ⏳ PR 5：模板专属代码迁到本仓库 `pkg/templatecenter/`
- ⏳ PR 6：删除 CubeMaster 侧已迁走的代码

## 端口规划

| 服务 | 端口 |
|---|---|
| CubeMaster | 8089 |
| **CubeTemplateCenter** | **8090** |
| CubeAPI | 8088 |
| Cubelet | 9999 |

## 部署要点

**持久化**：ext4 产物必须落 PVC（参照 `deploy/kubernetes/chart/templates/master-pvc.yaml`）。
**loop 设备**：mkfs ext4 需要 `/dev/loop0-31`（宿主机 loop 模块），容器内要么挂 `/dev` 要么预创建。
**配置加载**：`-conf` 指定路径，schema 与 CubeMaster 完全一致（共用 `pkg/base/config`）。

## 测试

```bash
make test          # 编译期检查 + 单元测试（无业务测试时仅验证编译）
make test-integration
```
