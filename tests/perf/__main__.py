# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""CLI entry point for the standalone performance benchmark suite.

Usage:
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf              # run benchmarks, produce JSON + Markdown
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --rounds 20  # run 20 rounds per scenario
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --scenarios snapshot-create-from  # only cold-start-from-snapshot
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --only snapshot rollback           # only selected scenarios
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --only ivshmem                      # default-off scenario, no extra env needed
    python3 -m perf --list-scenarios                              # list scenario keys/aliases
    python3 -m perf --md-only report.json                          # re-render md + json from existing data (no backend)

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

from .framework import registry

from .ops.cleanup import register_default_scripts
register_default_scripts()


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

    # Write back the data-flow settings this run actually used (incl. an
    # auto-discovered template id) so the 2nd/3rd run reuses them without any
    # re-exporting — just `python3 -m perf`.
    from . import _persist_dotenv, _TUNABLE_ENV_KEYS

    persisted = {
        "CUBE_API_URL": cfg.api_url,
        "CUBE_API_KEY": os.environ.get("CUBE_API_KEY")
        or os.environ.get("E2B_API_KEY", ""),
        "CUBE_TEMPLATE_ID": cfg.template_id or "",
        "CUBE_PROXY_NODE_IP": cfg.proxy_node_ip or "",
        "CUBE_PROXY_PORT_HTTP": str(cfg.proxy_port),
        "CUBE_SANDBOX_DOMAIN": cfg.sandbox_domain,
    }
    # Run tunables (concurrency ladders, rounds, density count, ...): only
    # persist keys explicitly set for *this* run (real env var, e.g. exported
    # to dodge a CubeMaster "no more resource" error, or already loaded from
    # a previous .env write-back) — never bake in an untouched internal
    # default. See framework/config.py for the actual defaults.
    persisted.update(
        (key, os.environ[key]) for key in _TUNABLE_ENV_KEYS if os.environ.get(key)
    )
    _persist_dotenv(persisted)

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

    # Per-concurrency-level cleanup is handled inside registry._bench()
    # via _post_concurrency_cleanup(). A final sweep here is a no-op.

    return json_path


def _run_external_scripts(
    scripts_dir: str,
    rounds: int,
    concurrency_levels: "tuple[int, ...] | None" = None,
) -> None:
    """Run every ``.py`` file in *scripts_dir* N times via subprocess.

    For each concurrency level in *concurrency_levels* the scripts are
    invoked in parallel threads (each thread spawns its own subprocess);
    wall‑clock time is measured per invocation and aggregated into the
    same avg / min / p95 / max stats the main suite uses.

    Zero‑coupling: scripts don't import anything from the framework — they
    just need to exit 0 on success.
    """
    import subprocess
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pathlib import Path

    levels = concurrency_levels or (1,)

    sd = Path(scripts_dir).expanduser().resolve()
    if not sd.is_dir():
        sys.exit(f"Error: '{sd}' is not a directory")

    py_files = sorted(sd.glob("*.py"))
    if not py_files:
        sys.exit(f"No .py files found in '{sd}'")

    print(f"Scripts dir     : {sd}")
    print(f"Files           : {len(py_files)}")
    print(f"Rounds          : {rounds}")
    print(f"Concurrency     : {', '.join(str(c) for c in levels)}")
    print(f"{'='*60}")

    all_results: list[dict] = []

    for pf in py_files:
        name = pf.stem
        print(f"\n [Run] {name}")
        for concurrency in levels:
            times: list[float] = []
            errors = 0
            wall_start = time.time()

            def _invoke():
                t0 = time.time()
                try:
                    proc = subprocess.run(
                        [sys.executable, str(pf)],
                        capture_output=True, text=True, timeout=300,
                        cwd=str(sd),
                    )
                except subprocess.TimeoutExpired:
                    return (time.time() - t0) * 1000, None, "TIMEOUT"
                elapsed = (time.time() - t0) * 1000
                if proc.returncode != 0:
                    err = (proc.stderr or "").strip()
                    return elapsed, proc.returncode, err[:200] if err else "ERR"
                return elapsed, 0, None

            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = [ex.submit(_invoke) for _ in range(rounds)]
                for i, fut in enumerate(as_completed(futs), 1):
                    elapsed, rc, err = fut.result()
                    if rc is None:  # timeout
                        errors += 1
                        print(f"  c={concurrency:>2} run {i:>2}/{rounds}: {elapsed:.0f}ms TIMEOUT")
                    elif rc != 0:
                        errors += 1
                        print(f"  c={concurrency:>2} run {i:>2}/{rounds}: {elapsed:.1f}ms ERR(rc={rc})")
                        if err:
                            print(f"       stderr: {err}")
                    else:
                        times.append(elapsed)

            wall_ms = (time.time() - wall_start) * 1000
            if times:
                times.sort()
                n = len(times)
                avg = sum(times) / n
                p50_val = times[int(n * 0.5)]
                p95_val = times[min(int(n * 0.95), n - 1)]
                per_ms = wall_ms / n if n > 0 else 0
                print(f"  concurrency={concurrency:>2}: avg={avg:.1f}ms "
                      f"min={times[0]:.1f}ms p95={p95_val:.1f}ms "
                      f"max={times[-1]:.1f}ms "
                      f"wall={wall_ms:.0f}ms per={per_ms:.1f}ms"
                      + (f"  errors={errors}" if errors else ""))
                all_results.append({
                    "name": name, "file": str(pf),
                    "concurrency": concurrency,
                    "avg_ms": round(avg, 2),
                    "p50_ms": round(p50_val, 2),
                    "p95_ms": round(p95_val, 2),
                    "min_ms": round(times[0], 2),
                    "max_ms": round(times[-1], 2),
                    "wall_ms": round(wall_ms, 2),
                    "per_ms": round(per_ms, 2),
                    "rounds": n, "errors": errors,
                })
            else:
                print(f"  concurrency={concurrency:>2}: ALL FAILED (errors={errors})")

    # Summary
    print(f"\n{'='*72}")
    header_fmt = f"{'Script':<30} {'c':>3} {'avg':>8} {'min':>8} {'p95':>8} {'max':>8} {'wall':>8} {'per':>8}"
    print(header_fmt)
    print(f"{'-'*30} {'-'*3} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for r in all_results:
        print(f"{r['name']:<30} {r['concurrency']:>3} "
              f"{r['avg_ms']:>8.1f} {r['min_ms']:>8.1f} {r['p95_ms']:>8.1f} "
              f"{r['max_ms']:>8.1f} {r['wall_ms']:>8.0f} {r['per_ms']:>8.1f}")
    if all_results:
        errors_total = sum(r.get("errors", 0) for r in all_results)
        if errors_total:
            print(f"\n  {errors_total} error(s) total")
    print(f"{'='*72}")

    if all_results:
        out = sd / "perf_scripts.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"Results saved to: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CubeSandbox Python SDK Performance Benchmark Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --rounds 20
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --scenarios snapshot-create-from
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --only snapshot rollback
  CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --scenarios all no-ivshmem
  python3 -m perf --list-scenarios
  python3 -m perf --md-only report.json
        """,
    )
    parser.add_argument(
        "--md-only",
        metavar="JSON_FILE",
        help="parse an existing JSON data file and re-render the Markdown + JSON "
        "reports (no benchmarks run, no backend required)",
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
        "--cleanup",
        action="store_true",
        help="delete all snap-* snapshot templates from the backend before running benchmarks",
    )
    parser.add_argument(
        "--cleanup-dry-run",
        action="store_true",
        help="list snap-* snapshots that --cleanup would delete, then exit",
    )
    parser.add_argument(
        "--cleanup-older-than",
        type=int,
        default=0,
        metavar="MINUTES",
        help="with --cleanup, only delete snapshots older than N minutes",
    )
    parser.add_argument(
        "--scripts",
        metavar="DIR",
        default=None,
        help="run all .py files in DIR as standalone scripts, measuring "
        "wall-clock time of each invocation (no concurrency, pure "
        "subprocess), then print stats — zero framework coupling",
    )
    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="list available benchmark scenario keys/aliases and exit",
    )

    args = parser.parse_args()

    # --scripts DIR: run raw .py files as-is and collect timing stats.
    if args.scripts:
        c_str = os.environ.get("CUBE_CREATE_CONCURRENCY") \
            or os.environ.get("CUBE_PERF_CONCURRENCY", "1")
        try:
            levels = tuple(int(x.strip()) for x in c_str.split(","))
        except Exception:
            levels = (1,)
        _run_external_scripts(
            args.scripts,
            rounds=args.rounds or PERF_ROUNDS,
            concurrency_levels=levels,
        )
        return

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

    # --md-only mode: no benchmarks, just re-render md/json from existing data
    if args.md_only:
        base = os.path.splitext(os.environ.get("CUBE_OUTPUT_REPORT", "report"))[0]
        written = report.render_from_json(args.md_only, base)
        print(f"Re-rendered reports from {args.md_only}:")
        for path in written:
            print(f"   - {path}")
        return

    # --cleanup-dry-run: list snapshots only, then exit
    if args.cleanup_dry_run or args.cleanup:
        from .ops.cleanup import list_snapshots, delete_snapshots

        snaps = list_snapshots()
        if not snaps:
            print("No snap-* snapshot templates found.")
        else:
            print(f"\nFound {len(snaps)} snap-* snapshot templates:")
            for s in snaps:
                print(f"  {s['templateID']:<36} {s.get('status',''):<12} {s.get('createdAt','')}")
            if args.cleanup_dry_run:
                print("\n[DRY RUN] Remove --cleanup-dry-run and use --cleanup to delete.")
            else:
                ids = [s["templateID"] for s in snaps]
                print(f"\nDeleting {len(ids)} snapshots ...")
                ok, fail = delete_snapshots(ids)
                print(f"Done: {ok} deleted, {fail} failed.\n")
        if args.cleanup_dry_run:
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

    sys.exit(0)


if __name__ == "__main__":
    main()
