# CubeSandbox Perf — Design & Architecture

## Overview

`tests/perf` is the benchmark harness for CubeSandbox. Its design goal is **"run once, report everywhere"** — a single command collects environment metadata, drives external benchmark scripts through a unified contract, and renders human-readable reports (JSON, Markdown) without third-party charting libraries.

### Design constraints

| Constraint | Rationale |
|---|---|
| Zero runtime JS dependencies | Reports render in air-gapped environments |
| Python 3.11+ for the harness, scripts run on any Python | Minimize CI setup |
| External scripts as the sole source of workload | Framework-agnostic; teams author benchmarks in their own repos |
| Single-pass data collection | Environment + benchmarks + cleanup in one run — no manual merge step |

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────┐
│  __main__.py        CLI orchestration                   │
│  __init__.py        .env lifecycle, sys.path bootstrap  │
├─────────────────────────────────────────────────────────┤
│  framework/         Core engine (no I/O beyond subprocess│
│                     & SDK calls)                         │
│    config.py        Env-driven runtime parameters        │
│    env.py           Hardware + OS + Cube version scan    │
│    registry.py       @benchmark decorator, script        │
│                     discovery, scenario lifecycle        │
│    runner.py          PerfResult/Sample, measure_parallel │
├─────────────────────────────────────────────────────────┤
│  reporting/         Data assembly & display config       │
│    report.py         build_report_data → JSON + Markdown │
│    report_config.py  TOML + env display layer            │
├─────────────────────────────────────────────────────────┤
│  plugins/           Lazy-loaded output adapters           │
│    html_report.py   (reserved, not currently active)      │
├─────────────────────────────────────────────────────────┤
│  ops/               Platform resource management         │
│    cleanup.py        Snapshot CRUD, default script        │
│                      registration, post-benchmark cleanup │
└─────────────────────────────────────────────────────────┘
```

### Layer contracts

- **framework/** has no knowledge of Markdown or TOML. It reads `os.environ`, calls `subprocess` or the SDK, and writes `PerfResult` objects into a module-level list.
- **reporting/** depends on `framework/` data structures but not on `plugins/`. It transforms raw `PerfResult` lists into a JSON-compatible dict (`build_report_data`).
- **plugins/** depends on `reporting/` JSON schema. Currently inactive; a future HTML or Slack adapter could reuse the same schema.
- **ops/** depends on the SDK (`cubesandbox`) but not on `framework/` or `reporting/`. It is called from `__main__.py` to clean up after benchmarks.

---

## Key Design Decisions

### 1. External-script contract

Instead of writing benchmark logic inside the framework, the harness delegates to external Python scripts following a CLI contract:

```bash
python bench_xxx.py -c <concurrency> -n <n-operations> [--rounds N] [--no-header]
```

| Motivation | Detail |
|---|---|
| Framework-agnostic | Teams can write benchmarks in any language so long as they accept the contract |
| Discovery via file system | `CUBE_EXTERNAL_SCRIPTS` env var or `--scripts DIR` — no config file needed |
| Wall-clock measurement | Each invocation is run once; the framework records total wall time via `subprocess` |

### 2. Concurrency-gradients as env vars

Every scenario accepts a concurrency gradient (e.g. `1,10,20,50`) and the framework calls the script once per level. Gradients can be overridden per scenario:

```
CUBE_CREATE_CONCURRENCY=1,10,20,50      # global default
CUBE_CLONE_CONCURRENCY=1,5,10           # per-scenario override
```

The override is resolved by `framework/registry.py` at registration time — the script itself is unaware of the gradient.

### 3. Version collection: release-manifest as ground truth

CubeSandbox installations ship `/usr/local/services/cubetoolbox/release-manifest.json`, a JSON file consumed by both `cubemaster` and `cubelet`. `framework/env.py` uses it as the authoritative version source, with two fallbacks:

1. **Primary** → `release-manifest.json` (all component versions, guest image, kernel digests)
2. **Fallback** → CubeAPI `/cluster/versions` (running-state view, **camelCase** fields — the old code used snake_case and silently returned empty strings)
3. **Last resort** → local binaries (`cube-api -V`, `cubemaster -v`, ...)

The collected `release_version` is placed first in the environment fingerprint so that two runs on the same machine but with different CubeSandbox releases automatically split into separate series in multi-env reports.

### 4. Single-pass report generation

A single call to `build_report_data()` produces a unified JSON blob:

```json
{
  "generated_at": "ISO8601",
  "environment": { /* hardware, OS, all component versions */ },
  "config": { /* resolved runtime parameters */ },
  "functional": { /* pass/fail/skip counts */ },
  "perf": [ /* array of PerfResult dicts */ ]
}
```

The same JSON feeds `report.py` (→ Markdown). The `plugins/` directory is reserved for future output adapters (HTML, Slack, etc.).

### 5. Post-benchmark cleanup (ops/)

`ops/cleanup.py` provides three functions called from `__main__.py`:

- `register_default_scripts()` — registers built-in benchmarks when `CUBE_EXTERNAL_SCRIPTS` is unset
- `list_snapshots()` / `delete_snapshots()` — snapshot CRUD via SDK, **only touches `snap-*` IDs**
- `cleanup_after_benchmark()` — opt-in via `CUBE_PERF_AUTO_CLEANUP=1`, deletes residual snapshots after benchmarks complete

---

## Data Flow (single run)

```
                    config.py reads env vars
                         │
                    collect_env_info(cfg)
                         │ EnvInfo (80+ fields)
                         ▼
             ┌─ register_default_scripts()
             │       registry discovers external .py scripts
             │
             └─ registry.run_all(cfg, selected=...)
                     │
                     ├─ for each external script:
                     │     for c in CONCURRENCY_GRADIENT:
                     │       subprocess: bench_xxx.py -c <c> -n <n>
                     │       record wall time → PerfResult → PERF_RESULTS[]
                     │
                     ▼
               build_report_data(env)
                     │ {generated_at, environment, config, functional, perf}
                     ▼
        ┌────────────┬──────────────┐
        ▼                            ▼
   report.json                  report.md
        │
        └─ cleanup_after_benchmark()
              (if CUBE_PERF_AUTO_CLEANUP=1)
```

---

---

## Extensibility

## Extensibility

| New scenario | Add a `.py` script, set `CUBE_EXTERNAL_SCRIPTS` |
|---|---|
| New component version | `framework/env.py` → `_MANIFEST_COMPONENT_MAP` + 1 line |
| New metric column | `reporting/report.py` → `_DEFAULT_METRICS` |
| New output format | `plugins/` → new adapter consuming the `build_report_data()` schema |
| New cleanup target | `ops/cleanup.py` → new function, wire into `cleanup_after_benchmark()` |

---

## Quick Start: Add a New Scenario

Three steps to add a new benchmark scenario.

### Step 1 — Write the script

Create a `.py` file that accepts `-c` (concurrency) and `-n` (operations). Declare table columns via module-level `METRICS` and `REPORT` variables.

```python
# bench_my_scenario.py
"""My scenario benchmark."""              # ← first line = report title

# ── Report metadata (drives Markdown table columns) ──
METRICS = ("avg", "min", "p50", "p95", "p99", "max")

REPORT = {                                # all fields optional
    "method_en": "My Operation",
    "method_zh": "我的操作",
    "noun_en":    "op",
    "noun_zh":    "次",
    "throughput": True,
    "table":      "latency",
}

LEVELS = (1, 10, 20, 50)                  # concurrency gradient (optional)

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
for _ in range(args.n):
    sb.do_something(concurrency=args.c)
sb.kill()

print(f"n={args.n}, c={args.c}")   # optional stdout for debugging
```

### Step 2 — Register in `.env`

Add the script path to `CUBE_EXTERNAL_SCRIPTS` in `tests/perf/.env`:

```bash
CUBE_EXTERNAL_SCRIPTS=\
../examples/snapshot-rollback-clone/bench_clone_concurrency.py,\
../examples/my-new-feature/bench_my_scenario.py
```

### Step 3 — Run

```bash
# List to verify registration
python3 -m perf --list-scenarios

# Run just the new scenario
python3 -m perf --rounds 1 --scenarios my-scenario
```

### What the framework does automatically

| Step | Framework handles |
|------|------------------|
| Concurrency gradient | Calls script once per level in `CUBE_PERF_CONCURRENCY` |
| Warm-up | First N rounds discarded (`CUBE_PERF_WARMUP`) |
| Timing | Wall-clock measured per invocation |
| Metrics | avg / min / p50 / p95 / p99 / max computed |
| Report | Markdown table generated |
| Cleanup | Residual sandboxes & snapshots cleaned up |

The script author only writes the benchmark logic — no timing, no stats, no report formatting.

### CLI contract reference

| Flag | Required | Description |
|------|:--------:|-------------|
| `-c N` | Yes | Concurrency level |
| `-n N` | Yes | Operations per round |
| `--rounds N` | No | Internal rounds (defaults to `-n`) |
| `--no-header` | No | Suppress repeated table headers |

---

## Configuration Precedence

```
CLI arguments  >  environment variables  >  report.toml  >  built-in defaults
```

`report.toml` is searched at: `$CUBE_REPORT_CONFIG` → `./report.toml` → `tests/perf/report.toml` → `tests/report.toml` → `sdk/python/report.toml`. Missing file or missing keys → fallback to defaults.
