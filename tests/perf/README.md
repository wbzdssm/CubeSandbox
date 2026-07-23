# `perf` — CubeSandbox Performance Benchmark Suite

One command runs all scenarios, producing **JSON + Markdown** reports.

## Quick Start

```bash
cd CubeSandbox/tests
cp perf/.env.example perf/.env      # edit with CUBE_API_URL / CUBE_TEMPLATE_ID
python3 -m perf
```

## Common Commands

| Command | Description |
|---------|-------------|
| `python3 -m perf` | Run all scenarios |
| `python3 -m perf --rounds 20` | 20 rounds per scenario |
| `python3 -m perf --scenarios clone-concurrency` | Run specific scenario |
| `python3 -m perf --list-scenarios` | List all scenarios |
| `python3 -m perf --cleanup` | Remove `snap-*` templates |
| `python3 -m perf --md-only report.json` | Re-render reports from JSON |

## Adding New Scenarios

See [Perf Integration Contract](../../docs/guide/perf-integration.zh.md).

TL;DR: Write a script accepting `-c <concurrency>` and `-n <operations>`, register it in `.env`.

## Configuration

All config in `tests/perf/.env.example`. `.env` auto-generated on first run.

Key variables:

| Variable | Description |
|----------|-------------|
| `CUBE_API_URL` | API endpoint |
| `CUBE_TEMPLATE_ID` | Template ID |
| `CUBE_EXTERNAL_SCRIPTS` | Script paths (comma-separated, glob support) |
| `CUBE_PERF_ROUNDS` | Rounds per scenario |
| `CUBE_PERF_CONCURRENCY` | Default concurrency gradient |
