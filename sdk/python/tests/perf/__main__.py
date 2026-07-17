# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""CLI entry point for the standalone performance benchmark suite.

Usage:
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf              # run benchmarks, produce JSON + HTML
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --html       # also generate HTML report
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --rounds 20  # run 20 rounds per scenario
    python3 -m perf --html-only data/*.json                        # generate HTML from existing data files
    python3 -m perf --compare data/run1.json data/run2.json        # compare two runs (HTML)

Optional env vars:
    CUBE_TEMPLATE_ID         - skip auto-discovery
    CUBE_SKIP_DENSITY        - set to "1" to skip deployment density test
    CUBE_OUTPUT_REPORT       - base path for output reports (default: report)
    CUBE_PERF_ROUNDS         - rounds per perf scenario (default: 10)
    CUBE_DENSITY_COUNT       - max sandbox count for density test (default: 100)
    CUBE_RUN_VOLUME          - set to "1" to enable Volume scenarios
    CUBE_HTML_OUTPUT         - path for HTML report (default: perf_report.html)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from . import report
from .config import DENSITY_COUNT, PERF_ROUNDS, resolve_config
from .env import collect_env_info
from .runner import PERF_RESULTS, reset

from . import benchmarks
from .report_html import generate_html


def _data_file_path(base: str, suffix: str = "") -> str:
    """Generate a timestamped data file path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{base}{suffix}_{ts}.json"


def run_benchmarks() -> str:
    """Run all benchmarks, write JSON data file, return the file path."""
    cfg = resolve_config()

    print("=" * 60)
    print(" Python SDK Performance Benchmark Suite")
    print("=" * 60)

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
    print(f"  Rounds per scenario: {PERF_ROUNDS}")
    print(f"  Density max count: {DENSITY_COUNT}")
    print()

    benchmarks.run_all(cfg)

    # --- Write JSON data ---
    data = report.build_report_data(env)
    base = os.environ.get("CUBE_OUTPUT_REPORT", "report")
    base_noext = os.path.splitext(base)[0]
    json_path = _data_file_path(base_noext)

    import json

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n📄 JSON data saved to: {os.path.abspath(json_path)}")

    # --- Also write MD reports (backward compat) ---
    written = report.write_reports(env)
    for path in written:
        print(f"   - {path}")

    print(f"\n{'='*60}")
    print(f"  {len(PERF_RESULTS)} performance scenarios collected")
    print(f"{'='*60}")

    return json_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CubeSandbox Python SDK Performance Benchmark Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --html
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --rounds 20
  python3 -m perf --html-only report_20260717T120000Z.json
  python3 -m perf --compare run1.json run2.json
        """,
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="generate HTML report after running benchmarks",
    )
    parser.add_argument(
        "--html-only",
        nargs="+",
        metavar="JSON_FILE",
        help="generate HTML report from existing JSON data files (no benchmarks run)",
    )
    parser.add_argument(
        "--compare",
        nargs="+",
        metavar="JSON_FILE",
        help="generate HTML comparison report from two or more JSON data files "
        "(files are grouped by environment fingerprint; same machine -> averaged, "
        "different machines -> separate comparison lines)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=None,
        help=f"override CUBE_PERF_ROUNDS (default: {PERF_ROUNDS})",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="HTML output path (default: perf_report.html)",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="CubeSandbox Performance Benchmark Report",
        help="HTML report title",
    )

    args = parser.parse_args()

    # Override rounds if specified
    if args.rounds is not None:
        os.environ["CUBE_PERF_ROUNDS"] = str(args.rounds)
        from . import config as _cfg

        _cfg.PERF_ROUNDS = args.rounds

    html_output = args.output or os.environ.get("CUBE_HTML_OUTPUT", "perf_report.html")

    # --html-only mode: no benchmarks, just generate HTML
    if args.html_only:
        generate_html(args.html_only, output_path=html_output, title=args.title)
        return

    # --compare mode: generate comparison HTML
    if args.compare:
        generate_html(args.compare, output_path=html_output, title=args.title)
        return

    # Default mode: run benchmarks
    reset()
    json_path = run_benchmarks()

    # --html flag: also generate HTML
    if args.html:
        generate_html([json_path], output_path=html_output, title=args.title)

    sys.exit(0)


if __name__ == "__main__":
    main()
