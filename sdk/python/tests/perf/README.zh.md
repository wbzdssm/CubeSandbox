# `perf` — 独立性能压测套件

[English](./README.md)

CubeSandbox Python SDK 的性能压测场景，与官方 CubeSandbox 性能报告保持一致。
从 `tests/e2e/` 中拆分出来，便于与功能集成测试独立运行和维护。

## 包结构

| 模块 | 职责 |
|---|---|
| `benchmarks.py` | 11 个压测场景 + `run_all()` |
| `__main__.py` | CLI 入口（`python3 -m perf`） |
| `__init__.py` | sys.path 初始化（定位 `sdk/python` 及同级 `e2e` 包） |

本包复用同级 [`tests/e2e/`](../e2e/README.zh.md) 包的共享基础设施，而不是
重复实现：

| 复用自 `e2e` | 用途 |
|---|---|
| `e2e.config` | `resolve_config()`、`PERF_ROUNDS`、`DENSITY_COUNT` |
| `e2e.env` | `collect_env_info()`、`get_free_mem_gb()` |
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

此命令**只**运行性能压测（不运行功能测试）。如果需要运行全链路套件（功能
+ 压测），请使用 [`tests/e2e/`](../e2e/README.zh.md)（`python3 -m e2e` 或
`python3 integration_test_full.py`），它会在内部导入本包，除非设置了
`CUBE_SKIP_PERF=1`。

### 可选环境变量

| 变量 | 说明 |
|---|---|
| `CUBE_TEMPLATE_ID` | 跳过自动发现 READY 模板 |
| `CUBE_SKIP_DENSITY` | 设为 `1` 跳过部署密度压测 |
| `CUBE_PERF_ROUNDS` | 每个压测场景的轮数（默认：`10`） |
| `CUBE_DENSITY_COUNT` | 密度测试的最大沙箱数量（默认：`100`） |
| `CUBE_OUTPUT_REPORT` | 输出报告的基础路径（默认：`report.md`） |
| `CUBE_RUN_VOLUME` | 设为 `1` 启用 4 个 Volume 场景（默认跳过——后端 `/volumes` 端点属于 SDK/文档先行路线图，可能尚未部署） |

### 报告

每次运行会在 `CUBE_OUTPUT_REPORT` 基础路径旁生成四份报告文件（格式与
`tests/e2e/` 完全一致，因为 `report.py` 是共享的）：

- `report.md` / `report.zh.md` — Markdown，英文 / 中文
- `report.json` / `report.zh.json` — JSON，英文 / 中文

由于只运行了压测（没有功能测试），报告中的“功能测试结果”一节会显示
0/0/0。

### 编程方式调用

```python
import sys
sys.path.insert(0, "tests")

from e2e.config import resolve_config
from e2e.env import collect_env_info
from e2e import report
from perf import benchmarks

cfg = resolve_config()
env = collect_env_info(cfg)
benchmarks.run_all(cfg)

data = report.build_report_data(env)
md_en = report.render_markdown(data, "en")
```
