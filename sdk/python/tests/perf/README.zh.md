# `perf` — 独立性能压测套件

[English](./README.md)

CubeSandbox Python SDK 的性能压测场景，与官方 CubeSandbox 性能报告保持一致。
从 `tests/e2e/` 中拆分出来，便于与功能集成测试独立运行和维护。

## 包结构

| 模块 | 职责 |
|---|---|
| `benchmarks.py` | 11 个压测场景 + `run_all()` + 组件版本采集 |
| `__main__.py` | CLI 入口（`python3 -m perf`），支持 HTML 报告生成 |
| `report_html.py` | 自包含交互式 HTML 报告，含基线对比 |
| `baseline.py` | CubeSandbox 官方性能基线数据（源自博客） |
| `__init__.py` | sys.path 初始化（定位 `sdk/python` 及同级 `e2e` 包） |

本包复用同级 [`tests/e2e/`](../e2e/README.zh.md) 包的共享基础设施，而不是
重复实现：

| 复用自 `e2e` | 用途 |
|---|---|
| `e2e.config` | `resolve_config()`、`PERF_ROUNDS`、`DENSITY_COUNT` |
| `e2e.env` | `collect_env_info()`、`get_free_mem_gb()`（现已包含 CubeAPI 版本信息） |
| `e2e.runner` | `PERF_RESULTS`、`PerfResult`、`PerfSample`、`measure_parallel`、`percentile`、`skip` |
| `e2e.report` | Markdown + JSON 报告生成 |

## 压测场景

| 场景 | 函数 |
|---|---|
| 基于模板创建沙箱（单发 & 并发） | `bench_template_create` |
| 部署密度（内存开销） | `bench_deployment_density` |
| 创建快照（并发，脏页规模扩展） | `bench_snapshot_create` |
| 基于快照创建沙箱（并发） | `bench_snapshot_create_from` |
| 回滚（Rollback） | `bench_rollback` |
| 克隆（顺序 & 并发） | `bench_clone` |
| 暂停 / 恢复 | `bench_pause_resume` |
| 创建 Volume（单发 & 并发） | `bench_volume_create` |
| 删除 Volume（单发 & 并发） | `bench_volume_destroy` |
| Volume 元数据操作（list / get_info / connect） | `bench_volume_metadata` |
| 挂载 Volume 的沙箱创建（端到端） | `bench_volume_mount_sandbox` |

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
| `--output PATH` | HTML 输出路径（默认：`perf_report.html`） |
| `--title TITLE` | 自定义 HTML 报告标题 |
| `--html-only JSON...` | 基于已有 JSON 数据文件生成 HTML（不运行压测） |
| `--compare JSON1 JSON2` | 生成两次运行的对比 HTML 报告 |

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

| 变量 | 说明 |
|---|---|
| `CUBE_TEMPLATE_ID` | 跳过自动发现 READY 模板 |
| `CUBE_SKIP_DENSITY` | 设为 `1` 跳过部署密度压测 |
| `CUBE_PERF_ROUNDS` | 每个压测场景的轮数（默认：`10`） |
| `CUBE_DENSITY_COUNT` | 密度测试的最大沙箱数量（默认：`100`） |
| `CUBE_OUTPUT_REPORT` | 输出报告的基础路径（默认：`report`） |
| `CUBE_HTML_OUTPUT` | HTML 报告输出路径（默认：`perf_report.html`） |
| `CUBE_RUN_VOLUME` | 设为 `1` 启用 Volume 场景（默认跳过） |

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

from e2e.config import resolve_config
from e2e.env import collect_env_info
from e2e import report
from perf import benchmarks
from perf.report_html import generate_html

cfg = resolve_config()
env = collect_env_info(cfg)
benchmarks.run_all(cfg)

data = report.build_report_data(env)
generate_html(["report.json"], output_path="my_report.html")
```
