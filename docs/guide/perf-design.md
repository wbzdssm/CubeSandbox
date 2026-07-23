# CubeSandbox Perf — Integration Contract

`tests/perf` is the CubeSandbox performance benchmark suite. This document defines the contract between benchmark scripts and the framework.

Follow this contract and scripts automatically get concurrency scheduling, metric collection, and Markdown report generation.

## Quick Start

```bash
cd CubeSandbox/tests
cp perf/.env.example perf/.env      # edit with CUBE_API_URL / CUBE_TEMPLATE_ID
python3 -m perf
```

## CLI Contract

The framework invokes scripts via `subprocess`:

```bash
python bench_xxx.py -c <concurrency> -n <operations> --rounds <rounds> --no-header
```

| Flag | Required | Description |
|------|:--------:|-------------|
| `-c N` | Yes | Concurrency level |
| `-n N` | Yes | Operations per round |
| `--rounds N` | No | Internal rounds (defaults to `-n`) |
| `--no-header` | No | Suppress repeated table headers |

Script `stdout` is displayed to the user, `stderr` goes to the log. Exit code 0 = success.

## Metadata Convention

The framework parses module-level variables from the script source. All are optional:

### METRICS

Declares statistic columns in the report table. Defaults to `avg`, `min`, `p50`, `p95`, `p99`, `max`:

```python
METRICS = ("avg", "min", "p95", "max")
```

### REPORT

Customises report table rendering:

```python
REPORT = {
    "method_en": "Create Sandbox",
    "method_zh": "创建沙箱",
    "noun_en":    "op",
    "noun_zh":    "次",
    "throughput": True,
    "table":      "latency",     # latency | dirty
    "star":       True,
}
```

Full field reference:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `table` | `str` | `"latency"` | Table type: `latency` or `dirty` |
| `method_en` | `str` | `""` | Operation description (English) |
| `method_zh` | `str` | `""` | Operation description (Chinese) |
| `noun_en` | `str` | `""` | Operation unit, e.g. `"op"` |
| `noun_zh` | `str` | `""` | Operation unit, e.g. `"次"` |
| `throughput` | `bool` | `False` | Show throughput column |
| `star` | `bool` | `False` | Mark as starred |

### LEVELS

Overrides the global concurrency gradient:

```python
LEVELS = (1, 10, 20, 50)
```

Defaults to `CUBE_PERF_CONCURRENCY` from `.env`.

## Full Example

```python
# bench_clone.py
"""Clone Concurrency"""               # first line → report title

METRICS = ("avg", "min", "p50", "p95", "p99", "max")

REPORT = {
    "method_en": "Clone Sandbox",
    "method_zh": "克隆沙箱",
    "noun_en":    "op",
    "noun_zh":    "次",
    "throughput": True,
}

LEVELS = (1, 5, 10, 20)

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
