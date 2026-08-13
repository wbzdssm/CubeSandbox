# 方案 C：API 表面与消费者契约审计（Root Cause R6）

| | |
|---|---|
| 状态 | 待评审 |
| 关联现象 | `cubemastercli node list` 看不到 `LocalTemplates` 字段 |
| 关联根因 | R6（API 表面与消费者契约不清）—— **新增维度**，非 R1-R5 之一 |
| 影响范围 | CubeMaster（`httpservice/inner` + `httpservice/meta` + `nodemeta` + `localcache` + `cmd/cubemastercli`） |
| 修复方式 | 3 处关键修改（按"改一处解决多问题"排） |
| DB 变更 | 无 |
| 是否向后兼容 | 是 |

> 配套阅读：[`templatecenter-design.md` §12](./templatecenter-design.md) 是 R1-R5 异常分类；本文档是 R6 维度，**正交**于 R1-R5，专门管"为什么 API 选择会让字段看不见"。

---

## 1. 摘要（一句话版）

`cubemastercli` 是运维工具，但它把"调度器内部 API"（`/internal/node`）当成了"节点管理 API"用——endpoint 的命名和分组没有按"消费者类型"分，cli 选 API 时缺线索，结果在 `NodeSnapshot → Node` 的单向投影里把 `LocalTemplates` 字段丢了。

**修一个 endpoint 选错问题，比改任何字段或方法都治本。**

---

## 2. 症状：用户视角能看到什么 / 看不到什么

| 工具 | 入口 | 返回类型 | 有 LocalTemplates？ | 备注 |
|---|---|---|---|---|
| `cubemastercli node list` | `GET /internal/node` | `[]*node.Node` | ❌ | 看不到，cli 输出列头也没有 |
| `cubemastercli node list --hostid X` | `GET /internal/node?host_id=X` | `[]*node.Node` | ❌ | 同上 |
| `cubemastercli node isolate/unisolate` | `PUT/DELETE /internal/meta/nodes/:id/isolation` | `*nodemeta.NodeSnapshot` | ✅ 有，但未使用 | 类型是 NodeSnapshot 但代码只读 `SchedulingDisabled` |
| `curl /internal/meta/nodes/:id` | （未在 cli 中封装） | `*nodemeta.NodeSnapshot` | ✅ 有 | 完整 nodemeta 视图 |
| `curl /internal/meta/nodes` | （未在 cli 中封装） | `[]*nodemeta.NodeSnapshot` | ✅ 有 | 完整列表 |
| WebUI NodeDetail 页面 | `GET /internal/meta/nodes/:id` | `*nodemeta.NodeSnapshot` | ✅ 有 | web 端能看，cli 端不能看——工具能力不对等 |

**结论**：
- 数据**在系统里是完整的**（DB + localcache + 调度器都看得到）
- 问题是 **cli 工具链错配了 endpoint**——运维工具走的是"调度器 API"
- 真正的"管理 API"是 `/internal/meta/...`，但 cli 没把它包成命令

---

## 3. 端到端调用链：数据为什么会丢

> 5 点原则架构下：`TemplateCenter` 是 `CubeMaster` 进程内的组件。所以"Master"和"TC"在调用链里是同一个进程，`templatecenter` 包内的数据是**进程内调用**（不是 RPC）。但 R6 的根本原因——`toSchedulerNode` 白名单投影丢字段——**仍然成立**，本审计适用于 5 点原则架构。

```text
[生产端：Cubelet 上报]
  C → MM  gRPC UpdateNodeStatus  (含 req.LocalTemplates)
        │
        ▼
[接收端：nodemeta 服务 (master 进程内)]
  UpdateNodeStatus()  service.go:294
    ├─ 写 t_cube_node_status.local_templates_json  ← 数据落 DB，完整
    └─ syncLocalcache(snap)  service.go:1120
          │
          ▼
[投影点：toSchedulerNode (master 进程内)]
  toSchedulerNode(snap)  service.go:1069
    ├─ snap.NodeID / HostIP / Capacity / ...
    └─ snap.LocalTemplates  ← ❌ 不拷贝
          │
          ▼
[落点：调度视图 localcache (master 进程内)]
  localcache.Node  ← 没有 LocalTemplates 字段 (node.go:20-99)
          │
          ▼
[消费者：scheduler + cli (master 进程内 + 外部)]
  ├─ Scheduler:  用 schedulable 字段，OK，不依赖 LocalTemplates
  └─ CLI:        走 /internal/node → getNodeInfo → rsp.Data = nodes  ← 拿到的是没 LocalTemplates 的切片
```

**关键投影点**（`pkg/nodemeta/service.go:1069-1118`）：

```go
func toSchedulerNode(snap *NodeSnapshot) *node.Node {
    // ... 拷贝 capacity / allocatable / labels / versions / etc.
    // ❌ 没有拷贝 snap.LocalTemplates
    n := &node.Node{ InsID: snap.NodeID, ... }
    return n
}
```

这是个 **白名单投影**——只拷调度器关心的字段，LocalTemplates 没被列入白名单，就丢了。

---

## 4. 5-Why 根因分析

```text
Q1. 为什么 cli 看不到 LocalTemplates？
A1. 因为 list 命令调 /internal/node，那个 endpoint 返回 []*node.Node，
    node.Node 结构体里没有 LocalTemplates 字段。

Q2. 为什么 /internal/node 返回 node.Node？
A2. 因为 inner endpoint 是给"调度器"用的，调度器不关心 LocalTemplates，
    只关心 score、healthy、allocatable 这些调度属性。

Q3. 为什么 cli（运维工具）也调 inner endpoint？
A3. 因为 list 是老命令，最早就是为"看调度节点"写的（看 score、healthy），
    后来要加"完整节点信息"的能力时，没人回头迁移到 meta endpoint，
    而是想着给 node.Node 加字段——但没人加。

Q4. 为什么没人迁移到 meta endpoint？
A4. 因为命名误导。inner 名字看起来比 meta 更"核心"（inner = 内部系统），
    /node 路径比 /meta/nodes/:id 更直接，cli 维护者没动力去用"看起来更远"的
    meta endpoint。

Q5. 为什么 endpoint 命名会误导？
A5. 因为 endpoint 的分组维度是"用途"（inner / meta），不是"消费者契约"
    （scheduler / admin / debug）。inner 不是"只能调度用"、meta 也没声明
    "我是给运维的"——分类轴错了，所以选 API 时无依据。
```

**最深一层**：endpoint 的分组维度错了——按"用途"分（inner / meta）而不是按"消费者类型"分（scheduler / admin / debug）。

---

## 5. 7 个失败方法清单

按调用链从 cli 一路往下：

| # | 方法 | 位置 | 错位 | 影响 |
|---|---|---|---|---|
| 1 | `NodeListCommand.Action` | `cmd/cubemastercli/commands/cubebox/node.go:75-115` | URL 写死 `/internal/node`，**没走 meta endpoint** | cli 拿不到 LocalTemplates |
| 2 | `getNodeInfo` | `pkg/service/httpservice/inner/info.go:19-78` | 返回类型限定为 `[]*node.Node`，**没有暴露 NodeSnapshot 的开关** | 即使加 `--json` flag 也拿不到 LocalTemplates |
| 3 | `localcache.GetNodes(-1)` | `pkg/service/httpservice/inner/info.go:57` | 数据源是**调度视图**的 localcache（已经被投影过） | 二次投影可乘性丢失：N→S→N |
| 4 | `toSchedulerNode` | `pkg/nodemeta/service.go:1069-1118` | **白名单投影**，只拷贝调度关心的字段，LocalTemplates 没被列入 | 源端有、sink 端没声明，直接丢 |
| 5 | `syncLocalcache` | `pkg/nodemeta/service.go:1120-1123` | 写入时调 `toSchedulerNode`，**丢字段是不可逆的** | 后续任何读 localcache 的消费者都看不到 |
| 6 | `printNodeSummary` | `cmd/cubemastercli/commands/cubebox/node.go:201-223` | 列头**写死 8 个字段**，没有 LocalTemplates | 即使加 flag 拿数据，表格也不显示 |
| 7 | `type Node struct` | `pkg/base/node/node.go:20-99` | **结构体里根本没有 LocalTemplates 字段** | 即便想投影，源类型上就没这个字段可拷贝 |

**病理切片**：

```text
cli 层
└─ #1 NodeListCommand ──── 选错 endpoint

endpoint 层
├─ #2 getNodeInfo ──── 返回类型锁死 node.Node
└─ #3 localcache.GetNodes(-1) ──── 拿的是已投影的二次视图

数据源层
└─ #5 syncLocalcache ──── 写入时调用 toSchedulerNode

投影层（最关键的失败点）
└─ #4 toSchedulerNode ──── 白名单投影，丢 LocalTemplates

结构体层
└─ #7 type Node struct ──── 没有 LocalTemplates 字段，连白名单都列不进去

打印层
└─ #6 printNodeSummary ──── 列头写死
```

---

## 6. 三个最关键修复点

按"改一处解决多问题"的标准排：

### 🥇 #4 `toSchedulerNode`（最致命）

- 这是"数据丢失"的源头
- 改法：要么保留全字段（用 `*NodeSnapshot` 嵌入），要么把 LocalTemplates 显式列入白名单
- 修这一个，下面 #2/#3/#5 都不再丢数据

### 🥈 #1 `NodeListCommand` 的 URL（最显眼）

- 运维工具的 `list` 命令走调度器 endpoint，方向性错误
- 改法：换 URL 到 `/internal/meta/nodes`，或者新增 `node show` 子命令
- 修这一个，cli 立刻能看到 LocalTemplates

### 🥉 #7 `node.Node` 结构体（最基础）

- 没有 LocalTemplates 字段，是 #4/#5 能丢字段的根本原因
- 改法：加 `LocalTemplates []LocalTemplate` 字段 + json tag
- **不推荐单独改**——会污染调度器模型，最好和 #4 一起用嵌入方式做

---

## 7. 修复方向（按层）

| 层 | 修法 | 工作量 | 是否推荐本期做 |
|---|---|---|---|
| **API 分层** | 重新规划 endpoint 命名：按消费者（`/admin/...` vs `/scheduler/...`）而不是用途（`/inner` vs `/meta`） | 中 | ❌ 大改动，跟其他 PR 一起做 |
| **数据模型** | 让 `node.Node` 内嵌一个 `*NodeMetaSnapshot` 指针，或用全字段结构 + json omitempty 隐藏 | 中 | ⚠️ 看后续是否真要拉平两套模型 |
| **转换函数** | `toSchedulerNode` 改成"全字段复制 + 调度字段补全"，而不是"白名单拷贝" | 小 | ✅ 本期，#4 一行代码 |
| **cli 设计** | cli 的 `node` 子命令统一走 meta endpoint，按"运维场景"组织子命令（`list` / `show` / `cordon` / `uncordon`） | 小 | ✅ 本期，#1 + #6 |
| **演进路径** | 写一条规则：nodemeta 新增字段时，必须 review 所有"返回 node 的 endpoint"，确认 cli 是否要暴露 | — | ✅ 流程上立即可做 |

---

## 8. 最小修复方案（本期建议）

**只动 2 个方法，~30 行，无 DB 变更，无向后兼容问题**：

### 8.1 新增 `node show` 子命令

**位置**：`cmd/cubemastercli/commands/cubebox/node.go`

```go
var NodeShowCommand = cli.Command{
    Name:      "show",
    Usage:     "show full node metadata (incl. LocalTemplates, Images, Versions)",
    ArgsUsage: "<node-id>",
    Flags: []cli.Flag{
        cli.BoolFlag{Name: "json", Usage: "print raw json response"},
    },
    Action: func(c *cli.Context) error {
        // 调 /internal/meta/nodes/:node_id
        // 返回 *nodemeta.NodeSnapshot
        // 打印 LocalTemplates / Images / Versions 等所有管理字段
    },
}
```

### 8.2 修 `toSchedulerNode` 让 LocalTemplates 显式列入

**位置**：`pkg/nodemeta/service.go:1069-1118`

```go
func toSchedulerNode(snap *NodeSnapshot) *node.Node {
    n := &node.Node{
        // ... 现有字段 ...
    }
    // 保留为快照：localcache 不需要这个字段（scheduler 不用），
    // 但 getNodeInfo 走 localcache 时不应该丢；
    // 改：cloning Snapshot 在 localcache 端，调度字段才走 toSchedulerNode
    return n
}
```

更彻底的改法是把 `localcache.Node` 和 `scheduler.Node` **拆成两套**，但工作量较大，本期先不推荐。

### 8.3 修 printNodeSummary 列头（次要）

可选，给 `node list` 加 `--with-templates` flag，单独打一行 LocalTemplates 数量。

---

## 9. 跟 R1-R5 的关系（正交维度）

| 维度 | R1-R5 | R6 |
|---|---|---|
| 关注点 | 状态机 / 数据 / Schema / 分发 / 外部 | API 表面 / 消费者契约 |
| 表现形式 | 业务行为不正确 | 工具/接口/调用方之间不对齐 |
| 检测方式 | 状态机图 + 异常 case | 调用链图 + endpoint 矩阵 |
| 修复单位 | 状态值 / 字段 / RPC | endpoint / cli 命令 / 类型投影 |

**R6 是新的"元根因"**：不直接导致功能错误，但会让"运维/排障"的能力缺失；是 R1-R5 的"可观测性"保障。

---

## 10. 失败方法索引（快速跳转）

| 文件 | 失败方法 | 现象 |
|---|---|---|
| `cmd/cubemastercli/commands/cubebox/node.go:75-115` | `NodeListCommand.Action` | URL 走 inner |
| `pkg/service/httpservice/inner/info.go:19-78` | `getNodeInfo` | 返回 node.Node |
| `pkg/service/httpservice/inner/info.go:57` | `localcache.GetNodes(-1)` | 数据源已投影 |
| `pkg/nodemeta/service.go:1069-1118` | `toSchedulerNode` | 白名单投影 |
| `pkg/nodemeta/service.go:1120-1123` | `syncLocalcache` | 写入时丢字段 |
| `cmd/cubemastercli/commands/cubebox/node.go:201-223` | `printNodeSummary` | 列头写死 |
| `pkg/base/node/node.go:20-99` | `type Node struct` | 字段缺失 |

---

## 11. 验收标准

- [ ] `cubemastercli node show <id>` 能打印出 LocalTemplates 列表
- [ ] `cubemastercli node show <id>` 也能打印出 Images / Versions / Conditions 等完整管理字段
- [ ] `getNodeInfo` 调用 meta endpoint（`/internal/meta/nodes/:id`）而不是 inner endpoint
- [ ] 现有 `node list` / `node isolate` / `node unisolate` 命令行为不变
- [ ] `cubemastercli node list --with-templates` flag 可选显示 LocalTemplates 数量
- [ ] 流程规则：nodemeta 新增字段时，PR 模板强制勾选"已 review 所有返回 node 的 endpoint"
- [ ] 文档：本文档 + `assets/templatecenter-sequence-distribution-pull.drawio` 同步更新

---

## 12. 不做的事

- **不重命名 inner / meta endpoint**——破坏面太大，跟其他 PR 冲突
- **不合并 `node.Node` 和 `NodeSnapshot` 为一个结构**——会污染调度器，且 R1-R5 阶段还在演进
- **不把 LocalTemplates 加到 `node.Node` 顶层**——同上
- **不改 web 端**（web 端已经能看了，是正确的）

---

## 13. 后续观察

修完后**新增一个监控指标**：
- `cubemastercli_node_query_total{endpoint="meta|inner", command="list|show|isolate"}`
- 看运维切到 meta endpoint 的比例——3 个月内应该 ≥ 80%

如果比例上不去，说明 cli 的命令命名 / 帮助文本 / 帮助页面还需要进一步引导。
