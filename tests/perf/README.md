# `perf` — CubeSandbox Python SDK Performance Benchmark Suite

One command runs all scenarios, producing **JSON + Markdown** reports.

## Quick Start

```bash
cd CubeSandbox/tests
python3 -m perf                  # local backend
CUBE_API_URL=http://1.2.3.4:3000 python3 -m perf  # remote
python3 -m perf
```

## Scenarios

All scenarios are external scripts, configured via `CUBE_EXTERNAL_SCRIPTS` in `.env` (comma-separated):

```bash
# tests/perf/.env
CUBE_EXTERNAL_SCRIPTS=../examples/snapshot-rollback-clone/bench_clone_concurrency.py,\
                      ../examples/snapshot-rollback-clone/bench_create_concurrency.py
```

Or one-off via CLI:

```bash
python3 -m perf --scripts /my/dir/
```

```bash
python3 -m perf --list-scenarios    # list all registered
python3 -m perf --only clone-create # run specific scenarios
```

## Concurrency

| Variable | Default | Scope |
|----------|---------|-------|
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | All external scripts |
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | Lightweight fallback |

Per-scenario override: `CUBE_<KEY>_CONCURRENCY` (e.g. `CUBE_CLONE_CONCURRENCY=1,5,10`). See `.env.example`.

Over-resourced concurrency levels show `errors=N/total` (red) without blocking other levels.

## CLI Options

| Flag | Description |
|------|-------------|
| `--only KEY...` | Run specific scenarios |
| `--rounds N` | Rounds per scenario |
| `--list-scenarios` | List registered scenarios |
| `--scripts DIR` | Run all `.py` files in a directory |
| `--cleanup` | Delete all `snap-*` templates before run |
| `--md-only JSON` | Re-render Markdown from JSON |
## Environment Variables

### Connection

| Variable | Default |
|----------|---------|
| `CUBE_API_URL` | `http://127.0.0.1:3000` |
| `CUBE_API_KEY` | — |
| `CUBE_TEMPLATE_ID` | Auto-discover |
| `CUBE_PROXY_NODE_IP` | — |
| `CUBE_PROXY_PORT_HTTP` | `80` |
| `CUBE_SANDBOX_DOMAIN` | `cube.app` |

### Run

| Variable | Default | Description |
|----------|---------|-------------|
| `CUBE_PERF_ROUNDS` | `3` | Rounds per scenario |
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | Default concurrency ladder |
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | Lightweight fallback |
| `CUBE_PERF_WARMUP` | `1` | Warm-up rounds |
| `CUBE_PERF_SETTLE` | `0` | Cooldown between levels |
| `CUBE_PERF_CLEANUP` | `1` | Clean micro-VMs between rounds |

## Adding a Script

Framework handles scheduling + stats. Scripts define benchmark logic.

### Convention

```bash
python bench_xxx.py -c <concurrency> -n <ops> --rounds <rounds> --no-header
```

| Arg | Required | Framework behavior |
|-----|:---:|------|
| `-c N` | Yes* | Swept across concurrency levels |
| `-n N` | Yes* | Maps to `CUBE_PERF_ROUNDS` |
| `--rounds N` | No | Internal script rounds |
| `--no-header` | No | Suppress repeated header output |

_\*Scripts without `-c`/`--concurrency` in their argparse definition are
auto-detected and run once with `--no-header` only (no concurrency sweep)._

### Example

```python
# bench_clone.py
"""Clone Concurrency"""               # first line → report title

# ── Report metadata (drives Markdown table columns) ──
METRICS = ("avg", "min", "p50", "p95", "p99", "max")

REPORT = {                            # all fields optional
    "method_en": "Clone Sandbox",
    "method_zh": "克隆沙箱",
    "noun_en":    "op",
    "noun_zh":    "次",
    "throughput": True,
}

LEVELS = (1, 5, 10, 20)               # concurrency gradient (optional)

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

### Script metadata convention

`discover_external_scripts()` parses module-level variables from the script file:

| Variable | Type | Required | Description |
|----------|------|:--------:|-------------|
| `METRICS` | `tuple[str, ...]` | No | Columns in latency table (default: avg,min,p50,p95,p99,max) |
| `REPORT` | `dict` | No | Report section fields; all `ReportSection` fields accepted |
| `LEVELS` | `tuple[int, ...]` | No | Concurrency gradient, overrides global default |

`REPORT` fields (subset of `ReportSection`, all optional):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `table` | `str` | `"latency"` | Table type: `latency` or `dirty` |
| `method_en` | `str` | `""` | Operation description (English) |
| `method_zh` | `str` | `""` | Operation description (Chinese) |
| `noun_en` | `str` | `""` | Operation unit (English), e.g. `"op"` |
| `noun_zh` | `str` | `""` | Operation unit (Chinese), e.g. `"次"` |
| `throughput` | `bool` | `False` | Show throughput column |
| `star` | `bool` | `False` | Mark as starred scenario |
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

## Cleanup

Snapshots created during benchmarks are **auto-deleted after every
concurrency level** (default ON). Disable with
`CUBE_PERF_AUTO_CLEANUP=0`.

| Variable | Default | Description |
|----------|---------|-------------|
| `CUBE_PERF_AUTO_CLEANUP` | `1` | Per-level cleanup (set `0` to disable) |
| `CUBE_PERF_AUTO_CLEANUP_WAIT` | `3` | Seconds to wait before cleanup |

Manual cleanup flags:

| Flag | Description |
|------|-------------|
| `--cleanup` | Delete all `snap-*` templates before run |
| `--cleanup-dry-run` | Preview which snapshots would be deleted |
| `--cleanup-older-than DAYS` | Only delete snapshots older than N days |

[中文文档](./README.zh.md)
