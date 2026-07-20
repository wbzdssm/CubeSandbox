# `perf` — 独立性能压测套件

[English](./README.md)

CubeSandbox Python SDK 的性能压测场景，与官方 CubeSandbox 性能报告保持一致。
从 `tests/e2e/` 中拆分出来，便于与功能集成测试独立运行和维护。

## 包结构

| 模块 | 职责 |
|---|---|
| `benchmarks.py` | 13 个压测场景 + `@benchmark` 装饰器/注册表 + `run_all()` + 组件版本采集 |
| `__main__.py` | CLI 入口（`python3 -m perf`），支持 HTML 报告生成 |
| `config.py` | 配置解析（`resolve_config()`）与运行期可调项（`PERF_ROUNDS`、`CONCURRENCY_LEVELS`、`DIRTY_SWEEP`、节点清理等） |
| `env.py` | 环境信息采集（主机/CPU/内存/磁盘/模板元数据、`get_free_mem_gb()`、组件版本） |
| `runner.py` | 计时/统计原语（`PerfResult`、`PerfSample`、`measure_parallel`、`percentile`、`skip`、`PERF_RESULTS`） |
| `report.py` | Markdown + JSON 报告生成（中英双语） |
| `report_html.py` | 自包含交互式 HTML 报告，含基线对比 |
| `report_config.py` | HTML 报告的分组/字段可定制项（env / `report.toml` 覆盖） |
| `baseline.py` | CubeSandbox 官方性能基线数据（源自博客） |
| `ivshmem.py` | ivshmem 共享内存 host 侧 mmap 探针 |
| `__init__.py` | `sys.path` 初始化（定位 `sdk/python`）+ 零依赖 `.env` 加载 |

本包为**自包含**结构——`config`/`env`/`runner`/`report` 等全部内置于 `perf/` 目录内，
不再依赖同级 `tests/e2e/` 包。二者是独立的 CLI 入口，但走同一套底层 SDK。

## 压测场景

按源代码装饰顺序（== 运行顺序）列出，共 13 个：

| 场景 | 函数 | 默认状态 |
|---|---|---|
| 基于模板创建沙箱（单发 & 并发） | `bench_template_create` | 默认开 |
| 部署密度（内存开销） | `bench_deployment_density` | 默认开，`CUBE_SKIP_DENSITY=1` 跳过 |
| 创建快照（并发） | `bench_snapshot_create` | 默认开 |
| 快照延迟 vs 脏页规模（含 create-from 恢复） | `bench_snapshot_dirty` | 默认开，`CUBE_SKIP_SNAPSHOT_DIRTY=1` 跳过 |
| 基于快照创建沙箱（并发） | `bench_snapshot_create_from` | 默认开 |
| 回滚（Rollback） | `bench_rollback` | 默认开 |
| 克隆（顺序 & 并发） | `bench_clone` | 默认开 |
| 暂停 / 恢复 | `bench_pause_resume` | 默认开 |
| 创建 Volume（单发 & 并发） | `bench_volume_create` | 默认关，`CUBE_RUN_VOLUME=1` 启用 |
| 删除 Volume（单发 & 并发） | `bench_volume_destroy` | 默认关，`CUBE_RUN_VOLUME=1` 启用 |
| Volume 元数据操作（list / get_info / connect） | `bench_volume_metadata` | 默认关，`CUBE_RUN_VOLUME=1` 启用 |
| 挂载 Volume 的沙箱创建（端到端） | `bench_volume_mount_sandbox` | 默认关，`CUBE_RUN_VOLUME=1` 启用 |
| ivshmem 共享内存 host 侧 mmap 读写 | `bench_ivshmem` | 默认关，`CUBE_RUN_IVSHMEM=1` 启用 |

## 用法

在 `tests/` 目录下运行：

```bash
CUBE_API_URL=https://api.example.com CUBE_API_KEY=sk-... python3 -m perf
```

### CLI 选项

| 选项 | 说明 |
|---|---|
| `--html` | 运行压测后生成交互式 HTML 报告 |
| `--rounds N` | 覆盖 `CUBE_PERF_ROUNDS`（默认：10） |
| `--scenarios / --only SCENARIO...` | 只运行指定场景（键或别名，支持逗号/空格分隔，`no-<键>` 排除）。见下方「场景选择」 |
| `--list-scenarios` | 列出所有可用场景键与别名后退出 |
| `--output PATH` | HTML 输出路径（默认：`perf_report.html`） |
| `--title TITLE` | 自定义 HTML 报告标题 |
| `--html-only JSON...` | 基于已有 JSON 数据文件生成 HTML（不运行压测） |
| `--compare JSON1 JSON2` | 生成两次运行的对比 HTML 报告 |

### 场景选择

默认运行全部场景。可通过 `--scenarios`/`--only`（或环境变量 `CUBE_PERF_SCENARIOS`）
只跑其中一部分，便于单独压测某条链路（如只测「快照冷启动」或「快照创建」）：

```bash
# 只跑「基于快照创建沙箱」（快照冷启动）
python3 -m perf --scenarios snapshot-create-from
# 别名等价写法
python3 -m perf --only cold-start

# 只跑「快照创建」
python3 -m perf --only snapshot

# 组合多个场景（逗号或空格均可）
python3 -m perf --scenarios snapshot-create,rollback

# 跑全部但排除 ivshmem 与 volume 组
python3 -m perf --scenarios all no-ivshmem no-volume

# 环境变量方式（CLI 优先级更高）
CUBE_PERF_SCENARIOS="snapshot rollback" python3 -m perf
```

规范键（canonical keys）：`template-create`、`density`、`snapshot-create`、
`snapshot-create-from`、`snapshot-dirty`、`rollback`、`clone`、`pause-resume`、
`volume-create`、`volume-destroy`、`volume-metadata`、`volume-mount-sandbox`、`ivshmem`。

常用别名：`create`→`template-create`，`snapshot`→`snapshot-create`，
`cold-start`/`snapshot-cold-start`/`restore`→`snapshot-create-from`，
`dirty`→`snapshot-dirty`，`pause`/`resume`→`pause-resume`，`volume`→四个 volume 场景。
完整列表执行 `python3 -m perf --list-scenarios` 查看。

### HTML 报告

HTML 报告是一个**自包含、零依赖**的页面，提供：

- **环境概览**：主机、CPU、内存、磁盘、操作系统、SDK 版本、CubeAPI 版本
- **基线对比**：与 [CubeSandbox 官方性能数据](https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html) 并排对比
- **各场景表格**：平均值 / 最小值 / P50 / P95 / 最大值 / 总耗时 / 单次均摊
- **柱状图**：延迟可视化对比（当前 vs 基线）
- **多机合并**：传入多个 JSON 文件可对比不同机器的运行结果

### 多机工作流

1. 在每台 DevCloud 机器上运行：
   ```bash
   CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf
   ```
   生成 `report_YYYYMMDDTHHMMSSZ.json`。

2. 收集所有 JSON 文件，生成合并后的 HTML 报告：
   ```bash
   python3 -m perf --html-only machine1.json machine2.json machine3.json --output merged_report.html
   ```

3. 检查性能劣化，对比两次运行：
   ```bash
   python3 -m perf --compare before.json after.json --output diff_report.html
   ```

### 可选环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `CUBE_TEMPLATE_ID` | 自动发现 | 指定后跳过自动发现 READY 模板 |
| `CUBE_PERF_SCENARIOS` | 全部 | 逗号/空格分隔的场景键或别名，只运行指定场景（被 `--scenarios` 覆盖） |
| `CUBE_PERF_ROUNDS` | `10` | 每个压测场景的轮数 |
| `CUBE_PERF_CONCURRENCY` | `1,2,4` | 创建/快照/回滚/暂停等场景的并发扫描档位（逗号分隔） |
| `CUBE_PERF_WARMUP` | `1` | 计时前丢弃的预热轮数（去冷启动尖刺） |
| `CUBE_PERF_SETTLE` | `0` | 轮次间静默秒数（让节点回稳） |
| `CUBE_DIRTY_SWEEP` | `0,10,50,100,200,500,800,1024` | `snapshot-dirty` 场景的脏页写入 MB 扫描档位 |
| `CUBE_DENSITY_COUNT` | `100` | 密度测试的最大沙箱数量 |
| `CUBE_SKIP_DENSITY` | — | 设为 `1` 跳过部署密度压测 |
| `CUBE_SKIP_SNAPSHOT_DIRTY` | — | 设为 `1` 跳过 `snapshot-dirty` 压测 |
| `CUBE_RUN_VOLUME` | — | 设为 `1` 启用 4 个 Volume 场景（默认跳过） |
| `CUBE_RUN_IVSHMEM` | — | 设为 `1` 启用 ivshmem 场景（默认跳过；需 ivshmem 模板且必须在 host 上运行） |
| `CUBE_IVSHMEM_TEMPLATE_ID` | 回落 `CUBE_TEMPLATE_ID` | ivshmem 场景专用模板 |
| `CUBE_IVSHMEM_ITERATIONS` | `10000` | ivshmem 场景的 mmap 迭代次数 |
| `CUBE_PERF_CLEANUP` | `1`（开） | 设为 `0` 关闭轮次间的节点残留 micro-VM 清理 |
| `CUBE_CLEANUP_CMD` | `echo y \| cubecli unsafe destroyall -f` | 覆盖节点清理命令 |
| `CUBE_OUTPUT_REPORT` | `report` | 输出报告的基础路径 |
| `CUBE_HTML_OUTPUT` | `perf_report.html` | HTML 报告输出路径 |

> **节点清理**：SDK 的 `kill()` 不总能回收残留沙箱，长跑会逐渐耗尽节点资源；套件默认在每轮计时前
> shell 调用节点本地 `cubecli` 强制 `destroyall`，回到干净的冷启动状态。设 `CUBE_PERF_CLEANUP=0` 关闭，
> 或用 `CUBE_CLEANUP_CMD` 覆盖清理命令。

### 报告

每次运行生成：

- `report_YYYYMMDDTHHMMSSZ.json` — JSON 数据（用于 HTML 报告 & 多机合并）
- `report.md` / `report.zh.md` — Markdown，英文 / 中文
- `report.json` / `report.zh.json` — JSON 摘要，英文 / 中文
- `perf_report.html` — 交互式 HTML 报告（需 `--html` 标志）

### 编程方式调用

```python
import sys
sys.path.insert(0, "tests")

from perf.config import resolve_config
from perf.env import collect_env_info
from perf import benchmarks, report
from perf.report_html import generate_html

cfg = resolve_config()
env = collect_env_info(cfg)
benchmarks.run_all(cfg)

report.write_reports(env)          # 写 report.md / report.json（中英）
generate_html(["report.json"], output_path="my_report.html")
```

## 新增压测场景（一行装饰器）

一个场景需要的**全部元数据都内聚在函数上方的 `@benchmark(...)` 一行装饰器里**：
注册、CLI 别名、opt-in/opt-out 跳过门控、可选依赖检测、HTML 报告图表分组。
因此新增用例**只需两步**——写函数 + 打装饰器，**不需要**改任何注册表、别名表、
`skip` 块或 `report_html.py`。

### 代码插入方式

在 `benchmarks.py` 中，把新函数插到你希望它运行的位置即可：**装饰顺序（== 源代码
顺序）就是套件的运行顺序**，也是 HTML 报告里图表出现的顺序。例如通用场景插在
`bench_clone` 附近、Volume 相关场景插在 volume 组内。

最小骨架：

```python
@benchmark("my-scenario")
def bench_my_scenario(cfg: Config) -> None:
    """Benchmark: 一句话英文描述."""
    print(f"\n{'='*60}")
    print(" [Perf] My Scenario")
    print(f"{'='*60}")

    for concurrency in CONCURRENCY_LEVELS:
        n = PERF_ROUNDS * concurrency

        def do_one():
            sb = Sandbox.create(cfg.template_id, timeout=120, config=cfg)
            try:
                ...  # 被测操作
            finally:
                try: sb.kill()
                except Exception: pass

        # 共享 runner 负责并发调度 + 计时 + 统计
        result = measure_parallel(f"my-scenario-c{concurrency}", do_one,
                                  n=n, concurrency=concurrency)
        PERF_RESULTS.append(result)
```

函数体只有两条硬性约定：

1. **结果必须 `PERF_RESULTS.append(result)`**（`result` 来自 `measure_parallel`，或手工
   构造的 `PerfResult(scenario=..., samples=[PerfSample(...)])`）。只有进了
   `PERF_RESULTS`，Markdown / JSON / HTML 报告才采集得到。
2. **场景名（`PerfResult.scenario`）用 `<key>-c<并发数>` 命名**，例如
   `my-scenario-c4`。出图表时报告靠这个前缀（`<prefix>-c<N>`）聚合数据点。

参考现成实现：并发采样看 `bench_template_create`，单沙箱串行 + 手工 `PerfResult`
看 `bench_snapshot_create`，单值指标（非延迟）看 `bench_deployment_density`。

### `@benchmark` 参数

`key` 为规范场景键（CLI 选择用），其余均为可选关键字参数：

| 参数 | 类型 | 作用 |
|---|---|---|
| `key`（位置参数） | `str` | 规范场景键，`--scenarios <key>` 选择，报告图表默认前缀 |
| `aliases` | `list[str]` | 友好别名；多个用例可共享一个别名（如四个 `volume-*` 都挂 `volume`） |
| `opt_in_env` | `str` | **默认关**：该环境变量 `=1` 才运行，否则跳过（volume / ivshmem） |
| `opt_out_env` | `str` | **默认开**：该环境变量 `=1` 时跳过（density / snapshot-dirty） |
| `skip_reason` | `str` | opt-in 跳过提示里追加的人类可读原因 |
| `available` | `bool` | 导入期求值，`False` 无条件跳过（如可选类型 `Volume is not None`） |
| `report` | `ReportGroup \| list[ReportGroup]` | 贡献 HTML 报告的图表 + 汇总表分组 |

装饰器只在声明了 gate（`opt_in_env` / `opt_out_env` / `available=False`）时才包一层
wrapper，普通场景零开销直连原函数。

### 出图表（`ReportGroup`）

给 `report=` 传一个 `ReportGroup` 即可在 HTML 报告里生成对应的柱状图 + 汇总表：

```python
@benchmark("my-scenario", report=ReportGroup("我的场景标题"))
def bench_my_scenario(cfg: Config) -> None: ...
```

`ReportGroup` 字段：`title`（图表标题，必填）、`prefix`（匹配前缀，默认取 `key`）、
`x_key`（默认 `"c"`）、`x_label`（默认 `"并发数"`）、`fallback`（无数据时的兜底坐标，
默认 `(1, 2, 4)`）。图表用 `<prefix>-<x_key><N>` 匹配 `PERF_RESULTS` 里的场景名。

一个用例可声明**多个**分组——例如 pause/resume 用一个函数同时喂「暂停」「恢复」两张图：

```python
@benchmark("pause-resume", aliases=["pause", "resume"],
           report=[ReportGroup("暂停（Pause）", prefix="pause"),
                   ReportGroup("恢复（Resume）", prefix="resume")])
def bench_pause_resume(cfg: Config) -> None: ...
```

不传 `report=` 的场景只进 Markdown / JSON 汇总，不出 HTML 图表（如 `density`、`clone`）。

### 常见配方

```python
# 1) 默认就跑、要出图表的普通场景
@benchmark("my-scenario", report=ReportGroup("我的场景"))

# 2) 默认关、需 env=1 才跑（外部依赖尚未就绪）
@benchmark("my-scenario", opt_in_env="CUBE_RUN_MINE",
           skip_reason="backend endpoint not available yet")

# 3) 默认开、想临时关时设 env=1
@benchmark("my-scenario", opt_out_env="CUBE_SKIP_MINE")

# 4) 依赖某个可选 SDK 类型，缺失则无条件跳过
@benchmark("my-scenario", available=MyOptionalType is not None)
```

装饰器完成后，`--list-scenarios`、`--scenarios <key>`、`no-<key>` 排除、`all` 全量、
HTML 图表分组会**自动**识别新用例，无需其他改动。
