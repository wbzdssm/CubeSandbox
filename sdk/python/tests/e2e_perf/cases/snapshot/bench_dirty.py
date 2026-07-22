# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot scenario: snapshot latency vs dirty-page size (+ create-from restore)."""

from __future__ import annotations

import os
import statistics
import time

from cubesandbox import Config, Sandbox

from ...framework.config import DIRTY_SWEEP, PERF_ROUNDS, PERF_SETTLE
from ...framework.registry import ReportSection, benchmark
from ...framework.runner import PERF_RESULTS, PerfResult, PerfSample


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


@benchmark(
    "snapshot-dirty",
    aliases=["dirty"],
    opt_out_env="CUBE_SKIP_SNAPSHOT_DIRTY",
    report=ReportSection(
        table="dirty",
        order=5,
        star=True,
        title_zh="快照耗时 vs 脏页大小",
        title_en="Snapshot Latency vs Dirty-Page Size",
        method_zh="在沙箱内写入不同大小的数据（0~1024 MB）以控制脏页量，分别测量快照制作耗时和基于该快照恢复沙箱的耗时。**这是区分不同架构内存页处理效率的核心场景。** 「实测脏页」列取自 host 侧 `vmm.log`，off-host 运行时显示为「未知」。",
        method_en='write varying amounts of data (0~1024 MB) inside the sandbox to control dirty-page volume, then measure snapshot latency and create-from-snapshot latency. The core scenario for comparing memory-page handling across architectures. "Dirty Page" is read from the host `vmm.log` (shows "unknown" when running off-host).',
    ),
)
def bench_snapshot_dirty(cfg: Config) -> None:
    """Benchmark: snapshot latency vs dirty-page size (plus create-from restore).

    For each write size in DIRTY_SWEEP: write N MB into the sandbox's tmpfs
    (/dev/shm), snapshot it (timed), then restore a sandbox from that snapshot
    (timed, after a discarded warm-up restore). Mirrors the standalone
    examples/snapshot-rollback-clone/bench_snapshot_dirty.py. Skip with
    CUBE_SKIP_SNAPSHOT_DIRTY=1.
    """
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
