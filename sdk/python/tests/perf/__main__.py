# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""CLI entry point for the standalone performance benchmark suite.

Usage:
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf              # run benchmarks, produce JSON + HTML
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --html       # also generate HTML report
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --rounds 20  # run 20 rounds per scenario
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --scenarios snapshot-create-from  # only cold-start-from-snapshot
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --only snapshot rollback           # only selected scenarios
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --only ivshmem                      # default-off scenario, no extra env needed
    python3 -m perf --list-scenarios                              # list scenario keys/aliases
    python3 -m perf --html-only data/*.json                        # generate HTML from existing data files
    python3 -m perf --compare data/run1.json data/run2.json        # compare two runs (HTML)

Optional env vars:
    CUBE_TEMPLATE_ID         - skip auto-discovery
    CUBE_PERF_SCENARIOS      - comma/space separated scenario keys/aliases to run (default: all)
    CUBE_SKIP_DENSITY        - set to "1" to skip deployment density test
    CUBE_OUTPUT_REPORT       - base path for output reports (default: report)
    CUBE_PERF_ROUNDS         - rounds per perf scenario (default: 10)
    CUBE_DENSITY_COUNT       - max sandbox count for density test (default: 100)
    CUBE_RUN_VOLUME          - set to "1" to enable Volume scenarios
    CUBE_RUN_IVSHMEM         - set to "1" to enable the ivshmem scenario (host-only)
    CUBE_IVSHMEM_TEMPLATE_ID - ivshmem-enabled template (falls back to CUBE_TEMPLATE_ID)
    CUBE_IVSHMEM_ITERATIONS  - mmap iterations for the ivshmem scenario (default: 10000)
    CUBE_HTML_OUTPUT         - path for HTML report (default: perf_report.html)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from .reporting import report
from .framework.config import DENSITY_COUNT, PERF_ROUNDS, resolve_config
from .framework.env import collect_env_info
from .framework.runner import PERF_RESULTS, reset

from . import cases  # noqa: F401 — importing registers every @benchmark scenario
from .framework import registry
from .reporting.report_html import generate_html


def _data_file_path(base: str, suffix: str = "") -> str:
    """Generate a timestamped data file path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{base}{suffix}_{ts}.json"


def _resolve_selected(cli_scenarios: "list[str] | None") -> "list[str] | None":
    """Merge --scenarios CLI values with the CUBE_PERF_SCENARIOS env var and
    flatten comma/space-separated tokens into a clean list. CLI wins over env;
    returns None when neither is set (i.e. run the full suite)."""
    raw = cli_scenarios
    if not raw:
        env = os.environ.get("CUBE_PERF_SCENARIOS", "").strip()
        raw = [env] if env else None
    if not raw:
        return None
    tokens: list[str] = []
    for item in raw:
        for part in item.replace(",", " ").split():
            if part:
                tokens.append(part)
    return tokens or None


def run_benchmarks(selected: "list[str] | None" = None) -> str:
    """Run benchmarks (all by default, or the *selected* subset), write JSON
    data file, return the file path."""
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

    registry.run_all(cfg, selected=selected)

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
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --scenarios snapshot-create-from
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --only snapshot rollback
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --scenarios all no-ivshmem
  python3 -m perf --list-scenarios
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
        "--scenarios",
        "--only",
        dest="scenarios",
        nargs="+",
        metavar="SCENARIO",
        default=None,
        help="run only the given scenario(s); accepts keys or aliases, "
        "comma/space separated, and 'no-<key>' to exclude "
        "(e.g. --scenarios snapshot-create-from, --only snapshot, "
        "--scenarios all no-ivshmem). Explicitly naming a default-off scenario "
        "(e.g. --only ivshmem / volume) auto-enables it, so its opt-in env "
        "(CUBE_RUN_IVSHMEM / CUBE_RUN_VOLUME) is no longer required. "
        "Overrides CUBE_PERF_SCENARIOS. Use --list-scenarios to see all choices.",
    )
    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="list available benchmark scenario keys/aliases and exit",
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

    # --list-scenarios: print available scenario keys/aliases and exit.
    if args.list_scenarios:
        print("Available scenarios (canonical keys):")
        for key in registry.BENCHMARK_REGISTRY:
            print(f"  {key}")
        print("\nAliases:")
        for alias, keys in registry.BENCHMARK_ALIASES.items():
            print(f"  {alias:<20} -> {', '.join(keys)}")
        print("\nExclude a scenario with a 'no-'/'skip-' prefix, e.g. "
              "--scenarios all no-ivshmem")
        return

    # Override rounds if specified
    if args.rounds is not None:
        os.environ["CUBE_PERF_ROUNDS"] = str(args.rounds)
        from .framework import config as _cfg

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
    selected = _resolve_selected(args.scenarios)
    # Validate selection early (raises with a helpful message on typos).
    try:
        registry.select_benchmarks(selected)
    except ValueError as exc:
        sys.exit(f"error: {exc}")

    reset()
    json_path = run_benchmarks(selected=selected)

    # --html flag: also generate HTML
    if args.html:
        generate_html([json_path], output_path=html_output, title=args.title)

    sys.exit(0)


if __name__ == "__main__":
    main()
