# CubeTemplateCenter

模板中心（Template Center）独立服务进程。本目录是 **第一版 MVP**：只做"独立进程跑起来"，不实现任何业务逻辑变化。

## 现状

| 项 | 状态 |
|---|---|
| 代码归属 | `CubeMaster/pkg/templatecenter/`（共享代码，TC 直接 require） |
| 独立进程 | ✅ `cmd/templatecenter/main.go` |
| HTTP server | ✅ 仅 `/health` |
| DB schema | 复用 `CubeDB/migrate`（与 CubeMaster 共享） |
| 业务端点 | ❌ 占位，待 PR 2 起接入 |
| 配置 | `conf.yaml`（端口 8090 区分于 master 8089） |

## 启动

```bash
cd CubeTemplateCenter
make build
./build/templatecenter -conf=conf.yaml
```

## 与 CubeMaster 的关系

**第一版原则**：完全不改 CubeMaster 代码，TC 仅作为"影子服务"运行。CubeMaster 继续承担所有模板相关职责，TC 启动后**只是初始化了依赖，不接收流量**。

代码层依赖：

```
CubeTemplateCenter
├── require github.com/tencentcloud/CubeSandbox/CubeMaster  (replace ../CubeMaster)
├── require github.com/tencentcloud/CubeSandbox/CubeDB      (replace ../CubeDB)
├── require github.com/tencentcloud/CubeSandbox/Cubelet     (replace ../Cubelet)
└── require github.com/tencentcloud/CubeSandbox/cubelog     (replace ../cubelog)
```

业务逻辑由 `CubeMaster/pkg/templatecenter` 直接提供，本仓库**零拷贝**——通过 `templatecenter.Init(ctx)` 复用全部能力。

## 后续演进

按 `docs/dev/templatecenter-design.md` §3.3 的 6 个 PR 顺序：

- ✅ 当前：PR 0 影子进程（不在 design 文档中，为 MVP 加的前置）
- ⏳ PR 1：Store 结构体化（CubeMaster 侧）
- ⏳ PR 2：cache 收进 Store
- ⏳ PR 3：共享文件移到 `CubeMaster/pkg/templatebase/`
- ⏳ PR 4：摘 wiring（快照钩子归 master / 模板部分归 TC）
- ⏳ PR 5：模板专属代码迁到本仓库 `pkg/templatecenter/`
- ⏳ PR 6：删除 CubeMaster 侧已迁走的代码

PR 5 之前本仓库不动 `pkg/templatecenter/`——它直接复用 CubeMaster 的包。

## 端口规划

| 服务 | 端口 |
|---|---|
| CubeMaster | 8089 |
| **CubeTemplateCenter** | **8090** |
| CubeAPI | 8088 |
| Cubelet | 9999 |

## 配置加载

通过 `-conf` 指定路径，默认 `./conf.yaml`。schema 与 CubeMaster 完全一致（共用 `pkg/base/config`）。
