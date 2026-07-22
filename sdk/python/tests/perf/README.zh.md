# `perf` — CubeSandbox Python SDK 性能压测套件

[English](./README.md) · 架构与实现细节见 [DESIGN.zh.md](./DESIGN.zh.md)

一个脚本、一条命令，把全部性能场景跑一遍，产出 **JSON + Markdown** 报告。
每个沙箱 / 快照 / Volume 都是**用完随手删除**，不留残留。自包含、零第三方依赖（除 SDK 本身）。

> HTML 报告是可选的增强项，见文末「HTML 报告（可选）」。默认路径只产出 JSON + Markdown。

## 快速开始

在 `sdk/python/tests/` 目录下运行：

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

**首次自动生成 + 运行后写回**：若启动时找不到任何 `.env`，套件会在 `tests/` 下
自动生成一份 `.env`，包含**所有常用变量**（数据流、场景开关、运行参数、外部脚本路径、
输出路径）——均为注释占位。若你已在命令行 export 了其中某些项，会被写成已填状态。
**打本地后端（默认 `http://127.0.0.1:3000`）时无需填任何变量即可开跑**；仅在打远端后端
时才需填 `CUBE_API_URL`（及鉴权 `CUBE_API_KEY`）。`CUBE_TEMPLATE_ID` 留空则自动发现一个
READY 模板。

每次运行结束后，**本次实际用到的所有变量**（含自动发现的模板 ID、并发梯度、场景开关、
外部脚本路径）会被**二次写回**该 `.env`，因此第二、三次直接 `python3 -m perf` 就能复用，
无需再 export。已存在的 `.env` 只会就地更新这几项，其余行（注释、你手动加的内容）原样保留。

```bash
# 打本地后端（默认 http://127.0.0.1:3000）：无需指定任何变量，直接跑
python3 -m perf

# 打远端后端：只需额外指定 CUBE_API_URL（及鉴权 CUBE_API_KEY），会被写回 .env
CUBE_API_URL=https://api.example.com CUBE_API_KEY=sk-... python3 -m perf

# 小节点跑到 "no more resource"？调小一次并发梯度即可 —— 会写回 .env，
# 之后的运行会一直沿用这个更小的梯度，无需重复 export
CUBE_CREATE_CONCURRENCY=1,3,5 python3 -m perf

# 想固化场景开关时，编辑 tests/.env（参照 .env.example），例如：
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
| 接入外部脚本 | `CUBE_EXTERNAL_SCRIPTS=/path/to/a.py,/path/to/b.py` | `--scripts /path/to/dir/` |

> `.env` 查找顺序（就近优先）：当前目录 → `tests/` → `sdk/python/`；
> 也可用 `CUBE_DOTENV=/path/to/other.env` 指定文件。首次无 `.env` 时在 `tests/` 下自动生成。

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
| `--scenarios / --only SCENARIO...` | 只跑指定场景；接键或别名，逗号/空格分隔，`no-`/`skip-`/`!`/`^` 前缀排除 |
| `--rounds N` | 覆盖 `CUBE_PERF_ROUNDS`（默认 10） |
| `--list-scenarios` | 列出全部场景键/别名后退出 |
| `--md-only JSON` | 从已有 JSON 重渲染 Markdown + JSON（不跑压测、不连后端） |

### 外部脚本

| 选项 | 说明 |
|---|---|
| `--scripts DIR` | 跑 `DIR/` 下所有 `.py` 文件，按 `CUBE_CREATE_CONCURRENCY` 阶梯并发，收集 wall-time 统计 |

### 快照清理

| 选项 | 说明 |
|---|---|
| `--cleanup` | 跑压测前删除所有 `snap-*` 模板 |
| `--cleanup-dry-run` | 预览将被清理的 `snap-*` 模板，不删、不跑压测 |
| `--cleanup-older-than N` | 配合 `--cleanup`，只删 N 分钟前的快照 |

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
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | 轻量场景（snapshot-create / rollback / pause-resume）并发扫描档位 |
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | 重型场景（template-create / create-from-snapshot / clone）并发扫描档位 |
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
| `CUBE_EXTERNAL_SCRIPTS` | — | 逗号分隔的外部脚本路径（`.py`，接受 `-c -n --rounds --no-header`） |
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

**三种方式，从简到繁，按需选择。**

### 方式一：`@perf_test`（一行 import，适合绝大多数场景）

```python
# cases/<任意目录>/bench_<任意>.py
from framework import perf_test, op

@perf_test("my-scenario", title="我的场景", levels=(1, 5, 10))
def bench(cfg, concurrency, n):
    yield op.sandbox(cfg, lambda sb: sb.exec("whoami"))
```

`@perf_test` 自动生成 `@benchmark` + `@parallel_sweep` + `ReportGroup`，一个装饰器搞定。
不传 `levels` 则表示非并发场景，`bench(cfg)` 只跑一次。

### 方式二：`def run():`（零 import，纯脚本）

```python
# cases/<任意目录>/bench_<任意>.py
"""我的场景描述"""                  # ← 首行自动当报告标题
LEVELS = (1, 5, 10)                 # ← 并发阶梯（不写用默认）

def run():
    from cubesandbox import Sandbox
    sb = Sandbox.create("tpl-xxx")
    try:
        return sb.exec("whoami")
    finally:
        sb.kill()
```

文件放到 `cases/` 下**自动注册**，框架按 `LEVELS` 扫并发、计时、统结果。**零 framework import。**

### 方式三：外部独立脚本（零耦合，`-c` `-n` 约定）

如果压测脚本完全不依赖框架 —— 自己的 `argparse`、自己的业务逻辑、自己的并发控制 ——
把路径加到 `.env` 即可接入：

```bash
# tests/.env
CUBE_EXTERNAL_SCRIPTS=/path/to/bench_clone.py,/path/to/bench_create.py
```

**脚本约定**（脚本方只需要遵守这个接口）：

```
python bench_xxx.py -c <并发> -n <操作数> --rounds <轮数> --no-header
```

| 参数 | 必选 | 说明 |
|------|:---:|------|
| `-c N` | 是 | 并发度，框架会按 `CUBE_CREATE_CONCURRENCY` 阶梯逐一调用 |
| `-n N` | 是 | 单轮操作数 |
| `--rounds N` | 否 | 脚本内部轮数（已传，加上即可） |
| `--no-header` | 否 | 抑制脚本自己的表头输出 |

示例：

```python
# /path/to/bench_clone.py
"""Clone concurrency benchmark."""         # ← 首行注释 → 报告标题

import argparse
ap = argparse.ArgumentParser()
ap.add_argument("-c", type=int, default=1)
ap.add_argument("-n", type=int, default=5)
ap.add_argument("--rounds", type=int, default=3)
ap.add_argument("--no-header", action="store_true")
args = ap.parse_args()

from cubesandbox import Sandbox
sb = Sandbox.create("tpl-xxx")
sb.clone(n=args.n, concurrency=args.c)
sb.kill()
```

**脚本方**只负责提供 `-c -n --rounds --no-header` 四个参数的入口，**框架方**负责：
扫并发阶梯、按档位逐一调用、测 wall-clock、收集统计。

> 默认也会自动发现 `examples/snapshot-rollback-clone/bench_*.py`，无需配置 `CUBE_EXTERNAL_SCRIPTS`。

### 方式四：`@sandbox_benchmark`（完整控制，适合复杂场景）

```python
from cubesandbox import Sandbox
from framework.registry import ReportChart, ReportSection, sandbox_benchmark

@sandbox_benchmark(
    "rollback",
    header=" [Perf] Rollback",
    fixture="snapshot",
    report=ReportSection(
        table="latency",
        order=7,
        title_zh="回滚（Rollback）",
        title_en="Rollback",
        method_zh="对运行中沙箱调用 `POST /sandboxes/{id}/rollback` …",
        method_en="`POST /sandboxes/{id}/rollback` restores memory + filesystem …",
        noun_zh="回滚", noun_en="rollback",
        charts=(ReportChart("回滚（Rollback）"),),
    ),
)
def bench_rollback(sb: Sandbox, snap_id: str) -> None:
    sb.rollback(snap_id)
```

完整说明见 [DESIGN.zh.md](./DESIGN.zh.md) §4。

### 接入方式对比

| 方式 | import 数量 | 适合 |
|------|:---:|------|
| `@perf_test` | 1 行 | 起沙箱→做件事→销毁，90% 的场景 |
| `def run():` | 0 | 完全不想学框架，脚本即测例 |
| 外部脚本 `-c -n` | 0 | 已有自己的 argparse/业务逻辑 |
| `@sandbox_benchmark` | 2 行 | 需要自定义报告章节、图表 |

## 编程方式调用

```python
import sys
sys.path.insert(0, "sdk/python/tests")

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
