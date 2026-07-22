# `perf` — CubeSandbox Python SDK Performance Benchmark Suite

[中文文档](./README.zh.md) · Architecture & implementation details in [DESIGN.zh.md](./DESIGN.zh.md)

One script, one command: run every performance scenario and produce a
**JSON + Markdown** report. Every sandbox / snapshot / volume is **destroyed
right after use** — no leftovers. Self-contained, zero third-party dependencies
(besides the SDK itself).

> The HTML report is an optional extra — see "HTML report (optional)" at the end.
> The default path only produces JSON + Markdown.

## Quick start

Run from the `tests/` directory:

```bash
# Run all scenarios, produce JSON + Markdown
CUBE_API_URL=https://api.example.com CUBE_API_KEY=sk-... python3 -m perf
```

Not specifying scenarios means **run everything**. When `CUBE_TEMPLATE_ID` is
unset, a READY template is auto-discovered.

## Selecting which scenarios to run

```bash
# Run all default-on scenarios (default behavior, no args needed)
python3 -m perf

# Run only a subset (keys or aliases, comma/space separated; --only == --scenarios)
python3 -m perf --only snapshot rollback
python3 -m perf --scenarios snapshot-create-from

# Run all default-on scenarios, then exclude a few (exclusion only makes sense for default-on)
python3 -m perf --scenarios all no-density no-clone

# Explicitly name a default-off scenario — auto-enables it, no opt-in env var needed
python3 -m perf --only ivshmem
python3 -m perf --only volume

# List all scenario keys / aliases and exit (no benchmarks, no backend)
python3 -m perf --list-scenarios
```

**Selection syntax**

| Form | Meaning |
|---|---|
| `--only` / `--scenarios` | Fully equivalent; take one or more "canonical keys" or "aliases", comma or space separated |
| `all` | Wildcard for all scenarios (equivalent to passing no args; still bound by default gates, see note below) |
| `no-<key/alias>` | Exclude a scenario; `skip-` / `!` / `^` prefixes are equivalent (e.g. `no-clone`, `skip-density`, `!snapshot`) |

> ⚠️ **Exclusion only affects default-on scenarios.** `all` is a "wildcard", not
> "explicitly naming each scenario", so it does **not** auto-enable default-off
> scenarios (`volume` / `ivshmem`) — writing `no-ivshmem` for them is a no-op.
> To run a default-off scenario, use `--only ivshmem` to explicitly name it (auto-enables),
> or set the corresponding opt-in env var (see "Scenario reference" below).

All canonical keys, aliases and default gates are in [Scenario reference](#scenario-reference) below.

### Freezing toggles in `.env` (avoid retyping the command)

Scenario toggles are all `CUBE_*` environment variables, and the suite
**auto-loads `.env`** on startup (zero-dependency, see `__init__.py`). So you can
write "which to run / which to enable / which to disable" into `.env` once, then
just run `python3 -m perf`. Real environment variables and CLI args always win;
`.env` only fills the gaps.

**Auto-scaffold + write-back**: if no `.env` is found on startup, the suite
generates a **minimal `.env`** under `tests/perf/` containing only the *data-flow
(connection) variables* — `CUBE_API_URL`, `CUBE_API_KEY`, `CUBE_TEMPLATE_ID`,
`CUBE_PROXY_NODE_IP`, `CUBE_PROXY_PORT_HTTP`, `CUBE_SANDBOX_DOMAIN`; no scenario/
run knobs are written (defaults apply). **Against a local backend (the default
`http://127.0.0.1:3000`) you need to fill nothing** — just run; only set
`CUBE_API_URL` (and `CUBE_API_KEY`) to hit a remote deployment. Leave
`CUBE_TEMPLATE_ID` empty to auto-discover a READY one. After each run, the
data-flow values the run actually used (**including an auto-discovered template
id**) are **written back** to that `.env`, so the 2nd/3rd run just needs
`python3 -m perf` — no re-exporting. An existing `.env` is only updated in place
for those keys; every other line (your comments, scenario toggles) is preserved.
To tune scenarios/run params, copy the relevant lines from `.env.example` into
your `.env`.

**Run tunables are written back too**: if you export a run tunable (concurrency
ladders, rounds, density count, warmup/settle, dirty sweep, cleanup — see
`CUBE_*` vars under "Run parameters" in `.env.example`) for a run, it's written
back the same way, so a value you dial down once (e.g. to dodge a CubeMaster
"no more resource" error on a small node) sticks for later runs too — no need to
re-export or hand-edit `.env`.

```bash
# Local backend (defaults to http://127.0.0.1:3000): nothing to set, just run.
python3 -m perf

# Remote backend: set CUBE_API_URL (and CUBE_API_KEY); they get written back to .env.
CUBE_API_URL=https://api.example.com CUBE_API_KEY=sk-... python3 -m perf

# Small node hitting "no more resource"? Trim the concurrency ladders once —
# they're written back to .env, so later runs keep using the smaller ladder.
CUBE_CREATE_CONCURRENCY=1,3,5 CUBE_PERF_CONCURRENCY=1,3,5 python3 -m perf

# To freeze scenario toggles, edit tests/perf/.env (see .env.example), e.g.:
#   CUBE_PERF_SCENARIOS=snapshot rollback   # run only these two (== --only)
#   CUBE_RUN_IVSHMEM=1                        # enable default-off ivshmem
#   CUBE_SKIP_DENSITY=1                       # skip default-on density
python3 -m perf
```

| Goal | Write in `.env` | Equivalent CLI |
|---|---|---|
| Run only a few | `CUBE_PERF_SCENARIOS=snapshot rollback` | `--only snapshot rollback` |
| Run all, then exclude | `CUBE_PERF_SCENARIOS=all no-density` | `--scenarios all no-density` |
| Enable Volume (default off) | `CUBE_RUN_VOLUME=1` | `--only volume` |
| Enable ivshmem (default off) | `CUBE_RUN_IVSHMEM=1` | `--only ivshmem` |
| Disable density (default on) | `CUBE_SKIP_DENSITY=1` | `--scenarios all no-density` |
| Disable snapshot-dirty (default on) | `CUBE_SKIP_SNAPSHOT_DIRTY=1` | `--scenarios all no-snapshot-dirty` |

> `.env` lookup order (nearest first): current dir → `tests/perf/` → `tests/` →
> `sdk/python/`; or use `CUBE_DOTENV=/path/to/other.env` to point at a specific file.
> `.env` is `.gitignore`d — don't commit secrets.

## Resource cleanup (destroy right after use)

The suite guarantees **every created resource is destroyed immediately after
use**, without relying on manual reclamation:

- Spin up a temporary sandbox for one thing → `sandbox.kill()` on exit (`sandbox` fixture).
- Need a sandbox + snapshot → on exit, `delete_snapshot` then `kill` (`snapshot` fixture).
- Creation itself is the operation under test (`template-create` / `density` / `clone` / `volume`) → collected in a resource pool, batch-destroyed at the end of each level.
- Cleanup is always **best-effort** (swallows exceptions); a single destroy failure never aborts the run.

> Node residual backstop: the SDK's `kill()` doesn't always reap residual
> micro-VMs, so a long run gradually exhausts node resources. Before each timed
> round the suite shells out to the node-local `cubecli` to force `destroyall`,
> returning to a clean cold-start state. Disable with `CUBE_PERF_CLEANUP=0`, or
> override the command via `CUBE_CLEANUP_CMD`.

## Output artifacts

A default run writes **1 raw data file + 4 reports**, all under the base path
given by `CUBE_OUTPUT_REPORT` (default `report`):

| File | Format | Content |
|---|---|---|
| `report_<timestamp>.json` | JSON | **Raw data snapshot** of this run (structure below), for re-render / multi-machine merge / HTML |
| `report.md` | Markdown | Full report (English), paste straight into a PR / Wiki |
| `report.zh.md` | Markdown | Full report (Chinese) |
| `report.json` | JSON | Report summary (English); adds `language` / `overall_status` over the raw data |
| `report.zh.json` | JSON | Report summary (Chinese) |

### What's in the JSON

The raw data is a single object with four top-level blocks:

| Key | Description |
|---|---|
| `generated_at` | UTC generation timestamp |
| `environment` | Full test-environment fingerprint: hostname / CPU / memory / disk, template spec, plus CubeAPI / CubeMaster / Cubelet / CubeShim / Guest Image / kernel / SDK component versions |
| `config` | This run's params: `perf_rounds` (rounds per scenario), `density_max_count` (density cap) |
| `perf` | Array of per-scenario per-concurrency results; per-item fields below |

Each `perf` array item maps to one `<key>-c<concurrency>` level:

| Field | Meaning |
|---|---|
| `scenario` | Scenario name (with concurrency suffix, e.g. `snapshot-create-c4`) |
| `count` / `concurrency` | Sample count / concurrency degree |
| `avg_ms` / `min_ms` / `p50_ms` / `p95_ms` / `p99_ms` / `max_ms` | Per-operation latency distribution (ms) |
| `wall_ms` / `per_ms` | Whole-batch wall time / amortized per-operation time |
| `raw_latencies` | Raw per-sample latency array (for scatter plots / re-computing stats) |
| `extra` | Scenario-specific extras (e.g. dirty-page `write_mb`, density `baseline_gb` / `final_free_gb`) |

### What's in the Markdown

`report.md` / `report.zh.md` are directly readable full reports with three parts:

1. **Test environment** — hardware info, sandbox spec & template, component versions, test config.
2. **Performance benchmarks** — one section per scenario: template-based creation
   (with throughput column), deployment density (per-VM memory amortization),
   snapshot creation, snapshot latency vs dirty-page size (two sub-tables: snapshot
   creation + snapshot-based restore), snapshot-based startup, rollback, clone,
   pause / resume. Each section's **title, test-method note, table type, throughput
   column and conclusion wording** are declared in that scenario's
   `@benchmark(report=ReportSection(...))` in `bench_*.py`, and rendered in `order`
   field order (see "Adding a scenario" below).
3. **Summary** — scenarios collected, rounds per scenario, success rate.

### Re-render without a backend

The raw JSON is the complete input to report rendering, so you can **regenerate
Markdown + JSON summaries from existing JSON** without a backend:

```bash
python3 -m perf --md-only report_20260720T120000Z.json
```

## Scenario reference

The table below lists all **13 scenarios** in **run order** (== registration
order == `--list-scenarios` output order, determined by the sorted paths of
modules under `cases/`). Scenarios with default "on" run without any config;
"off" ones need `--only <key>` to be explicitly named (auto-enables) or the
corresponding env var to run.

| Canonical key | Aliases | Default | Operation under test |
|---|---|:---:|---|
| `clone` | — | on | `clone` N new sandboxes from a running one (sequential & concurrent) |
| `ivshmem` | — | **off** | Host-side `mmap` read/write against ivshmem shared memory |
| `template-create` | `create` | on | Template-based sandbox cold start (single & concurrent, report includes throughput) |
| `density` | — | on | Deployment density: accumulate sandboxes, measure per-VM memory overhead |
| `pause-resume` | `pause`, `resume` | on | Concurrent `pause` (flush to disk) + `resume` |
| `snapshot-create` | `snapshot` | on | Concurrently snapshot a running sandbox |
| `snapshot-create-from` | `snapshot-cold-start`, `cold-start`, `coldstart`, `restore` | on | Concurrently create sandboxes from a snapshot (cold-start restore) |
| `snapshot-dirty` | `dirty` | on | Snapshot latency vs dirty-page size (0~1024 MB sweep, incl. create-from restore sub-table) |
| `rollback` | — | on | In-place `rollback` of a running sandbox to a snapshot |
| `volume-create` | `volume` | **off** | Create Volume (single & concurrent) |
| `volume-destroy` | `volume` | **off** | Destroy Volume (single & concurrent) |
| `volume-metadata` | `volume` | **off** | Volume metadata ops: `list` / `get_info` / `connect` |
| `volume-mount-sandbox` | `volume` | **off** | End-to-end sandbox creation with a mounted Volume |

> The `volume` alias selects all 4 `volume-*` scenarios at once; `snapshot` points
> only to `snapshot-create` (the other snapshot scenarios each have their own keys / aliases).

### Default-off scenarios & how to enable

| Scenario | How to enable (either) | Extra requirements |
|---|---|---|
| `ivshmem` | `--only ivshmem`, or `CUBE_RUN_IVSHMEM=1` | Needs an ivshmem template (`CUBE_IVSHMEM_TEMPLATE_ID`, falls back to `CUBE_TEMPLATE_ID`) + running on the node host |
| 4 `volume-*` | `--only volume` (selects all 4 at once), or `CUBE_RUN_VOLUME=1` | Backend `/volumes` endpoint available + SDK ships the `Volume` type |

### Default-on scenarios that can be disabled

| Scenario | How to disable (either) |
|---|---|
| `density` | `CUBE_SKIP_DENSITY=1`, or `--scenarios all no-density` |
| `snapshot-dirty` | `CUBE_SKIP_SNAPSHOT_DIRTY=1`, or `--scenarios all no-snapshot-dirty` (alias `no-dirty`) |

## CLI options

### Basics

| Option | Description |
|---|---|
| `--scenarios / --only SCENARIO...` | Run only the given scenarios; keys or aliases, comma/space separated, `no-`/`skip-`/`!`/`^` prefix to exclude. Explicitly naming a default-off scenario auto-enables it. See "Scenario reference" |
| `--rounds N` | Override `CUBE_PERF_ROUNDS` (default 10) |
| `--list-scenarios` | List all scenario keys/aliases and exit |
| `--md-only JSON` | Re-render Markdown + JSON from existing JSON (no benchmarks, no backend) |

### HTML (optional, see end)

| Option | Description |
|---|---|
| `--html` | Also generate an interactive HTML report after the run |
| `--html-only JSON...` | Generate HTML from existing JSON (no benchmarks) |
| `--compare JSON1 JSON2` | Generate a comparison HTML from multiple JSON files |
| `--output PATH` | HTML output path (default `perf_report.html`) |
| `--title TITLE` | Custom HTML report title |

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CUBE_TEMPLATE_ID` | auto-discover | Skip READY template auto-discovery when set |
| `CUBE_PERF_SCENARIOS` | all | Scenario keys/aliases, comma or space separated (overridden by `--scenarios`) |
| `CUBE_PERF_ROUNDS` | `10` | Rounds per scenario |
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | Concurrency sweep levels for light scenarios (snapshot-create / rollback / pause-resume) |
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | Concurrency sweep levels for heavy scenarios (template-create / create-from-snapshot / clone) |
| `CUBE_PERF_WARMUP` | `1` | Warm-up rounds discarded before timing |
| `CUBE_PERF_SETTLE` | `0` | Settle seconds between concurrency levels |
| `CUBE_DIRTY_SWEEP` | `0,10,...,1024` | `snapshot-dirty` dirty-page write MB levels |
| `CUBE_DENSITY_COUNT` | `100` | Max sandbox count for density test |
| `CUBE_SKIP_DENSITY` | — | `1` to skip deployment density |
| `CUBE_SKIP_SNAPSHOT_DIRTY` | — | `1` to skip `snapshot-dirty` |
| `CUBE_RUN_VOLUME` | — | `1` to enable the 4 Volume scenarios |
| `CUBE_RUN_IVSHMEM` | — | `1` to enable the ivshmem scenario (needs host + ivshmem template) |
| `CUBE_IVSHMEM_TEMPLATE_ID` | falls back to `CUBE_TEMPLATE_ID` | ivshmem-dedicated template |
| `CUBE_IVSHMEM_ITERATIONS` | `10000` | ivshmem mmap iterations |
| `CUBE_PERF_CLEANUP` | `1` | `0` disables node residual micro-VM cleanup between rounds |
| `CUBE_CLEANUP_CMD` | `echo y \| cubecli unsafe destroyall -f` | Override node cleanup command |
| `CUBE_OUTPUT_REPORT` | `report` | Output report base path |
| `CUBE_HTML_OUTPUT` | `perf_report.html` | HTML report output path |

## Directory layout

| Path | Responsibility |
|---|---|
| `__main__.py` | CLI entry point (`python3 -m perf`) |
| `__init__.py` | `sys.path` bootstrap + zero-dependency `.env` loading |
| `framework/` | Framework core: `config` (configuration), `env` (environment collection), `runner` (timing/stats/cleanup primitives), `registry` (`@benchmark` registry + `ReportSection`/`ReportChart` report declarations + scenario selection + `run_all`) |
| `cases/` | Concrete benchmark scenarios, **auto-discovered** by the `bench_*.py` convention; each declares its own `ReportSection` in the decorator |
| `reporting/` | Reporting system: `report` (Markdown + JSON, renders by iterating each scenario's `ReportSection`), `report_html` (HTML, optional, derived from the same declarations' `charts`), `report_config` (HTML customization layer) |

## Adding a scenario

**Drop a `bench_<name>.py` file under `cases/` and it auto-registers** — no need
to touch the registry, alias table, or report code. The most common "spin up a
temporary sandbox, do one thing to it, destroy it" is a one-liner with
`@sandbox_benchmark`:

```python
from cubesandbox import Sandbox
from ...framework.registry import ReportChart, ReportSection, sandbox_benchmark

@sandbox_benchmark(
    "rollback",
    header=" [Perf] Rollback",
    fixture="snapshot",
    report=ReportSection(
        table="latency",                 # table type: latency|density|dirty|clone|pause_resume
        order=7,                          # Markdown section order (§1 is always the environment)
        title_zh="回滚（Rollback）",      # section title
        title_en="Rollback",
        method_zh="对运行中沙箱调用 `POST /sandboxes/{id}/rollback` …",  # "Method" note
        method_en="`POST /sandboxes/{id}/rollback` restores memory + filesystem in place …",
        noun_zh="回滚", noun_en="rollback",  # word used in conclusions (e.g. "single-concurrency **rollback** latency …")
        charts=(ReportChart("回滚（Rollback）"),),  # HTML charts (zero or more; leave empty for no chart)
    ),
)
def bench_rollback(sb: Sandbox, snap_id: str) -> None:
    """Benchmark: in-place rollback to a snapshot."""
    sb.rollback(snap_id)  # this line is the operation under test; sandbox & snapshot auto-cleaned
```

The framework handles concurrency scheduling, timing, stats, report collection,
and **destroy-right-after-use cleanup**. Scenario names follow `<key>-c<concurrency>`;
reports aggregate data points by that prefix.

**Report metadata is decorator-driven**: each scenario's section title / method
note / table type / throughput column (`throughput=True`) / conclusion wording all
live in `@benchmark(report=ReportSection(...))` (`@sandbox_benchmark` accepts
`report=` too). The Markdown renderer iterates all `ReportSection` declarations
ordered by `order`; HTML charts are derived from the same declarations' `charts` —
so **adding a scenario never requires editing `reporting/report.py`**. Latency-style
scenarios attach a `ReportChart`; density / dirty / clone scenarios (no line chart)
just leave `charts` empty.

When you need to split concerns or customize sampling, use the four underlying
decorators directly (`@benchmark` / `@parallel_sweep` / `@metrics` /
`@sandbox_action`) — full details in [DESIGN.zh.md](./DESIGN.zh.md) §4.

## Programmatic usage

```python
import sys
sys.path.insert(0, "tests")

from perf.framework.config import resolve_config
from perf.framework.env import collect_env_info
from perf.framework import registry
from perf import cases  # noqa: F401 — importing registers every scenario
from perf.reporting import report

cfg = resolve_config()
env = collect_env_info(cfg)
registry.run_all(cfg)              # or registry.run_all(cfg, selected=["snapshot"])
report.write_reports(env)          # writes report.md / report.zh.md / report.json / report.zh.json
```

## HTML report (optional)

The base flow only produces JSON + Markdown. Add `--html` when you want an
interactive single-page report:

```bash
# Also generate HTML after the run
python3 -m perf --html

# No benchmarks, generate HTML from existing JSON
python3 -m perf --html-only report_20260720T120000Z.json

# Multi-machine merge / regression comparison (pass multiple JSON files)
python3 -m perf --html-only machine1.json machine2.json --output merged.html
python3 -m perf --compare before.json after.json --output diff.html
```

The HTML is a self-contained, zero-dependency single page with an environment
overview, per-scenario tables, and latency line charts.
