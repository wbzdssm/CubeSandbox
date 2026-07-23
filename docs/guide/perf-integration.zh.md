# Perf 脚本集成契约

本文档定义压测脚本与 `tests/perf` 框架之间的约定。遵循此契约即可将任意外部脚本接入框架，自动获得并发调度、指标采集和报告生成。

## CLI 契约

框架通过 `subprocess` 调用脚本，传递以下参数：

```bash
python bench_xxx.py -c <并发度> -n <操作数> --rounds <轮数> --no-header
```

| 参数 | 必选 | 说明 |
|------|:---:|------|
| `-c N` | 是 | 并发度，框架按 `LEVELS` 或全局阶梯逐一调用 |
| `-n N` | 是 | 每轮操作数 |
| `--rounds N` | 否 | 脚本内部轮数（默认同 `-n`） |
| `--no-header` | 否 | 抑制重复表头 |

脚本的 `stdout` 会展示给用户，`stderr` 会被日志输出。脚本退出码为 0 代表成功，非 0 代表失败。

## 元数据约定

框架通过解析脚本源码中的**模块级变量**来获取报告元数据。以下变量均为可选：

### METRICS

声明报告表格的指标列。未声明时使用框架默认列集 (`avg`, `min`, `p50`, `p95`, `p99`, `max`)：

```python
METRICS = ("avg", "min", "p95", "max")
```

### REPORT

声明报告表格的展示方式，所有字段均为可选：

```python
REPORT = {
    "method_zh": "创建沙箱",     # 操作方法中文名
    "method_en": "Create Sandbox",  # operation description (English)
    "noun_zh":    "次",          # 计量单位中文
    "noun_en":    "op",          # unit (English)
    "throughput": True,          # 显示吞吐量列
    "table":      "latency",     # 表格类型: latency | dirty
    "star":       True,          # 标记为星标场景
}
```

全部支持的字段（`ReportSection` 全量）：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `table` | `str` | `"latency"` | 表格类型：`latency`（延迟分布）或 `dirty`（脏页表格） |
| `method_zh` | `str` | `""` | 操作方法中文描述，如 `"创建沙箱"` |
| `method_en` | `str` | `""` | 操作方法英文描述，如 `"Create Sandbox"` |
| `noun_zh` | `str` | `""` | 操作计量单位中文，如 `"次"` |
| `noun_en` | `str` | `""` | 操作计量单位英文，如 `"op"` |
| `throughput` | `bool` | `False` | 是否显示吞吐量列（`个/s`） |
| `star` | `bool` | `False` | 是否标记为星标场景 |

### LEVELS

覆盖全局并发度阶梯：

```python
LEVELS = (1, 10, 20, 50)
```

未声明时使用 `.env` 中的 `CUBE_PERF_CONCURRENCY`（默认 `1,5,10`）。

## 完整示例

```python
# bench_clone.py
"""Clone Concurrency"""               # 首行 → 报告标题

# ── 报告元数据（均为可选）──
METRICS = ("avg", "min", "p50", "p95", "p99", "max")

REPORT = {
    "method_zh": "克隆沙箱",
    "method_en": "Clone Sandbox",
    "noun_zh":    "次",
    "noun_en":    "op",
    "throughput": True,
}

LEVELS = (1, 5, 10, 20)

# ── CLI 契约（必选）──
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

## 注册脚本

在 `tests/perf/.env` 中通过 `CUBE_EXTERNAL_SCRIPTS` 注册，支持 glob pattern：

```bash
CUBE_EXTERNAL_SCRIPTS=../examples/snapshot-rollback-clone/bench_*.py
```

注册后框架自动：
1. `--list-scenarios` 列出场景
2. 执行时按 `LEVELS` 调度并发度
3. 采集延迟指标并写入 Markdown 报告

## 数据流

```
脚本输出 → subprocess 计时 (wall time) → PerfResult
                                                  ↓
                                    METRICS / REPORT 元数据
                                                  ↓
                               _latency_table() 动态生成表头 → Markdown
```
