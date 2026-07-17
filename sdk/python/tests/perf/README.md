# `perf` — Standalone Performance Benchmark Suite

[中文文档](./README.zh.md)

Performance benchmark scenarios for the CubeSandbox Python SDK, matching
the official CubeSandbox perf report. Split out of `tests/e2e/` so it can
be run and maintained independently from the functional integration tests.

## Package Layout

| Module | Responsibility |
|---|---|
| `benchmarks.py` | 11 benchmark scenarios + `run_all()` |
| `__main__.py` | CLI entry point (`python3 -m perf`) |
| `__init__.py` | sys.path bootstrap (locates `sdk/python` and the sibling `e2e` package) |

This package reuses the shared infrastructure from the sibling
[`tests/e2e/`](../e2e/README.md) package rather than duplicating it:

| Reused from `e2e` | Purpose |
|---|---|
| `e2e.config` | `resolve_config()`, `PERF_ROUNDS`, `DENSITY_COUNT` |
| `e2e.env` | `collect_env_info()`, `get_free_mem_gb()` |
| `e2e.runner` | `PERF_RESULTS`, `PerfResult`, `PerfSample`, `measure_parallel`, `percentile`, `skip` |
| `e2e.report` | Markdown + JSON report generation |

## Benchmark Scenarios

| Scenario | Function |
|---|---|
| Template-based sandbox creation (single & concurrent) | `bench_template_create` |
| Deployment density (memory overhead) | `bench_deployment_density` |
| Snapshot creation (concurrent, dirty-page scaling) | `bench_snapshot_create` |
| Snapshot-based sandbox creation (concurrent) | `bench_snapshot_create_from` |
| Rollback | `bench_rollback` |
| Clone (sequential & concurrent) | `bench_clone` |
| Pause / Resume | `bench_pause_resume` |
| Volume create (single & concurrent) | `bench_volume_create` |
| Volume destroy (single & concurrent) | `bench_volume_destroy` |
| Volume metadata ops (list / get_info / connect) | `bench_volume_metadata` |
| Sandbox creation with mounted volume (E2E) | `bench_volume_mount_sandbox` |

## Usage

Run from the `tests/` directory:

```bash
CUBE_API_URL=https://api.example.com CUBE_API_KEY=sk-... python3 -m perf
```

This runs **only** the performance benchmarks (no functional tests). To
run the full-chain suite (functional + perf) instead, use
[`tests/e2e/`](../e2e/README.md) (`python3 -m e2e` or
`python3 integration_test_full.py`), which imports this package internally
unless `CUBE_SKIP_PERF=1` is set.

### Optional environment variables

| Variable | Description |
|---|---|
| `CUBE_TEMPLATE_ID` | skip auto-discovery of a READY template |
| `CUBE_SKIP_DENSITY` | set to `1` to skip the deployment density benchmark |
| `CUBE_PERF_ROUNDS` | rounds per perf scenario (default: `10`) |
| `CUBE_DENSITY_COUNT` | max sandbox count for density test (default: `100`) |
| `CUBE_OUTPUT_REPORT` | base path for output reports (default: `report.md`) |
| `CUBE_RUN_VOLUME` | set to `1` to enable the four Volume scenarios (skipped by default — the backend `/volumes` endpoint is part of the SDK/docs-first roadmap) |

### Reports

Each run produces four report files next to the `CUBE_OUTPUT_REPORT` base
path (same format as `tests/e2e/`, since `report.py` is shared):

- `report.md` / `report.zh.md` — Markdown, English / Chinese
- `report.json` / `report.zh.json` — JSON, English / Chinese

Since only benchmarks run (no functional tests), the "Functional Test
Results" section of the report will show 0/0/0.

### Programmatic usage

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
