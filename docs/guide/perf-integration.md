# Perf Script Integration Contract

This document defines the contract between benchmark scripts and the `tests/perf` framework. Follow this contract and your scripts automatically get concurrency scheduling, metric collection, and report generation.

## CLI Contract

The framework invokes scripts via `subprocess` with these arguments:

```bash
python bench_xxx.py -c <concurrency> -n <operations> --rounds <rounds> --no-header
```

| Flag | Required | Description |
|------|:--------:|-------------|
| `-c N` | Yes | Concurrency level |
| `-n N` | Yes | Operations per round |
| `--rounds N` | No | Internal rounds (defaults to `-n`) |
| `--no-header` | No | Suppress repeated table headers |

Script `stdout` is displayed to the user; `stderr` goes to the log. Exit code 0 = success, non-zero = failure.

## Metadata Convention

The framework parses module-level variables from the script source. All are optional:

### METRICS

Declares which statistic columns appear in the report table. Defaults to `avg`, `min`, `p50`, `p95`, `p99`, `max` if unset:

```python
METRICS = ("avg", "min", "p95", "max")
```

### REPORT

Customises how the report table is rendered. All fields are optional:

```python
REPORT = {
    "method_en": "Create Sandbox",
    "method_zh": "创建沙箱",
    "noun_en":    "op",
    "noun_zh":    "次",
    "throughput": True,          # show throughput column
    "table":      "latency",     # table type: latency | dirty
    "star":       True,          # mark as starred scenario
}
```

Full field reference (`ReportSection`):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `table` | `str` | `"latency"` | Table type: `latency` or `dirty` |
| `method_en` | `str` | `""` | Operation description (English) |
| `method_zh` | `str` | `""` | Operation description (Chinese) |
| `noun_en` | `str` | `""` | Operation unit (English), e.g. `"op"` |
| `noun_zh` | `str` | `""` | Operation unit (Chinese), e.g. `"次"` |
| `throughput` | `bool` | `False` | Show throughput column |
| `star` | `bool` | `False` | Mark as starred scenario |

### LEVELS

Overrides the global concurrency gradient:

```python
LEVELS = (1, 10, 20, 50)
```

Defaults to `CUBE_PERF_CONCURRENCY` from `.env` (`1,5,10`).

## Full Example

```python
# bench_clone.py
"""Clone Concurrency"""               # first line → report title

# ── Report metadata (all optional) ──
METRICS = ("avg", "min", "p50", "p95", "p99", "max")

REPORT = {
    "method_en": "Clone Sandbox",
    "method_zh": "克隆沙箱",
    "noun_en":    "op",
    "noun_zh":    "次",
    "throughput": True,
}

LEVELS = (1, 5, 10, 20)

# ── CLI contract (required) ──
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

## Registering Scripts

Set `CUBE_EXTERNAL_SCRIPTS` in `tests/perf/.env`. Supports glob patterns:

```bash
CUBE_EXTERNAL_SCRIPTS=../examples/snapshot-rollback-clone/bench_*.py
```

The framework then:
1. Lists scenarios via `--list-scenarios`
2. Schedules concurrency gradients from `LEVELS`
3. Collects latency metrics and generates Markdown reports

## Data Flow

```
Script output → subprocess timing (wall time) → PerfResult
                                                  ↓
                                    METRICS / REPORT metadata
                                                  ↓
                               _latency_table() → Markdown headers
```
