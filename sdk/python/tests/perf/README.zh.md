# `perf` — CubeSandbox Python SDK 性能压测套件

[English](./README.md) · 架构与实现细节见 [DESIGN.zh.md](./DESIGN.zh.md)

一个脚本、一条命令，把全部性能场景跑一遍，产出 **JSON + Markdown** 报告。
每个沙箱 / 快照 / Volume 都是**用完随手删除**，不留残留。自包含、零第三方依赖（除 SDK 本身）。

> HTML 报告是可选的增强项，见文末「HTML 报告（可选）」。默认路径只产出 JSON + Markdown。

## 快速开始

在 `tests/` 目录下运行：

```bash
# 跑全部场景，产出 JSON + Markdown
CUBE_API_URL=https://api.example.com CUBE_API_KEY=sk-... python3 -m perf
```

不指定场景就是**跑全部**。`CUBE_TEMPLATE_ID` 不设时会自动发现一个 READY 模板。

## 指定跑哪些场景

```bash
# 跑全部默认开启的场景（默认行为，不带参数即可）
python3 -m perf

# 只跑部分（键或别名，逗号/空格分隔，--only 与 --scenarios 等价）
python3 -m perf --only snapshot rollback
python3 -m perf --scenarios snapshot-create-from

# 跑全部默认开启的，再排除其中某几个（排除只对默认开启的场景有意义）
python3 -m perf --scenarios all no-density no-clone

# 显式点名一个默认关闭的场景 —— 自动启用，无需再设开关环境变量
python3 -m perf --only ivshmem
python3 -m perf --only volume

# 列出全部场景键 / 别名后退出（不跑压测，不连后端）
python3 -m perf --list-scenarios
```

**选择语法**

| 写法 | 含义 |
|---|---|
| `--only` / `--scenarios` | 二者完全等价，接一个或多个「规范键」或「别名」，逗号或空格分隔 |
| `all` | 批量通配全部场景（等价于不传参数，仍受默认开关约束，见下方注意） |
| `no-<键/别名>` | 排除某个场景；`skip-` / `!` / `^` 前缀等价（如 `no-clone`、`skip-density`、`!snapshot`） |

> ⚠️ **排除只对默认开启的场景有效**。`all` 是「批量通配」而非「逐个显式点名」，
> 因此它**不会**自动打开默认关闭的场景（`volume` / `ivshmem`）——对它们写 `no-ivshmem`
> 属于空操作。要跑默认关闭的场景，请用 `--only ivshmem` 显式点名（会自动启用），
> 或设对应开关环境变量（见下节「压测场景一览」）。

全部规范键、别名与默认开关见下方 [压测场景一览](#压测场景一览)。

### 用 `.env` 固化开关（不想每次敲命令）

场景开关本质都是 `CUBE_*` 环境变量，套件启动时会**自动加载 `.env`**（零依赖，见
`__init__.py`），因此可以把「跑哪些 / 开哪些 / 关哪些」一次写进 `.env`，之后直接
`python3 -m perf` 即可。真实环境变量与 CLI 参数始终优先，`.env` 只补空缺。

**首次自动生成 + 运行后写回**：若启动时找不到任何 `.env`，套件会在 `tests/perf/` 下
自动生成一份**极简 `.env`**，只含「数据流（连接）变量」——即
`CUBE_API_URL`、`CUBE_API_KEY`、`CUBE_TEMPLATE_ID`、`CUBE_PROXY_NODE_IP`、
`CUBE_PROXY_PORT_HTTP`、`CUBE_SANDBOX_DOMAIN`；其余场景/运行参数一律不写入，用默认即可。
若你已在命令行 export 了其中某些项，会被写成已填状态。**打本地后端（默认
`http://127.0.0.1:3000`）时无需填任何变量即可开跑**；仅在打远端后端时才需填
`CUBE_API_URL`（及鉴权 `CUBE_API_KEY`）。`CUBE_TEMPLATE_ID` 留空则自动发现一个 READY
模板。

每次运行结束后，本次实际用到的数据流变量（**含自动发现的模板 ID**）会被**二次写回**
该 `.env`，因此第二、三次直接 `python3 -m perf` 就能复用，无需再 export。已存在的 `.env`
只会就地更新这几项，其余行（注释、你手动加的场景开关）原样保留。要调场景/运行参数，
参照 `.env.example` 手动加进 `.env` 即可。

```bash
# 打本地后端（默认 http://127.0.0.1:3000）：无需指定任何变量，直接跑
python3 -m perf

# 打远端后端：只需额外指定 CUBE_API_URL（及鉴权 CUBE_API_KEY），会被写回 .env
CUBE_API_URL=https://api.example.com CUBE_API_KEY=sk-... python3 -m perf

# 想固化场景开关时，编辑 tests/perf/.env（参照 .env.example），例如：
#   CUBE_PERF_SCENARIOS=snapshot rollback   # 只跑这两个（等价 --only）
#   CUBE_RUN_IVSHMEM=1                        # 启用默认关闭的 ivshmem
#   CUBE_SKIP_DENSITY=1                       # 跳过默认开启的 density
python3 -m perf
```

| 目的 | `.env` 里写 | 等价 CLI |
|---|---|---|
| 只跑某几个 | `CUBE_PERF_SCENARIOS=snapshot rollback` | `--only snapshot rollback` |
| 全跑再排除 | `CUBE_PERF_SCENARIOS=all no-density` | `--scenarios all no-density` |
| 启用 Volume（默认关） | `CUBE_RUN_VOLUME=1` | `--only volume` |
| 启用 ivshmem（默认关） | `CUBE_RUN_IVSHMEM=1` | `--only ivshmem` |
| 关闭 density（默认开） | `CUBE_SKIP_DENSITY=1` | `--scenarios all no-density` |
| 关闭 snapshot-dirty（默认开） | `CUBE_SKIP_SNAPSHOT_DIRTY=1` | `--scenarios all no-snapshot-dirty` |

> `.env` 查找顺序（就近优先）：当前目录 → `tests/perf/` → `tests/` → `sdk/python/`；
> 也可用 `CUBE_DOTENV=/path/to/other.env` 指定文件。`.env` 已被 `.gitignore` 忽略，勿提交密钥。

## 资源清理（随手删除）

套件保证**每个被创建的资源都会在用完后立即销毁**，不依赖人工回收：

- 起一个临时沙箱做一件事 → 退出即 `sandbox.kill()`（`sandbox` fixture）。
- 需要沙箱 + 快照 → 退出即先 `delete_snapshot` 再 `kill`（`snapshot` fixture）。
- 创建本身就是被测操作（`template-create` / `density` / `clone` / `volume`）→ 用资源池收集，档位结束批量销毁。
- 清理一律 **best-effort**（吞掉异常），单个销毁失败不会中断整轮压测。

> 节点残留兜底：SDK 的 `kill()` 不总能回收残留 micro-VM，长跑会逐渐耗尽节点资源。
> 套件默认在每轮计时前 shell 调用节点本地 `cubecli` 强制 `destroyall`，回到干净冷启动状态。
> 设 `CUBE_PERF_CLEANUP=0` 关闭，或用 `CUBE_CLEANUP_CMD` 覆盖命令。

## 输出产物

默认一次运行写出 **1 份原始数据 + 4 个报告**，全部落在 `CUBE_OUTPUT_REPORT`（默认 `report`）指定的基础路径下：

| 文件 | 格式 | 内容 |
|---|---|---|
| `report_<时间戳>.json` | JSON | 本次运行的**原始数据快照**（结构见下），供重渲染 / 多机合并 / HTML |
| `report.md` | Markdown | 完整报告（英文），可直接贴 PR / Wiki |
| `report.zh.md` | Markdown | 完整报告（中文） |
| `report.json` | JSON | 报告摘要（英文），比原始数据多 `language` / `overall_status` 字段 |
| `report.zh.json` | JSON | 报告摘要（中文） |

### JSON 里有什么

原始数据是一个对象，顶层分四块：

| 键 | 说明 |
|---|---|
| `generated_at` | UTC 生成时间戳 |
| `environment` | 测试环境全量指纹：主机名 / CPU / 内存 / 磁盘、模板规格，以及 CubeAPI / CubeMaster / Cubelet / CubeShim / Guest Image / 内核 / SDK 等各组件版本 |
| `config` | 本次运行参数：`perf_rounds`（每场景轮数）、`density_max_count`（密度上限） |
| `perf` | 各场景各并发档位的结果数组，每项字段见下表 |

`perf` 数组每项对应一个 `<键>-c<并发>` 档位：

| 字段 | 含义 |
|---|---|
| `scenario` | 场景名（含并发后缀，如 `snapshot-create-c4`） |
| `count` / `concurrency` | 采样次数 / 并发度 |
| `avg_ms` / `min_ms` / `p50_ms` / `p95_ms` / `p99_ms` / `max_ms` | 单次操作延迟的统计分布（毫秒） |
| `wall_ms` / `per_ms` | 整批墙钟耗时 / 单次均摊耗时 |
| `raw_latencies` | 每次采样的原始延迟数组（供散点图 / 重新统计） |
| `extra` | 场景专属附加数据（如脏页 `write_mb`，密度 `baseline_gb` / `final_free_gb`） |

### Markdown 里有什么

`report.zh.md` / `report.md` 是可直接阅读的完整报告，含三大部分：

1. **测试环境** —— 硬件信息、沙箱规格与模板、各组件版本、测试配置。
2. **性能压测** —— 每个场景一节：基于模板创建（带吞吐量列）、部署密度（单 VM 内存均摊）、
   创建快照、快照耗时 vs 脏页规模（快照制作 + 基于快照恢复两张子表）、基于快照启动、回滚、
   克隆、暂停 / 恢复。每节的**小节标题、测试方式说明、表类型、吞吐量列、结论用词**均由对应
   `bench_*.py` 里的 `@benchmark(report=ReportSection(...))` 声明，渲染时按 `order` 字段排序（见
   下方「新增一个场景」）。
3. **总结** —— 采集场景数、每场景轮数、成功率。

### 脱离后端重渲染

原始 JSON 就是渲染报告的全部输入，因此不连后端也能**从已有 JSON 重新生成** Markdown + JSON 摘要：

```bash
python3 -m perf --md-only report_20260720T120000Z.json
```

## 压测场景一览

下表按**运行顺序**（== 注册顺序 == `--list-scenarios` 输出顺序，由 `cases/` 下模块的排序路径决定）
列出全部 **13 个场景**。「默认」为「开」的无需任何配置即会运行；为「关」的需 `--only <键>`
显式点名（自动启用）或设对应环境变量才会运行。

| 规范键 | 别名 | 默认 | 被测操作 |
|---|---|:---:|---|
| `clone` | — | 开 | 从运行中沙箱 `clone` 派生 N 个新沙箱（顺序 & 并发） |
| `ivshmem` | — | **关** | host 侧对 ivshmem 共享内存 `mmap` 读写 |
| `template-create` | `create` | 开 | 基于模板创建沙箱冷启动（单发 & 并发，报告含吞吐量） |
| `density` | — | 开 | 部署密度：累积起沙箱，测单 VM 内存均摊开销 |
| `pause-resume` | `pause`、`resume` | 开 | 并发 `pause` 落盘 + `resume` 恢复 |
| `snapshot-create` | `snapshot` | 开 | 对运行中沙箱并发制作快照 |
| `snapshot-create-from` | `snapshot-cold-start`、`cold-start`、`coldstart`、`restore` | 开 | 基于快照并发创建沙箱（冷启动恢复） |
| `snapshot-dirty` | `dirty` | 开 | 快照耗时 vs 脏页规模（0~1024 MB 扫描，含 create-from 恢复子表） |
| `rollback` | — | 开 | 对运行中沙箱原地 `rollback` 到指定快照 |
| `volume-create` | `volume` | **关** | 创建 Volume（单发 & 并发） |
| `volume-destroy` | `volume` | **关** | 删除 Volume（单发 & 并发） |
| `volume-metadata` | `volume` | **关** | Volume 元数据操作：`list` / `get_info` / `connect` |
| `volume-mount-sandbox` | `volume` | **关** | 挂载 Volume 的沙箱端到端创建 |

> `volume` 别名一次点全 4 个 `volume-*` 场景；`snapshot` 只指向 `snapshot-create`
> （其余快照场景各有自己的键 / 别名）。

### 默认关闭的场景 & 启用方式

| 场景 | 启用方式（任选其一） | 额外要求 |
|---|---|---|
| `ivshmem` | `--only ivshmem`，或 `CUBE_RUN_IVSHMEM=1` | 需 ivshmem 模板（`CUBE_IVSHMEM_TEMPLATE_ID`，回落 `CUBE_TEMPLATE_ID`）+ 在节点 host 上运行 |
| 4 个 `volume-*` | `--only volume`（一次点全 4 个），或 `CUBE_RUN_VOLUME=1` | 后端 `/volumes` 端点可用 + SDK 带 `Volume` 类型 |

### 默认开启但可关闭的场景

| 场景 | 关闭方式（任选其一） |
|---|---|
| `density` | `CUBE_SKIP_DENSITY=1`，或 `--scenarios all no-density` |
| `snapshot-dirty` | `CUBE_SKIP_SNAPSHOT_DIRTY=1`，或 `--scenarios all no-snapshot-dirty`（别名 `no-dirty`） |

## CLI 选项

### 基础

| 选项 | 说明 |
|---|---|
| `--scenarios / --only SCENARIO...` | 只跑指定场景；接键或别名，逗号/空格分隔，`no-`/`skip-`/`!`/`^` 前缀排除。显式点名默认关闭场景会自动启用它。详见「压测场景一览」 |
| `--rounds N` | 覆盖 `CUBE_PERF_ROUNDS`（默认 10） |
| `--list-scenarios` | 列出全部场景键/别名后退出 |
| `--md-only JSON` | 从已有 JSON 重渲染 Markdown + JSON（不跑压测、不连后端） |

### HTML（可选，见文末）

| 选项 | 说明 |
|---|---|
| `--html` | 运行后额外生成交互式 HTML 报告 |
| `--html-only JSON...` | 从已有 JSON 生成 HTML（不跑压测） |
| `--compare JSON1 JSON2` | 生成多份 JSON 的对比 HTML |
| `--output PATH` | HTML 输出路径（默认 `perf_report.html`） |
| `--title TITLE` | 自定义 HTML 报告标题 |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `CUBE_TEMPLATE_ID` | 自动发现 | 指定后跳过 READY 模板自动发现 |
| `CUBE_PERF_SCENARIOS` | 全部 | 场景键/别名，逗号或空格分隔（被 `--scenarios` 覆盖） |
| `CUBE_PERF_ROUNDS` | `10` | 每场景轮数 |
| `CUBE_PERF_CONCURRENCY` | `1,2,4` | 并发扫描档位 |
| `CUBE_PERF_WARMUP` | `1` | 计时前丢弃的预热轮数 |
| `CUBE_PERF_SETTLE` | `0` | 并发档位间静默秒数 |
| `CUBE_DIRTY_SWEEP` | `0,10,...,1024` | `snapshot-dirty` 脏页写入 MB 档位 |
| `CUBE_DENSITY_COUNT` | `100` | 密度测试最大沙箱数 |
| `CUBE_SKIP_DENSITY` | — | `1` 跳过部署密度 |
| `CUBE_SKIP_SNAPSHOT_DIRTY` | — | `1` 跳过 `snapshot-dirty` |
| `CUBE_RUN_VOLUME` | — | `1` 启用 4 个 Volume 场景 |
| `CUBE_RUN_IVSHMEM` | — | `1` 启用 ivshmem 场景（需 host 上运行 + ivshmem 模板） |
| `CUBE_IVSHMEM_TEMPLATE_ID` | 回落 `CUBE_TEMPLATE_ID` | ivshmem 专用模板 |
| `CUBE_IVSHMEM_ITERATIONS` | `10000` | ivshmem mmap 迭代次数 |
| `CUBE_PERF_CLEANUP` | `1` | `0` 关闭轮次间节点残留 micro-VM 清理 |
| `CUBE_CLEANUP_CMD` | `echo y \| cubecli unsafe destroyall -f` | 覆盖节点清理命令 |
| `CUBE_OUTPUT_REPORT` | `report` | 输出报告基础路径 |
| `CUBE_HTML_OUTPUT` | `perf_report.html` | HTML 报告输出路径 |

## 目录结构

| 路径 | 职责 |
|---|---|
| `__main__.py` | CLI 入口（`python3 -m perf`） |
| `__init__.py` | `sys.path` 初始化 + 零依赖 `.env` 加载 |
| `framework/` | 框架核心：`config`（配置）、`env`（环境采集）、`runner`（计时/统计/资源清理原语）、`registry`（`@benchmark` 注册表 + `ReportSection`/`ReportChart` 报告声明 + 场景选择 + `run_all`） |
| `cases/` | 具体压测场景，按 `bench_*.py` 约定**自动发现**；每个场景在装饰器里声明自己的 `ReportSection` |
| `reporting/` | 报告体系：`report`（Markdown + JSON，遍历各场景 `ReportSection` 声明渲染）、`report_html`（HTML，可选，从同一份声明的 `charts` 派生）、`report_config`（HTML 可定制层） |

## 新增一个场景

**在 `cases/` 下丢一个 `bench_<名字>.py` 文件即可自动注册**——无需改注册表、别名表或报告代码。
最常见的「起一个临时沙箱、对它做一件事、随手销毁」用 `@sandbox_benchmark` 一行糖搞定：

```python
from cubesandbox import Sandbox
from ...framework.registry import ReportChart, ReportSection, sandbox_benchmark

@sandbox_benchmark(
    "rollback",
    header=" [Perf] Rollback",
    fixture="snapshot",
    report=ReportSection(
        table="latency",                 # 表类型：latency|density|dirty|clone|pause_resume
        order=7,                          # Markdown 小节排序（§1 恒为测试环境）
        title_zh="回滚（Rollback）",      # 小节标题
        title_en="Rollback",
        method_zh="对运行中沙箱调用 `POST /sandboxes/{id}/rollback` …",  # 「测试方式」说明
        method_en="`POST /sandboxes/{id}/rollback` restores memory + filesystem in place …",
        noun_zh="回滚", noun_en="rollback",  # 结论句用词（如「单并发**回滚**延迟约 …」）
        charts=(ReportChart("回滚（Rollback）"),),  # HTML 图表（可多个；无图则留空）
    ),
)
def bench_rollback(sb: Sandbox, snap_id: str) -> None:
    """Benchmark: in-place rollback to a snapshot."""
    sb.rollback(snap_id)  # 这一行就是被测操作；沙箱与快照由框架自动清理
```

框架负责并发调度、计时、统计、报告采集、以及**资源的随手清理**。场景名统一按 `<键>-c<并发数>`
生成，报告靠该前缀聚合数据点。

**报告声明由装饰器驱动**：每个场景的小节标题 / 测试方式 / 表类型 / 吞吐量列（`throughput=True`）/
结论用词，全部写在 `@benchmark(report=ReportSection(...))` 里（`@sandbox_benchmark` 同样接受
`report=`）。Markdown 渲染遍历所有 `ReportSection` 声明、按 `order` 排序成节；HTML 图表则从同一份
声明的 `charts` 派生——**新增场景无需改动 `reporting/report.py`**。带图的延迟类场景挂 `ReportChart`，
density / dirty / clone 等无折线图的场景 `charts` 留空即可。

需要拆分关注点或自定义采样时，可直接用底层四层装饰器
（`@benchmark` / `@parallel_sweep` / `@metrics` / `@sandbox_action`）——完整说明见
[DESIGN.zh.md](./DESIGN.zh.md) §4。

## 编程方式调用

```python
import sys
sys.path.insert(0, "tests")

from perf.framework.config import resolve_config
from perf.framework.env import collect_env_info
from perf.framework import registry
from perf import cases  # noqa: F401 — 导入即注册全部场景
from perf.reporting import report

cfg = resolve_config()
env = collect_env_info(cfg)
registry.run_all(cfg)              # 或 registry.run_all(cfg, selected=["snapshot"])
report.write_reports(env)          # 写 report.md / report.zh.md / report.json / report.zh.json
```

## HTML 报告（可选）

基础流程只产出 JSON + Markdown。需要交互式单页报告时再加 `--html`：

```bash
# 运行后额外生成 HTML
python3 -m perf --html

# 不跑压测，从已有 JSON 生成 HTML
python3 -m perf --html-only report_20260720T120000Z.json

# 多机合并 / 回归对比（传多个 JSON）
python3 -m perf --html-only machine1.json machine2.json --output merged.html
python3 -m perf --compare before.json after.json --output diff.html
```

HTML 是自包含、零依赖的单页面，含环境概览、各场景表格与延迟折线图。
