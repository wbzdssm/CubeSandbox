# CubeSandbox Perf — Design & Architecture

## Overview

`tests/perf` is the benchmark harness for CubeSandbox. Its design goal is **"run once, report everywhere"** — a single command collects environment metadata, drives external benchmark scripts through a unified contract, and renders human-readable reports (JSON, Markdown, HTML) without third-party charting libraries.

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
│    html_report.py    Chart-less SVG line chart +          │
│                      collapsible details for multi-env   │
├─────────────────────────────────────────────────────────┤
│  ops/               Platform resource management         │
│    cleanup.py        Snapshot CRUD, default script        │
│                      registration, post-benchmark cleanup │
└─────────────────────────────────────────────────────────┘
```

### Layer contracts

- **framework/** has no knowledge of HTML, Markdown, or TOML. It reads `os.environ`, calls `subprocess` or the SDK, and writes `PerfResult` objects into a module-level list.
- **reporting/** depends on `framework/` data structures but not on `plugins/`. It transforms raw `PerfResult` lists into a JSON-compatible dict (`build_report_data`).
- **plugins/** depends on `reporting/` JSON schema. `html_report.py` is the only consumer today; a Slack/Mattermost adapter could reuse the same schema.
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

The collected `release_version` is placed first in the environment fingerprint so that two runs on the same machine but with different CubeSandbox releases automatically split into separate series in multi-env HTML reports.

### 4. Baseline filtering: auto mode

Published baselines (BMSA9, BMI5, Kunpeng 920, Vera) are compared against the current run. The default mode is `auto`: baselines whose scenario keys don't intersect the current run are silently dropped. This prevents empty "Kunpeng 920" bars from appearing in an x86-only report.

Configuration via `report.toml`:

```toml
[baselines]
mode = "auto"    # auto | all | none | list
```

### 5. Single-pass report generation

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

The same JSON feeds `report.py` (→ Markdown) and `html_report.py` (→ HTML). Multi-environment comparison is achieved by feeding multiple JSON files to `generate_html()` — the internal `_group_runs()` function splits by environment fingerprint and the SVG chart renders one line per fingerprint.

### 6. Post-benchmark cleanup (ops/)

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
        ┌────────────┴──────────────┐
        ▼                           ▼
   report.json / report.md    --html? → html_report.generate_html()
        │                                  │ SVG + collapsible details
        │                                  ▼
        │                           perf_report.html
        │
        └─ cleanup_after_benchmark()
              (if CUBE_PERF_AUTO_CLEANUP=1)
```

---

## Multi-Environment Report Internals

When `generate_html()` receives multiple JSON files:

1. **Grouping** (`_group_runs`) — compute `_env_fingerprint` for each file; same fingerprint → merged samples; different fingerprint → separate series.
2. **Labeling** (`_env_label`) — prefers `ip_address` over `hostname`, appends `release_version` for at-a-glance identification.
3. **Disambiguation** (`_disambiguate_labels`) — for multi-env reports, appends differing component versions to the legend label.
4. **Rendering** — the SVG chart draws one polyline per series. Data points are wrapped in `<g><title>...</title></g>` for native browser tooltips.

The HTML is **fully self-contained** — no external Chart.js CDN, no internet dependency. All CSS and JS are inlined.

---

## Extensibility

| New scenario | Add a `.py` script, set `CUBE_EXTERNAL_SCRIPTS` |
|---|---|
| New component version | `framework/env.py` → `_MANIFEST_COMPONENT_MAP` + 1 line |
| New metric column | `reporting/report.py` → `_DEFAULT_METRICS` |
| New output format | `plugins/` → new adapter consuming the `build_report_data()` schema |
| New cleanup target | `ops/cleanup.py` → new function, wire into `cleanup_after_benchmark()` |

---

## Configuration Precedence

```
CLI arguments  >  environment variables  >  report.toml  >  built-in defaults
```

`report.toml` is searched at: `$CUBE_REPORT_CONFIG` → `./report.toml` → `tests/perf/report.toml` → `tests/report.toml` → `sdk/python/report.toml`. Missing file or missing keys → fallback to defaults.
