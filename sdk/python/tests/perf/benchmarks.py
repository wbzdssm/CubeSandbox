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
- ivshmem shared-memory host-side mmap read/write

NOTE: the four Volume scenarios are **skipped by default** because the
backend `/volumes` endpoint is part of the SDK/docs-first roadmap and may not
be deployed yet. Set ``CUBE_RUN_VOLUME=1`` to enable them. The ivshmem
scenario is likewise opt-in (``CUBE_RUN_IVSHMEM=1``) — it needs an
ivshmem-enabled template and must run on the CubeSandbox host.

Shares its config/env/runner/report infrastructure with the `e2e` package
(`tests/e2e/`) — the two are independent CLI entry points but talk to the
same underlying SDK and reuse the same helpers to avoid duplication.

Data produced by this suite is written as JSON (for HTML report consumption
and multi-machine merging) and Markdown (for human review). See `report_html.py`
for the self-contained HTML report generator with baseline comparison.
"""

from __future__ import annotations

import os
import platform
import queue
import statistics
import threading
import time
from uuid import uuid4

from cubesandbox import Config, Sandbox

try:
    from cubesandbox import Volume
except ImportError:
    Volume = None  # type: ignore[assignment]

from .config import CONCURRENCY_LEVELS, DENSITY_COUNT, DIRTY_SWEEP, PERF_ROUNDS, PERF_SETTLE, PERF_WARMUP
from .env import get_free_mem_gb
from .runner import PERF_RESULTS, PerfResult, PerfSample, measure_parallel, percentile, skip


def bench_template_create(cfg: Config) -> None:
    """Benchmark: Template-based sandbox creation (single & concurrent)."""
    print(f"\n{'='*60}")
    print(" [Perf] Template-Based Sandbox Creation")
    print(f"{'='*60}")

    for concurrency in CONCURRENCY_LEVELS:
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

    for concurrency in CONCURRENCY_LEVELS:
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

    for concurrency in CONCURRENCY_LEVELS:
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

    for concurrency in CONCURRENCY_LEVELS:
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
    """Benchmark: Clone (sequential & concurrent fan-out).

    Fan-out is intentionally kept small (driven by CONCURRENCY_LEVELS, default
    1/2/4) so a single node does not exhaust its resource quota (CubeMaster
    error 130597 "no more resource"). Override via CUBE_PERF_CONCURRENCY,
    e.g. "1,5,10".
    """
    print(f"\n{'='*60}")
    print(" [Perf] Clone")
    print(f"{'='*60}")

    # (concurrency, n) pairs: a single-clone baseline plus one fan-out per
    # configured concurrency level, with the fan-out count equal to the level.
    workloads = [(1, 1)] + [(c, c) for c in CONCURRENCY_LEVELS if c > 1]

    for concurrency, n in workloads:
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

    for concurrency in CONCURRENCY_LEVELS:
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
    if Volume is None:
        return False
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

    for concurrency in CONCURRENCY_LEVELS:
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

    for concurrency in CONCURRENCY_LEVELS:
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

    for concurrency in CONCURRENCY_LEVELS:
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


def _grep_snapshot_bytes(sandbox_id: str) -> int:
    """Best-effort: read actual snapshot bytes written from the host vmm.log.

    Returns -1 when the log is unavailable (e.g. perf runs off-host), matching
    the standalone examples/snapshot-rollback-clone/bench_snapshot_dirty.py.
    """
    import re
    import subprocess

    vmm_log = os.environ.get("VMM_LOG", "/data/log/CubeVmm/vmm.log")
    pat = re.compile(r"(?:PagemapAnon|Soft-dirty) snapshot saved:\s+(\d+)\s+\w+ bytes written")
    try:
        out = subprocess.check_output(["grep", "-i", sandbox_id, vmm_log], text=True, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return -1
    for line in reversed(out.strip().splitlines()):
        m = pat.search(line)
        if m:
            return int(m.group(1))
    return -1


def bench_snapshot_dirty(cfg: Config) -> None:
    """Benchmark: snapshot latency vs dirty-page size (plus create-from restore).

    For each write size in DIRTY_SWEEP: write N MB into the sandbox's tmpfs
    (/dev/shm), snapshot it (timed), then restore a sandbox from that snapshot
    (timed, after a discarded warm-up restore). Mirrors the standalone
    examples/snapshot-rollback-clone/bench_snapshot_dirty.py. Skip with
    CUBE_SKIP_SNAPSHOT_DIRTY=1.
    """
    if os.environ.get("CUBE_SKIP_SNAPSHOT_DIRTY") == "1":
        skip("snapshot dirty-page scaling", "CUBE_SKIP_SNAPSHOT_DIRTY=1")
        return

    print(f"\n{'='*60}")
    print(" [Perf] Snapshot Latency vs Dirty-Page Size")
    print(f"{'='*60}")

    rounds = min(PERF_ROUNDS, 3)
    result = PerfResult(scenario="snapshot-dirty")

    for size_mb in DIRTY_SWEEP:
        snap_times: list[float] = []
        create_times: list[float] = []
        dirty_bytes_seen = -1

        for _ in range(rounds):
            sb = Sandbox.create(cfg.template_id, timeout=120, config=cfg)
            sid = sb.sandbox_id
            snap_id = None
            try:
                if size_mb > 0:
                    sb.run_code(f"open('/dev/shm/dirty','wb').write(b'x' * {size_mb * 1024 * 1024})")

                t0 = time.perf_counter()
                snap = sb.create_snapshot()
                snap_times.append((time.perf_counter() - t0) * 1000)
                snap_id = snap.snapshot_id
                try: sb.kill()
                except Exception: pass
                sb = None

                b = _grep_snapshot_bytes(sid)
                if b >= 0:
                    dirty_bytes_seen = b

                # Warm-up restore (discarded) to remove the cache-miss spike.
                warm = Sandbox.create(snap_id, timeout=120, config=cfg)
                try: warm.kill()
                except Exception: pass

                t1 = time.perf_counter()
                sb2 = Sandbox.create(snap_id, timeout=120, config=cfg)
                create_times.append((time.perf_counter() - t1) * 1000)
                try: sb2.kill()
                except Exception: pass
            finally:
                if sb is not None:
                    try: sb.kill()
                    except Exception: pass
                if snap_id is not None:
                    try: Sandbox.delete_snapshot(snap_id, config=cfg)
                    except Exception: pass
            if PERF_SETTLE:
                time.sleep(PERF_SETTLE)

        snap_avg = statistics.mean(snap_times) if snap_times else 0
        create_avg = statistics.mean(create_times) if create_times else 0
        dirty_mb = round(dirty_bytes_seen / (1024 * 1024), 1) if dirty_bytes_seen >= 0 else -1
        result.samples.append(PerfSample(
            label=f"dirty-{size_mb}mb",
            latency_ms=snap_avg,
            extra={"write_mb": size_mb, "dirty_mb": dirty_mb,
                   "snapshot_ms": round(snap_avg, 1), "create_from_ms": round(create_avg, 1)},
        ))
        print(f"  write={size_mb:>4}MB dirty≈{dirty_mb:>7}MB  snapshot={snap_avg:.1f}ms  create_from={create_avg:.1f}ms")

    PERF_RESULTS.append(result)


def _ivshmem_enabled() -> bool:
    """ivshmem is opt-in: it needs an ivshmem-enabled template and host access
    to ``/dev/shm/ivshmem-*`` (i.e. the benchmark must run on the CubeSandbox
    host, not a remote client)."""
    return os.environ.get("CUBE_RUN_IVSHMEM") == "1"


def bench_ivshmem(cfg: Config) -> None:
    """Benchmark: host-side ivshmem shared-memory mmap read/write.

    Skipped by default — set ``CUBE_RUN_IVSHMEM=1`` to enable. Requires an
    ivshmem-enabled template (``CUBE_IVSHMEM_TEMPLATE_ID``, falls back to the
    default template) and must run on the host so ``/dev/shm/ivshmem-{id}`` is
    reachable. Measures single-byte latency plus 100 B / 1 KB / 100 KB block
    write latency and throughput.
    """
    if not _ivshmem_enabled():
        skip("ivshmem shared-memory", "set CUBE_RUN_IVSHMEM=1 (needs an ivshmem-enabled template + host /dev/shm access)")
        return

    from .ivshmem import run_probe, wait_for_shm_file

    print(f"\n{'='*60}")
    print(" [Perf] ivshmem Shared-Memory (host-side mmap)")
    print(f"{'='*60}")

    template = os.environ.get("CUBE_IVSHMEM_TEMPLATE_ID") or cfg.template_id
    iterations = int(os.environ.get("CUBE_IVSHMEM_ITERATIONS", "10000"))

    sb = Sandbox.create(template, timeout=120, config=cfg)
    try:
        try:
            path = wait_for_shm_file(sb.sandbox_id)
        except FileNotFoundError as exc:
            skip("ivshmem shared-memory", str(exc))
            return

        results = run_probe(path, iterations)

        # Single-byte write: report latency (converted us -> ms) + ops/s.
        sbyte = results["single_byte"]
        r = PerfResult(scenario="ivshmem-write-1b")
        r.samples.append(PerfSample(
            label="ivshmem-1b",
            latency_ms=sbyte["latency_us"] / 1000.0,
            extra={"latency_us": sbyte["latency_us"], "ops_per_sec": sbyte["ops_per_sec"], "iterations": sbyte["iterations"]},
        ))
        PERF_RESULTS.append(r)
        print(f"  single-byte: {sbyte['latency_us']:.3f} us/op  {sbyte['ops_per_sec']:,} ops/s")

        # Block writes: report latency (us -> ms) + throughput (MB/s).
        for key, scenario, label in [
            ("block_100b", "ivshmem-write-100b", "100B"),
            ("block_1kb", "ivshmem-write-1kb", "1KB"),
            ("block_100kb", "ivshmem-write-100kb", "100KB"),
        ]:
            blk = results[key]
            r = PerfResult(scenario=scenario)
            r.samples.append(PerfSample(
                label=scenario,
                latency_ms=blk["latency_us"] / 1000.0,
                extra={"latency_us": blk["latency_us"], "throughput_mb": blk["throughput_mb"],
                       "block_size": blk["block_size"], "iterations": blk["iterations"]},
            ))
            PERF_RESULTS.append(r)
            print(f"  {label:>6} block: {blk['latency_us']:.3f} us/op  {blk['throughput_mb']} MB/s")
    finally:
        try:
            sb.kill()
        except Exception:
            pass


# Ordered list of benchmark functions for the CLI runner.
ALL_BENCHMARKS = [
    bench_template_create,
    bench_deployment_density,
    bench_snapshot_create,
    bench_snapshot_create_from,
    bench_snapshot_dirty,
    bench_rollback,
    bench_clone,
    bench_pause_resume,
    bench_volume_create,
    bench_volume_destroy,
    bench_volume_metadata,
    bench_volume_mount_sandbox,
    bench_ivshmem,
]


def collect_component_versions(cfg: Config) -> dict[str, str]:
    """Collect component version info for the HTML report environment section.

    Queries CubeAPI health endpoint and local system for component versions.
    """
    versions: dict[str, str] = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }

    # Try to get CubeAPI version from health endpoint
    try:
        import httpx

        headers = {}
        api_key = os.environ.get("CUBE_API_KEY") or os.environ.get("E2B_API_KEY", "")
        if api_key:
            headers["X-API-Key"] = api_key
        resp = httpx.get(f"{cfg.api_url}/health", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                for key in ("version", "commit", "build_time", "go_version"):
                    if key in data:
                        versions[f"cubeapi_{key}"] = str(data[key])
    except Exception:
        pass

    # Try to get SDK version
    try:
        import cubesandbox

        versions["sdk_version"] = cubesandbox.__version__
    except Exception:
        pass

    return versions


def run_all(cfg: Config) -> None:
    """Run all performance benchmarks in order."""
    # Print component versions
    versions = collect_component_versions(cfg)
    print("\n--- Component Versions ---")
    for k, v in sorted(versions.items()):
        print(f"  {k}: {v}")
    print()

    for bench_fn in ALL_BENCHMARKS:
        bench_fn(cfg)
