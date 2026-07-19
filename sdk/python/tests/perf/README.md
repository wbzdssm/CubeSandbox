# `perf` — Standalone Performance Benchmark Suite

[中文文档](./README.zh.md)

Performance benchmark scenarios for the CubeSandbox Python SDK, matching
the official CubeSandbox perf report. Split out of `tests/e2e/` so it can
be run and maintained independently from the functional integration tests.

## Package Layout

| Module | Responsibility |
|---|---|
| `benchmarks.py` | 11 benchmark scenarios + `run_all()` + component version collection |
| `__main__.py` | CLI entry point (`python3 -m perf`) with HTML generation support |
| `report_html.py` | Self-contained interactive HTML report with baseline comparison |
| `baseline.py` | Official CubeSandbox performance baseline data (from blog) |
| `__init__.py` | sys.path bootstrap (locates `sdk/python` and the sibling `e2e` package) |

This package reuses the shared infrastructure from the sibling
[`tests/e2e/`](../e2e/README.md) package rather than duplicating it:

| Reused from `e2e` | Purpose |
|---|---|
| `e2e.config` | `resolve_config()`, `PERF_ROUNDS`, `DENSITY_COUNT` |
| `e2e.env` | `collect_env_info()`, `get_free_mem_gb()` (now includes CubeAPI version) |
| `e2e.runner` | `PERF_RESULTS`, `PerfResult`, `PerfSample`, `measure_parallel`, `percentile`, `skip` |
| `e2e.report` | Markdown + JSON report generation |

## Benchmark Scenarios

| Scenario | Function |
|---|---|
| Template-based sandbox creation (single & concurrent) | `bench_template_create` |
| Deployment density (memory overhead) | `bench_deployment_density` |
| Snapshot creation (concurrent, dirty-page scaling) | `bench_snapshot_create` |
| Snapshot latency vs dirty-page size (+ create-from) | `bench_snapshot_dirty` |
| Snapshot-based sandbox creation (concurrent) | `bench_snapshot_create_from` |
| Rollback | `bench_rollback` |
| Clone (sequential & concurrent) | `bench_clone` |
| Pause / Resume | `bench_pause_resume` |
| Volume create (single & concurrent) | `bench_volume_create` |
| Volume destroy (single & concurrent) | `bench_volume_destroy` |
| Volume metadata ops (list / get_info / connect) | `bench_volume_metadata` |
| Sandbox creation with mounted volume (E2E) | `bench_volume_mount_sandbox` |
| ivshmem shared-memory host-side mmap read/write | `bench_ivshmem` |

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
| `--output PATH` | HTML output path (default: `perf_report.html`) |
| `--title TITLE` | Custom HTML report title |
| `--html-only JSON...` | Generate HTML from existing JSON data files (skip benchmarks) |
| `--compare JSON1 JSON2` | Generate HTML comparison of two runs |

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

| Variable | Description |
|---|---|
| `CUBE_TEMPLATE_ID` | skip auto-discovery of a READY template |
| `CUBE_SKIP_DENSITY` | set to `1` to skip the deployment density benchmark |
| `CUBE_PERF_ROUNDS` | rounds per perf scenario (default: `10`) |
| `CUBE_DENSITY_COUNT` | max sandbox count for density test (default: `100`) |
| `CUBE_OUTPUT_REPORT` | base path for output reports (default: `report`) |
| `CUBE_HTML_OUTPUT` | HTML report output path (default: `perf_report.html`) |
| `CUBE_RUN_VOLUME` | set to `1` to enable Volume scenarios (skipped by default) |
| `CUBE_RUN_IVSHMEM` | set to `1` to enable the ivshmem scenario (skipped by default; needs an ivshmem-enabled template and must run on the host) |
| `CUBE_IVSHMEM_TEMPLATE_ID` | ivshmem-enabled template for the ivshmem scenario (falls back to `CUBE_TEMPLATE_ID`) |
| `CUBE_IVSHMEM_ITERATIONS` | mmap iterations for the ivshmem scenario (default: `10000`) |

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
