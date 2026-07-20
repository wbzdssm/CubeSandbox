# `perf` — Standalone Performance Benchmark Suite

[中文文档](./README.zh.md)

Performance benchmark scenarios for the CubeSandbox Python SDK, matching
the official CubeSandbox perf report. Split out of `tests/e2e/` so it can
be run and maintained independently from the functional integration tests.

## Package Layout

| Module | Responsibility |
|---|---|
| `benchmarks.py` | 13 benchmark scenarios + `@benchmark` decorator/registry + `run_all()` + component version collection |
| `__main__.py` | CLI entry point (`python3 -m perf`) with HTML generation support |
| `config.py` | Config resolution (`resolve_config()`) + runtime tunables (`PERF_ROUNDS`, `CONCURRENCY_LEVELS`, `DIRTY_SWEEP`, node cleanup, ...) |
| `env.py` | Environment info collection (host/CPU/memory/disk/template metadata, `get_free_mem_gb()`, component versions) |
| `runner.py` | Timing/stats primitives (`PerfResult`, `PerfSample`, `measure_parallel`, `percentile`, `skip`, `PERF_RESULTS`) |
| `report.py` | Markdown + JSON report generation (English & Chinese) |
| `report_html.py` | Self-contained interactive HTML report with baseline comparison |
| `report_config.py` | HTML report group/field customization (env / `report.toml` overrides) |
| `baseline.py` | Official CubeSandbox performance baseline data (from blog) |
| `ivshmem.py` | Host-side ivshmem shared-memory mmap probe |
| `__init__.py` | `sys.path` bootstrap (locates `sdk/python`) + zero-dependency `.env` loading |

This package is **self-contained** — `config`/`env`/`runner`/`report` and the rest
all live inside the `perf/` directory; it no longer depends on the sibling
`tests/e2e/` package. The two are independent CLI entry points that talk to the
same underlying SDK.

## Benchmark Scenarios

Listed in source decoration order (== run order), 13 total:

| Scenario | Function | Default |
|---|---|---|
| Template-based sandbox creation (single & concurrent) | `bench_template_create` | on |
| Deployment density (memory overhead) | `bench_deployment_density` | on, skip with `CUBE_SKIP_DENSITY=1` |
| Snapshot creation (concurrent) | `bench_snapshot_create` | on |
| Snapshot latency vs dirty-page size (+ create-from) | `bench_snapshot_dirty` | on, skip with `CUBE_SKIP_SNAPSHOT_DIRTY=1` |
| Snapshot-based sandbox creation (concurrent) | `bench_snapshot_create_from` | on |
| Rollback | `bench_rollback` | on |
| Clone (sequential & concurrent) | `bench_clone` | on |
| Pause / Resume | `bench_pause_resume` | on |
| Volume create (single & concurrent) | `bench_volume_create` | off, enable with `CUBE_RUN_VOLUME=1` |
| Volume destroy (single & concurrent) | `bench_volume_destroy` | off, enable with `CUBE_RUN_VOLUME=1` |
| Volume metadata ops (list / get_info / connect) | `bench_volume_metadata` | off, enable with `CUBE_RUN_VOLUME=1` |
| Sandbox creation with mounted volume (E2E) | `bench_volume_mount_sandbox` | off, enable with `CUBE_RUN_VOLUME=1` |
| ivshmem shared-memory host-side mmap read/write | `bench_ivshmem` | off, enable with `CUBE_RUN_IVSHMEM=1` |

## Usage

Run from the `tests/` directory:

```bash
CUBE_API_URL=https://api.example.com CUBE_API_KEY=sk-... python3 -m perf
```

### CLI Options

| Option | Description |
|---|---|
| `--html` | Generate an interactive HTML report after running benchmarks |
| `--rounds N` | Override `CUBE_PERF_ROUNDS` (default: 10) |
| `--scenarios / --only SCENARIO...` | Run only the given scenario(s) (keys or aliases, comma/space separated, `no-<key>` to exclude). See "Scenario selection" |
| `--list-scenarios` | List all available scenario keys/aliases and exit |
| `--output PATH` | HTML output path (default: `perf_report.html`) |
| `--title TITLE` | Custom HTML report title |
| `--html-only JSON...` | Generate HTML from existing JSON data files (skip benchmarks) |
| `--compare JSON1 JSON2` | Generate HTML comparison of two runs |

### Scenario selection

All scenarios run by default. Use `--scenarios`/`--only` (or the
`CUBE_PERF_SCENARIOS` env var) to run just a subset — handy for exercising a
single path such as snapshot cold-start or snapshot creation:

```bash
# Only "create sandbox from snapshot" (snapshot cold-start)
python3 -m perf --scenarios snapshot-create-from
# Equivalent via alias
python3 -m perf --only cold-start

# Only "snapshot creation"
python3 -m perf --only snapshot

# Multiple scenarios (comma or space separated)
python3 -m perf --scenarios snapshot-create,rollback

# Everything except ivshmem and the volume group
python3 -m perf --scenarios all no-ivshmem no-volume

# Via env var (CLI takes precedence)
CUBE_PERF_SCENARIOS="snapshot rollback" python3 -m perf
```

Canonical keys: `template-create`, `density`, `snapshot-create`,
`snapshot-create-from`, `snapshot-dirty`, `rollback`, `clone`, `pause-resume`,
`volume-create`, `volume-destroy`, `volume-metadata`, `volume-mount-sandbox`,
`ivshmem`.

Common aliases: `create`→`template-create`, `snapshot`→`snapshot-create`,
`cold-start`/`snapshot-cold-start`/`restore`→`snapshot-create-from`,
`dirty`→`snapshot-dirty`, `pause`/`resume`→`pause-resume`, `volume`→the four
volume scenarios. Run `python3 -m perf --list-scenarios` for the full list.

### HTML Report

The HTML report is a **self-contained, zero-dependency** page that provides:

- **Environment overview**: host, CPU, memory, disk, OS, SDK version, CubeAPI version
- **Baseline comparison**: side-by-side with [official CubeSandbox perf data](https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html)
- **Per-scenario tables**: avg / min / p50 / p95 / max / wall / per-operation
- **Bar charts**: visual latency comparison (current vs baseline)
- **Multi-run merge**: pass multiple JSON files to compare runs from different machines

### Multi-machine workflow

1. On each DevCloud machine, run:
   ```bash
   CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf
   ```
   This produces `report_YYYYMMDDTHHMMSSZ.json`.

2. Collect all JSON files and generate a merged HTML report:
   ```bash
   python3 -m perf --html-only machine1.json machine2.json machine3.json --output merged_report.html
   ```

3. To check for performance regressions, compare two runs:
   ```bash
   python3 -m perf --compare before.json after.json --output diff_report.html
   ```

### Optional environment variables

| Variable | Default | Description |
|---|---|---|
| `CUBE_TEMPLATE_ID` | auto-discover | skip auto-discovery of a READY template |
| `CUBE_PERF_SCENARIOS` | all | comma/space separated scenario keys/aliases to run (overridden by `--scenarios`) |
| `CUBE_PERF_ROUNDS` | `10` | rounds per perf scenario |
| `CUBE_PERF_CONCURRENCY` | `1,2,4` | concurrency levels swept by create/snapshot/rollback/pause scenarios |
| `CUBE_PERF_WARMUP` | `1` | warm-up rounds discarded before measured rounds (removes cold-start spikes) |
| `CUBE_PERF_SETTLE` | `0` | settle seconds slept between rounds |
| `CUBE_DIRTY_SWEEP` | `0,10,50,100,200,500,800,1024` | dirty-page write sizes (MB) swept by `snapshot-dirty` |
| `CUBE_DENSITY_COUNT` | `100` | max sandbox count for density test |
| `CUBE_SKIP_DENSITY` | — | set to `1` to skip the deployment density benchmark |
| `CUBE_SKIP_SNAPSHOT_DIRTY` | — | set to `1` to skip the `snapshot-dirty` benchmark |
| `CUBE_RUN_VOLUME` | — | set to `1` to enable the four Volume scenarios (skipped by default) |
| `CUBE_RUN_IVSHMEM` | — | set to `1` to enable the ivshmem scenario (skipped by default; needs an ivshmem-enabled template and must run on the host) |
| `CUBE_IVSHMEM_TEMPLATE_ID` | falls back to `CUBE_TEMPLATE_ID` | ivshmem-enabled template for the ivshmem scenario |
| `CUBE_IVSHMEM_ITERATIONS` | `10000` | mmap iterations for the ivshmem scenario |
| `CUBE_PERF_CLEANUP` | `1` (on) | set to `0` to disable node-local residual micro-VM cleanup between rounds |
| `CUBE_CLEANUP_CMD` | `echo y \| cubecli unsafe destroyall -f` | override the node cleanup command |
| `CUBE_OUTPUT_REPORT` | `report` | base path for output reports |
| `CUBE_HTML_OUTPUT` | `perf_report.html` | HTML report output path |

> **Node cleanup**: the SDK's `kill()` does not always reap residual sandboxes, so a
> long run eventually exhausts node resources. Before each measured round the suite
> shells out to the node-local `cubecli` to force a `destroyall` back to a clean
> cold-start state. Disable with `CUBE_PERF_CLEANUP=0` or override via `CUBE_CLEANUP_CMD`.

### Reports

Each run produces:

- `report_YYYYMMDDTHHMMSSZ.json` — JSON data (for HTML report & multi-machine merge)
- `report.md` / `report.zh.md` — Markdown, English / Chinese
- `report.json` / `report.zh.json` — JSON summary, English / Chinese
- `perf_report.html` — Interactive HTML report (with `--html` flag)

### Programmatic usage

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

report.write_reports(env)          # writes report.md / report.json (en & zh)
generate_html(["report.json"], output_path="my_report.html")
```

## Adding a benchmark (one-line decorator)

**Everything a scenario needs is co-located in the single `@benchmark(...)` line
above the function**: registration, CLI aliases, the opt-in/opt-out skip gate,
optional-dependency detection, and the HTML report chart group. Adding a
benchmark is therefore **two steps** — write the function and tag it — with **no**
need to touch a registry, an alias table, a `skip` block, or `report_html.py`.

### Where the code goes

In `benchmarks.py`, insert the new function wherever you want it to run:
**decoration order (== source order) is the canonical run order** of the suite
and the order charts appear in the HTML report. Put a generic scenario near
`bench_clone`, a volume-related one inside the volume group, etc.

Minimal skeleton:

```python
@benchmark("my-scenario")
def bench_my_scenario(cfg: Config) -> None:
    """Benchmark: one-line description."""
    print(f"\n{'='*60}")
    print(" [Perf] My Scenario")
    print(f"{'='*60}")

    for concurrency in CONCURRENCY_LEVELS:
        n = PERF_ROUNDS * concurrency

        def do_one():
            sb = Sandbox.create(cfg.template_id, timeout=120, config=cfg)
            try:
                ...  # operation under test
            finally:
                try: sb.kill()
                except Exception: pass

        # the shared runner handles concurrency scheduling + timing + stats
        result = measure_parallel(f"my-scenario-c{concurrency}", do_one,
                                  n=n, concurrency=concurrency)
        PERF_RESULTS.append(result)
```

The body has only two hard rules:

1. **Results must be `PERF_RESULTS.append(result)`** (`result` from
   `measure_parallel`, or a hand-built
   `PerfResult(scenario=..., samples=[PerfSample(...)])`). Only entries in
   `PERF_RESULTS` are picked up by the Markdown / JSON / HTML reports.
2. **Name the scenario (`PerfResult.scenario`) `<key>-c<concurrency>`**, e.g.
   `my-scenario-c4`. Charts aggregate data points by this prefix
   (`<prefix>-c<N>`).

For working references: concurrent sampling → `bench_template_create`;
single-sandbox serial + hand-built `PerfResult` → `bench_snapshot_create`;
a single non-latency metric → `bench_deployment_density`.

### `@benchmark` parameters

`key` is the canonical scenario key (used for CLI selection); everything else is
an optional keyword argument:

| Parameter | Type | Purpose |
|---|---|---|
| `key` (positional) | `str` | Canonical scenario key; selected via `--scenarios <key>`, default chart prefix |
| `aliases` | `list[str]` | Friendly aliases; several benchmarks may share one (e.g. all `volume-*` under `volume`) |
| `opt_in_env` | `str` | **Default-off**: runs only when this env var `=1`, otherwise skipped (volume / ivshmem) |
| `opt_out_env` | `str` | **Default-on**: skipped when this env var `=1` (density / snapshot-dirty) |
| `skip_reason` | `str` | Extra human hint appended to the opt-in skip message |
| `available` | `bool` | Evaluated at import time; `False` skips unconditionally (e.g. `Volume is not None`) |
| `report` | `ReportGroup \| list[ReportGroup]` | Contributes the HTML report's chart + summary-table group(s) |

The decorator only wraps the function when a gate is declared
(`opt_in_env` / `opt_out_env` / `available=False`); plain scenarios call the
original function directly at zero cost.

### Producing a chart (`ReportGroup`)

Pass a `ReportGroup` to `report=` to emit a bar chart + summary table in the
HTML report:

```python
@benchmark("my-scenario", report=ReportGroup("My Scenario Title"))
def bench_my_scenario(cfg: Config) -> None: ...
```

`ReportGroup` fields: `title` (chart title, required), `prefix` (match prefix,
defaults to `key`), `x_key` (default `"c"`), `x_label` (default `"并发数"`),
`fallback` (fallback x-axis when no data, default `(1, 2, 4)`). The chart
matches `PERF_RESULTS` scenario names via `<prefix>-<x_key><N>`.

A single benchmark may declare **multiple** groups — e.g. pause/resume feeds a
Pause chart and a Resume chart from one function:

```python
@benchmark("pause-resume", aliases=["pause", "resume"],
           report=[ReportGroup("暂停（Pause）", prefix="pause"),
                   ReportGroup("恢复（Resume）", prefix="resume")])
def bench_pause_resume(cfg: Config) -> None: ...
```

Scenarios without `report=` only appear in the Markdown / JSON summaries (no
HTML chart) — e.g. `density`, `clone`.

### Common recipes

```python
# 1) default-on scenario that also renders a chart
@benchmark("my-scenario", report=ReportGroup("My Scenario"))

# 2) default-off, requires env=1 (external dependency not ready yet)
@benchmark("my-scenario", opt_in_env="CUBE_RUN_MINE",
           skip_reason="backend endpoint not available yet")

# 3) default-on, set env=1 to skip temporarily
@benchmark("my-scenario", opt_out_env="CUBE_SKIP_MINE")

# 4) depends on an optional SDK type; skip unconditionally if missing
@benchmark("my-scenario", available=MyOptionalType is not None)
```

Once decorated, `--list-scenarios`, `--scenarios <key>`, `no-<key>` exclusion,
the `all` selector, and HTML chart grouping pick up the new benchmark
**automatically** — nothing else to change.
