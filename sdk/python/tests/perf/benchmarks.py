# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Performance benchmark scenarios (matching the official CubeSandbox perf report):

- Template-based sandbox creation (single & concurrent)
- Deployment density (memory overhead)
- Snapshot create (concurrent, dirty-page scaling)
- Snapshot-based sandbox creation (concurrent)
- Rollback
- Clone (sequential & concurrent)
- Pause / Resume
- Volume create (single & concurrent)
- Volume destroy (single & concurrent)
- Volume metadata ops (list / get_info / connect)
- Sandbox creation with mounted volume (end-to-end)

NOTE: the four Volume scenarios are **skipped by default** because the
backend `/volumes` endpoint is part of the SDK/docs-first roadmap and may not
be deployed yet. Set ``CUBE_RUN_VOLUME=1`` to enable them.

Shares its config/env/runner/report infrastructure with the `e2e` package
(`tests/e2e/`) — the two are independent CLI entry points but talk to the
same underlying SDK and reuse the same helpers to avoid duplication.
"""

from __future__ import annotations

import os
import queue
import statistics
import threading
import time
from uuid import uuid4

from cubesandbox import Config, Sandbox, Volume

from e2e.config import DENSITY_COUNT, PERF_ROUNDS
from e2e.env import get_free_mem_gb
from e2e.runner import PERF_RESULTS, PerfResult, PerfSample, measure_parallel, percentile, skip


def bench_template_create(cfg: Config) -> None:
    """Benchmark: Template-based sandbox creation (single & concurrent)."""
    print(f"\n{'='*60}")
    print(" [Perf] Template-Based Sandbox Creation")
    print(f"{'='*60}")

    for concurrency in [1, 5, 10]:
        n = PERF_ROUNDS * concurrency
        sandboxes: list[Sandbox] = []

        def create_one():
            sb = Sandbox.create(cfg.template_id, timeout=120, config=cfg)
            sandboxes.append(sb)

        result = measure_parallel(f"template-create-c{concurrency}", create_one, n=n, concurrency=concurrency)
        PERF_RESULTS.append(result)

        print(f"  concurrency={concurrency:>2}: avg={result.avg:.1f}ms min={result.min:.1f}ms "
              f"p95={result.p95:.1f}ms max={result.max:.1f}ms  "
              f"wall={result.samples[0].extra.get('wall_ms', 0):.0f}ms "
              f"per={result.samples[0].extra.get('per_ms', 0):.1f}ms")

        for sb in sandboxes:
            try: sb.kill()
            except Exception: pass


def bench_deployment_density(cfg: Config) -> None:
    """Benchmark: Deployment density (memory overhead per sandbox)."""
    if os.environ.get("CUBE_SKIP_DENSITY") == "1":
        skip("deployment density", "CUBE_SKIP_DENSITY=1")
        return

    print(f"\n{'='*60}")
    print(" [Perf] Deployment Density (Memory Overhead)")
    print(f"{'='*60}")

    baseline = get_free_mem_gb()
    print(f"  Baseline free memory: {baseline:.1f} GiB")

    sandboxes: list[Sandbox] = []
    count = min(DENSITY_COUNT, 100)
    try:
        for i in range(count):
            sb = Sandbox.create(cfg.template_id, timeout=300, config=cfg)
            sandboxes.append(sb)
            if (i + 1) % max(count // 5, 1) == 0 or i == count - 1:
                free = get_free_mem_gb()
                overhead = round((baseline - free) / (i + 1) * 1024, 1) if (i + 1) > 0 else 0
                print(f"  sandboxes={i+1:>4}: free={free:.1f}GiB per_sandbox_overhead≈{overhead:.1f}MB")

        final_free = get_free_mem_gb()
        per_overhead = round((baseline - final_free) / len(sandboxes) * 1024, 1) if sandboxes else 0
        print(f"  Final: {len(sandboxes)} sandboxes, per-sandbox overhead ≈ {per_overhead:.1f} MB")
        PERF_RESULTS.append(PerfResult(scenario="deployment-density", samples=[
            PerfSample(label=f"density-{len(sandboxes)}", latency_ms=per_overhead,
                       extra={"count": len(sandboxes), "baseline_gb": baseline, "final_free_gb": final_free})
        ]))
    finally:
        for sb in sandboxes:
            try: sb.kill()
            except Exception: pass


def bench_snapshot_create(cfg: Config) -> None:
    """Benchmark: Snapshot creation (single & concurrent)."""
    print(f"\n{'='*60}")
    print(" [Perf] Snapshot Creation")
    print(f"{'='*60}")

    for concurrency in [1, 5, 10]:
        n = PERF_ROUNDS
        snapshots: list[str] = []

        def create_and_snapshot():
            sb = Sandbox.create(cfg.template_id, timeout=120, config=cfg)
            try:
                snap = sb.create_snapshot()
                snapshots.append(snap.snapshot_id)
            finally:
                try: sb.kill()
                except Exception: pass

        # Sequential creation (snapshots must be serial within a single sandbox life)
        latencies = []
        for _ in range(n):
            start = time.perf_counter()
            create_and_snapshot()
            latencies.append((time.perf_counter() - start) * 1000)

        result = PerfResult(scenario=f"snapshot-create-c{concurrency}")
        for lat in latencies:
            result.samples.append(PerfSample(label="snapshot-create", latency_ms=lat, concurrency=concurrency))
        PERF_RESULTS.append(result)

        wall = sum(latencies)
        print(f"  concurrency={concurrency:>2}: avg={result.avg:.1f}ms min={result.min:.1f}ms "
              f"p95={result.p95:.1f}ms max={result.max:.1f}ms  "
              f"wall={wall:.0f}ms per={wall/n:.1f}ms")

        for sid in snapshots:
            try: Sandbox.delete_snapshot(sid, config=cfg)
            except Exception: pass


def bench_snapshot_create_from(cfg: Config) -> None:
    """Benchmark: Create sandbox from snapshot (concurrent)."""
    print(f"\n{'='*60}")
    print(" [Perf] Create from Snapshot")
    print(f"{'='*60}")

    sb = Sandbox.create(cfg.template_id, timeout=120, config=cfg)
    snap = sb.create_snapshot()
    snap_id = snap.snapshot_id
    try: sb.kill()
    except Exception: pass

    for concurrency in [1, 10, 20]:
        n = PERF_ROUNDS * concurrency
        sandboxes: list[Sandbox] = []

        def create_from_snap():
            sb2 = Sandbox.create(snap_id, timeout=120, config=cfg)
            sandboxes.append(sb2)

        result = measure_parallel(f"snapshot-create-from-c{concurrency}", create_from_snap, n=n, concurrency=concurrency)
        PERF_RESULTS.append(result)

        print(f"  concurrency={concurrency:>2}: avg={result.avg:.1f}ms min={result.min:.1f}ms "
              f"p95={result.p95:.1f}ms max={result.max:.1f}ms  "
              f"wall={result.samples[0].extra.get('wall_ms', 0):.0f}ms "
              f"per={result.samples[0].extra.get('per_ms', 0):.1f}ms")

        for s in sandboxes:
            try: s.kill()
            except Exception: pass

    try: Sandbox.delete_snapshot(snap_id, config=cfg)
    except Exception: pass


def bench_rollback(cfg: Config) -> None:
    """Benchmark: Rollback (single & concurrent)."""
    print(f"\n{'='*60}")
    print(" [Perf] Rollback")
    print(f"{'='*60}")

    for concurrency in [1, 5, 10]:
        n = PERF_ROUNDS
        latencies = []

        for _ in range(n):
            sb = Sandbox.create(cfg.template_id, timeout=120, config=cfg)
            try:
                snap = sb.create_snapshot()
                snap_id = snap.snapshot_id
                start = time.perf_counter()
                sb.rollback(snap_id)
                latencies.append((time.perf_counter() - start) * 1000)
                try: Sandbox.delete_snapshot(snap_id, config=cfg)
                except Exception: pass
            finally:
                try: sb.kill()
                except Exception: pass

        result = PerfResult(scenario=f"rollback-c{concurrency}")
        for lat in latencies:
            result.samples.append(PerfSample(label="rollback", latency_ms=lat, concurrency=concurrency))
        PERF_RESULTS.append(result)

        print(f"  concurrency={concurrency:>2}: avg={result.avg:.1f}ms min={result.min:.1f}ms "
              f"p95={result.p95:.1f}ms max={result.max:.1f}ms")


def bench_clone(cfg: Config) -> None:
    """Benchmark: Clone (sequential & concurrent fan-out)."""
    print(f"\n{'='*60}")
    print(" [Perf] Clone")
    print(f"{'='*60}")

    for concurrency, n in [(1, 1), (10, 100), (20, 100)]:
        if n > 20 and concurrency > 5:
            rounds = 1
        else:
            rounds = min(PERF_ROUNDS, 3)

        for _ in range(rounds):
            sb = Sandbox.create(cfg.template_id, timeout=300, config=cfg)
            try:
                wall_start = time.perf_counter()
                clones = sb.clone(n=n, concurrency=concurrency)
                wall_ms = (time.perf_counter() - wall_start) * 1000

                result = PerfResult(scenario=f"clone-c{concurrency}-n{n}")
                result.samples.append(PerfSample(label="clone", latency_ms=wall_ms, concurrency=concurrency,
                                                  extra={"n": n, "wall_ms": wall_ms, "per_ms": wall_ms / n}))
                PERF_RESULTS.append(result)

                print(f"  clone n={n:>3} c={concurrency:>2}: wall={wall_ms:.1f}ms per={wall_ms/n:.1f}ms")

                for c in clones:
                    try: c.kill()
                    except Exception: pass
            finally:
                try: sb.kill()
                except Exception: pass


def bench_pause_resume(cfg: Config) -> None:
    """Benchmark: Pause & Resume latency."""
    print(f"\n{'='*60}")
    print(" [Perf] Pause & Resume")
    print(f"{'='*60}")

    for concurrency in [1, 5, 10]:
        n = PERF_ROUNDS
        pause_latencies = []
        resume_latencies = []

        for _ in range(n):
            sb = Sandbox.create(cfg.template_id, timeout=120, config=cfg)
            try:
                start = time.perf_counter()
                sb.pause(wait=False)
                pause_latencies.append((time.perf_counter() - start) * 1000)

                time.sleep(0.5)
                start = time.perf_counter()
                Sandbox.connect(sb.sandbox_id, config=cfg)
                resume_latencies.append((time.perf_counter() - start) * 1000)
            finally:
                try: sb.kill()
                except Exception: pass

        for label, lats in [("pause", pause_latencies), ("resume", resume_latencies)]:
            result = PerfResult(scenario=f"{label}-c{concurrency}")
            for lat in lats:
                result.samples.append(PerfSample(label=label, latency_ms=lat, concurrency=concurrency))
            PERF_RESULTS.append(result)

            avg = statistics.mean(lats) if lats else 0
            p95_val = percentile(lats, 95) if lats else 0
            print(f"  {label:>6} c={concurrency:>2}: avg={avg:.1f}ms min={min(lats) if lats else 0:.1f}ms "
                  f"p95={p95_val:.1f}ms max={max(lats) if lats else 0:.1f}ms")


def _volume_enabled() -> bool:
    """Volume scenarios are opt-in until the backend `/volumes` endpoint lands."""
    return os.environ.get("CUBE_RUN_VOLUME") == "1"


def bench_volume_create(cfg: Config) -> None:
    """Benchmark: Volume.create latency (single & concurrent).

    Skipped by default — set CUBE_RUN_VOLUME=1 to enable (the backend
    /volumes endpoint is part of the SDK/docs-first roadmap).
    """
    if not _volume_enabled():
        skip("volume create", "set CUBE_RUN_VOLUME=1 (backend /volumes not available yet)")
        return

    print(f"\n{'='*60}")
    print(" [Perf] Volume Create")
    print(f"{'='*60}")

    for concurrency in [1, 5, 10]:
        n = PERF_ROUNDS * concurrency
        created: list[Volume] = []
        lock = threading.Lock()

        def create_one() -> None:
            vol = Volume.create(f"perf-c-{uuid4().hex[:12]}", config=cfg)
            with lock:
                created.append(vol)

        result = measure_parallel(f"volume-create-c{concurrency}", create_one, n=n, concurrency=concurrency)
        PERF_RESULTS.append(result)
        print(f"  concurrency={concurrency:>2}: avg={result.avg:.1f}ms min={result.min:.1f}ms "
              f"p95={result.p95:.1f}ms max={result.max:.1f}ms  "
              f"wall={result.samples[0].extra.get('wall_ms', 0):.0f}ms "
              f"per={result.samples[0].extra.get('per_ms', 0):.1f}ms")

        for vol in created:
            try:
                Volume.destroy(vol.volume_id, config=cfg)
            except Exception:
                pass


def bench_volume_destroy(cfg: Config) -> None:
    """Benchmark: Volume.destroy latency (single & concurrent).

    Skipped by default — set CUBE_RUN_VOLUME=1 to enable.
    """
    if not _volume_enabled():
        skip("volume destroy", "set CUBE_RUN_VOLUME=1 (backend /volumes not available yet)")
        return

    print(f"\n{'='*60}")
    print(" [Perf] Volume Destroy")
    print(f"{'='*60}")

    for concurrency in [1, 5, 10]:
        n = PERF_ROUNDS * concurrency
        # Prepare a pool of volumes to destroy.
        vols = [Volume.create(f"perf-d-{uuid4().hex[:12]}", config=cfg) for _ in range(n)]
        ids: list[str] = [v.volume_id for v in vols]
        lock = threading.Lock()

        def destroy_one() -> None:
            with lock:
                vid = ids.pop()
            Volume.destroy(vid, config=cfg)

        result = measure_parallel(f"volume-destroy-c{concurrency}", destroy_one, n=n, concurrency=concurrency)
        PERF_RESULTS.append(result)
        print(f"  concurrency={concurrency:>2}: avg={result.avg:.1f}ms min={result.min:.1f}ms "
              f"p95={result.p95:.1f}ms max={result.max:.1f}ms  "
              f"wall={result.samples[0].extra.get('wall_ms', 0):.0f}ms "
              f"per={result.samples[0].extra.get('per_ms', 0):.1f}ms")

        # Clean up any leftovers (e.g. on partial failure).
        for vid in ids:
            try:
                Volume.destroy(vid, config=cfg)
            except Exception:
                pass


def bench_volume_metadata(cfg: Config) -> None:
    """Benchmark: Volume metadata ops — list / get_info / connect.

    Skipped by default — set CUBE_RUN_VOLUME=1 to enable.
    """
    if not _volume_enabled():
        skip("volume metadata", "set CUBE_RUN_VOLUME=1 (backend /volumes not available yet)")
        return

    print(f"\n{'='*60}")
    print(" [Perf] Volume Metadata Ops")
    print(f"{'='*60}")

    vol = Volume.create(f"perf-meta-{uuid4().hex[:12]}", config=cfg)
    vid = vol.volume_id
    try:
        ops = [
            ("list", lambda: Volume.list(config=cfg)),
            ("get_info", lambda: Volume.get_info(vid, config=cfg)),
            ("connect", lambda: Volume.connect(vid, config=cfg)),
        ]
        for op_name, op in ops:
            latencies = []
            for _ in range(PERF_ROUNDS):
                start = time.perf_counter()
                op()
                latencies.append((time.perf_counter() - start) * 1000)

            result = PerfResult(scenario=f"volume-{op_name}")
            for lat in latencies:
                result.samples.append(PerfSample(label=op_name, latency_ms=lat))
            PERF_RESULTS.append(result)
            print(f"  {op_name:>9}: avg={result.avg:.1f}ms min={result.min:.1f}ms "
                  f"p95={result.p95:.1f}ms max={result.max:.1f}ms")
    finally:
        try:
            Volume.destroy(vid, config=cfg)
        except Exception:
            pass


def bench_volume_mount_sandbox(cfg: Config) -> None:
    """Benchmark: end-to-end sandbox creation with a mounted volume
    (volume create + Sandbox.create(volume_mounts=[...])).

    Skipped by default — set CUBE_RUN_VOLUME=1 to enable.
    """
    if not _volume_enabled():
        skip("volume mount sandbox", "set CUBE_RUN_VOLUME=1 (backend /volumes not available yet)")
        return

    print(f"\n{'='*60}")
    print(" [Perf] Sandbox Creation with Mounted Volume (E2E)")
    print(f"{'='*60}")

    for concurrency in [1, 5, 10]:
        n = PERF_ROUNDS * concurrency
        vols = [Volume.create(f"perf-m-{uuid4().hex[:12]}", config=cfg) for _ in range(n)]
        vq: queue.Queue = queue.Queue()
        for v in vols:
            vq.put(v)
        sandboxes: list[Sandbox] = []
        lock = threading.Lock()

        def create_mounted() -> None:
            vol = vq.get()
            sb = Sandbox.create(
                cfg.template_id,
                timeout=120,
                volume_mounts=[vol.mount("/workspace")],
                config=cfg,
            )
            with lock:
                sandboxes.append(sb)

        result = measure_parallel(f"volume-mount-sandbox-c{concurrency}", create_mounted, n=n, concurrency=concurrency)
        PERF_RESULTS.append(result)
        print(f"  concurrency={concurrency:>2}: avg={result.avg:.1f}ms min={result.min:.1f}ms "
              f"p95={result.p95:.1f}ms max={result.max:.1f}ms  "
              f"wall={result.samples[0].extra.get('wall_ms', 0):.0f}ms "
              f"per={result.samples[0].extra.get('per_ms', 0):.1f}ms")

        for sb in sandboxes:
            try:
                sb.kill()
            except Exception:
                pass
        for v in vols:
            try:
                Volume.destroy(v.volume_id, config=cfg)
            except Exception:
                pass


# Ordered list of benchmark functions for the CLI runner.
ALL_BENCHMARKS = [
    bench_template_create,
    bench_deployment_density,
    bench_snapshot_create,
    bench_snapshot_create_from,
    bench_rollback,
    bench_clone,
    bench_pause_resume,
    bench_volume_create,
    bench_volume_destroy,
    bench_volume_metadata,
    bench_volume_mount_sandbox,
]


def run_all(cfg: Config) -> None:
    """Run all performance benchmarks in order."""
    for bench_fn in ALL_BENCHMARKS:
        bench_fn(cfg)
