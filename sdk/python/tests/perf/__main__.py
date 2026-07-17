# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""CLI entry point for the standalone performance benchmark suite.

Usage:
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf   # from tests/ dir

This suite only runs the performance benchmarks (see `benchmarks.py`); it
does not run the functional test cases (those live in `tests/e2e/`). It
reuses `e2e`'s config/env/runner/report helpers, so the generated report
files share the exact same format — the "Functional Test Results" section
will simply show 0/0/0 since no functional tests were executed.

Optional env vars:
    CUBE_TEMPLATE_ID         - skip auto-discovery
    CUBE_SKIP_DENSITY        - set to "1" to skip deployment density test
    CUBE_OUTPUT_REPORT       - base path for output reports (default: report.md)
                               produces report.md / report.zh.md / report.json / report.zh.json
    CUBE_PERF_ROUNDS         - rounds per perf scenario (default: 10)
    CUBE_DENSITY_COUNT       - max sandbox count for density test (default: 100)
"""

from __future__ import annotations

import sys

from e2e import report
from e2e.config import resolve_config
from e2e.env import collect_env_info
from e2e.runner import PERF_RESULTS

from . import benchmarks


def main() -> None:
    print("=" * 60)
    print(" Python SDK Performance Benchmark Suite")
    print("=" * 60)

    cfg = resolve_config()

    # --- Environment info ---
    print("\nCollecting environment information ...")
    env = collect_env_info(cfg)
    print(f"  Host: {env.hostname} | CPU: {env.cpu_model[:60] if env.cpu_model else 'N/A'}")
    print(f"  Cores: {env.cpu_cores_logical} logical ({env.cpu_sockets}S×{env.cpu_cores_physical}C×2T) | "
          f"Memory: {env.memory_total_gb} GiB | Disk: {env.disk_size_gb} GB {env.disk_type}")

    # --- Performance benchmarks ---
    print("\n" + "=" * 60)
    print(" PERFORMANCE BENCHMARKS")
    print("=" * 60)
    benchmarks.run_all(cfg)

    # --- Generate reports ---
    written = report.write_reports(env)
    print("\n📄 Reports saved to:")
    for path in written:
        print(f"   - {path}")

    print(f"\n{'='*60}")
    print(f"  {len(PERF_RESULTS)} performance scenarios collected")
    print(f"{'='*60}")

    sys.exit(0)


if __name__ == "__main__":
    main()
