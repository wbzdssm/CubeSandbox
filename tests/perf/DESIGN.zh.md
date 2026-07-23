# CubeSandbox Perf — 设计与架构

## 概述

`tests/perf` 是 CubeSandbox 性能基准测试套件。设计目标：**一次运行，多份报告** — 一条命令收集环境元数据、通过统一契约驱动外部压测脚本、产出自包含的可视化报告（JSON / Markdown / HTML），无需第三方图表库。

### 设计约束

| 约束 | 原因 |
|---|---|
| 零运行时 JS 依赖 | 报告在隔离环境中也能打开 |
| 框架 Python≥3.11，脚本不限 | 降低 CI 环境要求 |
| 外部脚本是唯一的 workload 来源 | 框架无关；团队可在自己的仓库中编写压测脚本 |
| 单次采集 | 环境 + 压测 + 清理一次完成，无需手动合并 |

---

## 分层架构

```
┌─────────────────────────────────────────────────────────┐
│  __main__.py        CLI 编排入口                         │
│  __init__.py        .env 生命周期、sys.path 引导        │
├─────────────────────────────────────────────────────────┤
│  framework/         核心引擎（不依赖报表/HTML）           │
│    config.py        环境变量驱动的运行时参数              │
│    env.py           硬件 + OS + Cube 组件版本采集        │
│    registry.py      @benchmark 装饰器、外部脚本发现       │
│                     与注册、场景生命周期                  │
│    runner.py        PerfResult/Sample、measure_parallel   │
├─────────────────────────────────────────────────────────┤
│  reporting/         数据组装与展示配置                    │
│    report.py        build_report_data → JSON + Markdown  │
│    report_config.py TOML + env 展示配置                  │
├─────────────────────────────────────────────────────────┤
│  plugins/           懒加载输出适配器                      │
│    html_report.py   无 Chart.js 的 SVG 折线图 +           │
│                     多环境折叠卡片                        │
├─────────────────────────────────────────────────────────┤
│  ops/               平台资源管理                          │
│    cleanup.py        快照 CRUD、默认脚本注册、             │
│                      压测后资源清理                        │
└─────────────────────────────────────────────────────────┘
```

### 层间契约

- **framework/** 不理解 HTML、Markdown 或 TOML。它只读 `os.environ`、调 `subprocess` 或 SDK，将 `PerfResult` 写入模块级列表。
- **reporting/** 依赖 `framework/` 的数据结构，但不依赖 `plugins/`。它将原始 `PerfResult` 转为 JSON 兼容 dict（`build_report_data`）。
- **plugins/** 依赖 `reporting/` 的 JSON schema。目前只有 `html_report.py` 一个消费者；未来可加 Slack/Mattermost 适配器。
- **ops/** 依赖 SDK（`cubesandbox`），但不依赖 `framework/` 或 `reporting/`。被 `__main__.py` 调用来做压测后清理。

---

## 关键设计

### 1. 外部脚本契约

框架通过 CLI 契约驱动外部脚本，而不是把压测逻辑写死在框架里：

```bash
python bench_xxx.py -c <并发度> -n <操作数> [--rounds N] [--no-header]
```

| 动因 | 说明 |
|---|---|
| 框架无关 | 团队可用任意语言写脚本，只要接受契约即可 |
| 文件系统发现 | `CUBE_EXTERNAL_SCRIPTS` 或 `--scripts DIR` — 无需配置文件 |
| 墙上时间测量 | 每次调用一次 subprocess，框架记录总 time.now 而非脚本内部耗时 |

### 2. 并发梯度 — 环境变量驱动

每个场景接受并发梯度（如 `1,10,20,50`），框架按每个梯度各调用一次脚本。梯度可按场景覆盖：

```
CUBE_CREATE_CONCURRENCY=1,10,20,50      # 全局默认
CUBE_CLONE_CONCURRENCY=1,5,10           # 场景级覆盖
```

覆盖由 `framework/registry.py` 在注册时解析——脚本自身不感知梯度。

### 3. 版本采集：release-manifest 为权威源

CubeSandbox 安装后会在 `/usr/local/services/cubetoolbox/release-manifest.json` 留下清单，这是 `cubemaster` 和 `cubelet` 共用的单一事实源。`framework/env.py` 依次按以下优先级查找，逐级回退：

1. **首选** → `release-manifest.json`（所有组件版本 + 摘要 + guest-image + kernel）
2. **备用** → CubeAPI `/cluster/versions`（运行时视图，注意返回字段是 **camelCase** — 旧代码用 snake_case 导致全部读空）
3. **兜底** → 本地二进制（`cube-api -V`、`cubemaster -v`……）

采集到的 `release_version` 放在环境指纹的最前面，保证同机换版本时会自动分为不同的 series。

### 4. 单次报告生成

一次 `build_report_data()` 调用产出统一 JSON blob：

```json
{
  "generated_at": "ISO8601",
  "environment": { /* 硬件、OS、全部组件版本 */ },
  "config": { /* 解析后的运行时参数 */ },
  "functional": { /* 通过/失败/跳过计数 */ },
  "perf": [ /* PerfResult 数组 */ ]
}
```

同一份 JSON 同时喂给 `report.py`（→ Markdown）和 `html_report.py`（→ HTML）。多环境对比只需传多份 JSON 给 `generate_html()`——内部 `_group_runs()` 按环境指纹分组，SVG 折线图每个指纹一条线。

### 5. 压测后清理（ops/）

`ops/cleanup.py` 提供三个被 `__main__.py` 调用的函数：

- `register_default_scripts()` — 未设 `CUBE_EXTERNAL_SCRIPTS` 时注册内置默认脚本
- `list_snapshots()` / `delete_snapshots()` — 快照 CRUD（仅 `snap-*` 前缀 ID）
- `cleanup_after_benchmark()` — 通过 `CUBE_PERF_AUTO_CLEANUP=1` 开启，压测后自动删残留快照

---

## 单次运行数据流

```
                    config.py 读环境变量
                         │
                    collect_env_info(cfg)
                         │ EnvInfo (80+ 字段)
                         ▼
             ┌─ register_default_scripts()
             │       registry 发现外部 .py 脚本
             │
             └─ registry.run_all(cfg, selected=...)
                     │
                     ├─ 遍历每个外部脚本：
                     │     for c in CONCURRENCY_GRADIENT:
                     │       subprocess: bench_xxx.py -c <c> -n <n>
                     │       记录 end-start → PerfResult → PERF_RESULTS[]
                     │
                     ▼
               build_report_data(env)
                     │ {generated_at, environment, config, functional, perf}
                     ▼
        ┌────────────┴──────────────┐
        ▼                           ▼
   report.json / report.md    --html? → html_report.generate_html()
        │                                  │ SVG + 折叠详情卡片
        │                                  ▼
        │                           perf_report.html
        │
        └─ cleanup_after_benchmark()
              (CUBE_PERF_AUTO_CLEANUP=1)
```

---

## 多环境报告原理

`generate_html()` 接收多份 JSON 时：

1. **分组** (`_group_runs`) — 计算 `_env_fingerprint`；相同指纹的样本合并，不同指纹分成独立 series。
2. **标签** (`_env_label`) — 优先用 `ip_address` 而非 `hostname`，追加 `release_version`。
3. **消歧** (`_disambiguate_labels`) — 多环境时在图例中拼接差异的组件版本。
4. **渲染** — SVG 折线图每个 series 一条 `<polyline>`，数据点用 `<g><title>...</title></g>` 包一层提供原生 hover 提示。

HTML 完全自包含 — 无外部 Chart.js CDN、无网络依赖。所有 CSS / JS / SVG 均内联。

---

## 可扩展性

| 需求 | 改动位置 |
|---|---|
| 加新场景 | 写一个 `.py` 脚本，设 `CUBE_EXTERNAL_SCRIPTS` |
| 加新组件版本 | `framework/env.py` → `_MANIFEST_COMPONENT_MAP` + 1 行 |
| 加新指标列 | `reporting/report.py` → `_DEFAULT_METRICS` |
| 加输出格式 | `plugins/` 里加新适配器，消费 `build_report_data()` schema |
| 加清理目标 | `ops/cleanup.py` 加函数，接入 `cleanup_after_benchmark()` |

---

## 快速接入新场景

三步完成一个新压测场景的接入。

### 第一步 — 编写脚本

创建一个 `.py` 文件，接受 `-c`（并发度）和 `-n`（操作数）参数。首行 docstring 会作为报告标题。

```python
# bench_my_scenario.py
"""我的场景压测"""    # ← 首行 docstring = 报告标题

import argparse, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("-c", type=int, default=1)     # 并发度（必选）
ap.add_argument("-n", type=int, default=5)       # 每轮操作数（必选）
ap.add_argument("--rounds", type=int, default=3)
ap.add_argument("--no-header", action="store_true")
args = ap.parse_args()

from cubesandbox import Sandbox

sb = Sandbox.create("tpl-xxx")
for _ in range(args.n):
    sb.do_something(concurrency=args.c)
sb.kill()

print(f"n={args.n}, c={args.c}")   # 可选 stdout 用于调试
```

### 第二步 — 注册到 `.env`

在 `tests/perf/.env` 的 `CUBE_EXTERNAL_SCRIPTS` 中添加脚本路径：

```bash
CUBE_EXTERNAL_SCRIPTS=\
../examples/snapshot-rollback-clone/bench_clone_concurrency.py,\
../examples/my-new-feature/bench_my_scenario.py
```

### 第三步 — 运行

```bash
# 列出场景确认注册成功
python3 -m perf --list-scenarios

# 只跑新场景
python3 -m perf --rounds 1 --scenarios my-scenario --html
```

### 框架自动完成的事

| 环节 | 框架处理 |
|------|---------|
| 并发度梯度 | 按 `CUBE_PERF_CONCURRENCY` 逐级调用脚本 |
| 预热 | 前 N 轮结果丢弃（`CUBE_PERF_WARMUP`） |
| 计时 | 每次调用记录墙钟时间 |
| 指标 | 自动计算 avg / min / p50 / p95 / p99 / max |
| 报告 | 生成 Markdown 表格 + HTML 折线图 |
| 清理 | 自动清理残留沙箱和快照 |

脚本作者只需写压测逻辑 — 不用管计时、统计、报告格式。

### CLI 参数约定

| 参数 | 必选 | 说明 |
|------|:----:|------|
| `-c N` | 是 | 并发度 |
| `-n N` | 是 | 每轮操作数 |
| `--rounds N` | 否 | 脚本内部轮数（默认同 `-n`） |
| `--no-header` | 否 | 抑制重复表头 |

---

## 配置优先级

```
CLI 参数  >  环境变量  >  report.toml  >  内置默认
```

`report.toml` 查找路径：`$CUBE_REPORT_CONFIG` → `./report.toml` → `tests/perf/report.toml` → `tests/report.toml` → `sdk/python/report.toml`。文件不存在或缺键 → 回退到默认。
