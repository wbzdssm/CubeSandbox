# `perf` — CubeSandbox Performance Benchmark Suite

One command runs all scenarios, producing **JSON + Markdown** reports.

## Quick Start

```bash
cd CubeSandbox/tests
cp perf/.env.example perf/.env      # edit with CUBE_API_URL / CUBE_TEMPLATE_ID
python3 -m perf

# Run a specific scenario
python3 -m perf --scenarios clone-concurrency

# List all scenarios
python3 -m perf --list-scenarios
```

## Common Commands

| Command | Description |
|---------|-------------|
| `python3 -m perf` | Run all scenarios |
| `python3 -m perf --rounds 20` | 20 rounds per scenario |
| `python3 -m perf --scenarios clone-concurrency` | Run specific scenario |
| `python3 -m perf --list-scenarios` | List registered scenarios |
| `python3 -m perf --cleanup` | Remove `snap-*` templates |
| `python3 -m perf --cleanup-dry-run` | Preview snapshots to be removed |
| `python3 -m perf --md-only report.json` | Re-render reports from JSON |

## Output Files

By default, reports are written into a fresh per-run directory under
`tests/perf/report/<UTC-timestamp>/`, so the CWD stays clean:

```
tests/perf/report/
├── 20260723T044740Z/             # one subdir per invocation
│   ├── report.json               # Full data JSON (English, with environment, config, performance)
│   ├── report.zh.json            # Same, Chinese-friendly (locale tag set to "zh")
│   ├── report.md                 # Markdown report (English)
│   └── report.zh.md              # Markdown report (Chinese)
└── aggregate/                    # reserved for cross-run aggregation
```

The default base name is `report`; the path is
`<perf>/report/<UTC-timestamp>/report.{md,zh.md,json,zh.json}`.

Override the **full base path** with `CUBE_OUTPUT_REPORT`, e.g.:

- `CUBE_OUTPUT_REPORT=./report` → files land in `./report.{md,zh.md,json,zh.json}` (CWD)
- `CUBE_OUTPUT_REPORT=/tmp/perf/2026-07-23/report` → all 4 files in that dir

## Cleanup Behaviour

The framework cleans up after **each round** and **after all scenarios**:

| Timing | What is cleaned | How to disable |
|--------|----------------|----------------|
| After each round | Sandboxes created in that round (kill) | `CUBE_PERF_CLEANUP=0` |
| After all scenarios | `snap-*` snapshot templates (SDK delete) | `CUBE_PERF_AUTO_CLEANUP=0` |
| Manual trigger | All `snap-*` snapshots, regardless of age | `python3 -m perf --cleanup` |

**Notes**:
- Only deletes templates with IDs starting with `snap-`; user-owned `tpl-*` templates are never touched
- Snapshots with active sandbox references (non-empty `replicas`) are silently skipped
- Waits `CUBE_PERF_AUTO_CLEANUP_WAIT` seconds (default 5s) before cleanup to let async operations settle
- Use `--cleanup-dry-run` to preview snapshots without deleting

`.env` is auto-generated on first run. Full comments in `.env.example`. Precedence: CLI > env var > .env.

### Connection

| Variable | Default | Description |
|----------|---------|-------------|
| `CUBE_API_URL` | `http://127.0.0.1:3000` | CubeMaster API URL |
| `CUBE_API_KEY` | — | API key (optional) |
| `CUBE_TEMPLATE_ID` | auto-discover | Template ID (leave empty to auto-find READY template) |
| `CUBE_PROXY_NODE_IP` | — | Direct-connect node IP |
| `CUBE_PROXY_PORT_HTTP` | `80` | Proxy HTTP port |
| `CUBE_SANDBOX_DOMAIN` | `cube.app` | Sandbox domain |

### Run Parameters

| Variable | Default | Description |
|----------|---------|-------------|
| `CUBE_PERF_ROUNDS` | `3` | Rounds per scenario |
| `CUBE_PERF_WARMUP` | `1` | Warm-up rounds (excluded from stats) |
| `CUBE_PERF_SETTLE` | `0` | Settle seconds between levels |
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | Default concurrency gradient |
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | Create scenario gradient |
| `CUBE_DENSITY_COUNT` | `100` | Max sandbox count for density test |

### Auto Cleanup

| Variable | Default | Description |
|----------|---------|-------------|
| `CUBE_PERF_AUTO_CLEANUP` | `1` | Remove residual `snap-*` after benchmarks |
| `CUBE_PERF_AUTO_CLEANUP_WAIT` | `0` | Wait seconds for async ops before cleanup (0 = no wait) |

### External Scripts

| Variable | Description |
|----------|-------------|
| `CUBE_EXTERNAL_SCRIPTS` | Comma-separated script paths, `*` glob supported |

## Built-in Scenarios

6 scenarios enabled by default, 2 opt-in. All located in `../examples/snapshot-rollback-clone/`:

| Scenario | Key | Description |
|----------|-----|-------------|
| Create (concurrency) | `create-concurrency` | Multi-concurrency sandbox create |
| Snapshot (concurrency) | `snapshot-concurrency` | Multi-concurrency snapshot create |
| Rollback (concurrency) | `rollback-concurrency` | Multi-concurrency snapshot rollback |
| Clone (concurrency) | `clone-concurrency` | Multi-concurrency sandbox clone |
| Pause & Resume | `pause-resume-concurrency` | Multi-concurrency pause/resume |
| Snapshot Dirty | `snapshot-dirty` | Snapshot creation with varying dirty page sizes |

Opt-in scenarios:

| Scenario | Key | Enable | Notes |
|----------|-----|--------|-------|
| ivshmem shared memory | `ivshmem` | `CUBE_RUN_IVSHMEM=1` | Requires host ivshmem + ivshmem template |
| Volume (remote storage) | `volume` | `CUBE_RUN_VOLUME=1` | Requires Volume plugin |

## Adding New Scenarios

See [Integration Contract](docs/guide/perf-design.md). TL;DR: Write a script accepting `-c <concurrency>` and `-n <operations>`, register it in `.env`.
