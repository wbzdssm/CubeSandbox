# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Volume scenarios: create / destroy / metadata / mounted-sandbox (E2E).

All four are **skipped by default** because the backend ``/volumes`` endpoint
is part of the SDK/docs-first roadmap and may not be deployed yet. Set
``CUBE_RUN_VOLUME=1`` to enable them. ``available=Volume is not None`` also
skips unconditionally when the SDK build does not export ``Volume``.

Every scenario leans on ``volume_pool()`` for cleanup (and ``sandbox_pool()``
for the mounted-sandbox case), so none of them hand-writes the old
``created=[]`` + ``lock`` + ``try/finally: Volume.destroy(...)`` boilerplate.
"""

from __future__ import annotations

import queue
import time
from uuid import uuid4

from cubesandbox import Config, Sandbox

try:
    from cubesandbox import Volume
except ImportError:
    Volume = None  # type: ignore[assignment]

from ...framework.config import PERF_ROUNDS
from ...framework.registry import benchmark, parallel_sweep
from ...framework.runner import (
    PERF_RESULTS,
    PerfResult,
    PerfSample,
    sandbox_pool,
    volume_pool,
)

_VOLUME_SKIP_HINT = "backend /volumes not available yet"


@benchmark("volume-create", aliases=["volume"],
           opt_in_env="CUBE_RUN_VOLUME", available=Volume is not None,
           skip_reason=_VOLUME_SKIP_HINT)
@parallel_sweep("volume-create", header=" [Perf] Volume Create")
def bench_volume_create(cfg: Config, concurrency: int, n: int):
    """Benchmark: Volume.create latency (single & concurrent).

    Skipped by default — set CUBE_RUN_VOLUME=1 to enable (the backend
    /volumes endpoint is part of the SDK/docs-first roadmap). Creation *is*
    the measured op; the resulting volumes are just cleanup debt the pool owns.
    """
    with volume_pool(cfg) as pool:
        yield lambda: pool.add(Volume.create(f"perf-c-{uuid4().hex[:12]}", config=cfg))


@benchmark("volume-destroy", aliases=["volume"],
           opt_in_env="CUBE_RUN_VOLUME", available=Volume is not None,
           skip_reason=_VOLUME_SKIP_HINT)
@parallel_sweep("volume-destroy", header=" [Perf] Volume Destroy", warmup=0)
def bench_volume_destroy(cfg: Config, concurrency: int, n: int):
    """Benchmark: Volume.destroy latency (single & concurrent).

    Skipped by default — set CUBE_RUN_VOLUME=1 to enable. Each measured op
    consumes one of exactly *n* pre-created volumes, so ``warmup=0`` (a warm-up
    op would eat into that fixed pool and starve the timed rounds). The pool
    destroys any leftovers on exit; re-destroying an already-destroyed volume
    is a harmless no-op.
    """
    with volume_pool(cfg) as pool:
        vq: queue.Queue = queue.Queue()
        for _ in range(n):
            vq.put(pool.add(Volume.create(f"perf-d-{uuid4().hex[:12]}", config=cfg)))

        def destroy_one() -> None:
            Volume.destroy(vq.get().volume_id, config=cfg)

        yield destroy_one


@benchmark("volume-metadata", aliases=["volume"],
           opt_in_env="CUBE_RUN_VOLUME", available=Volume is not None,
           skip_reason=_VOLUME_SKIP_HINT)
def bench_volume_metadata(cfg: Config) -> None:
    """Benchmark: Volume metadata ops — list / get_info / connect.

    Skipped by default — set CUBE_RUN_VOLUME=1 to enable. Sequential per-op
    timing (not a concurrency sweep), so it keeps its own loop but still leans
    on ``volume_pool()`` for the single volume's cleanup.
    """
    print(f"\n{'='*60}")
    print(" [Perf] Volume Metadata Ops")
    print(f"{'='*60}")

    with volume_pool(cfg) as pool:
        vid = pool.add(Volume.create(f"perf-meta-{uuid4().hex[:12]}", config=cfg)).volume_id
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


@benchmark("volume-mount-sandbox", aliases=["volume"],
           opt_in_env="CUBE_RUN_VOLUME", available=Volume is not None,
           skip_reason=_VOLUME_SKIP_HINT)
@parallel_sweep("volume-mount-sandbox", warmup=0,
                header=" [Perf] Sandbox Creation with Mounted Volume (E2E)")
def bench_volume_mount_sandbox(cfg: Config, concurrency: int, n: int):
    """Benchmark: end-to-end sandbox creation with a mounted volume
    (volume create + Sandbox.create(volume_mounts=[...])).

    Skipped by default — set CUBE_RUN_VOLUME=1 to enable. Each measured op
    consumes one of exactly *n* pre-created volumes, so ``warmup=0``. Volumes
    are the outer resource: the nested ``sandbox_pool()`` kills the sandboxes on
    its (inner) exit *before* ``volume_pool()`` destroys the volumes.
    """
    with volume_pool(cfg) as vpool:
        vq: queue.Queue = queue.Queue()
        for _ in range(n):
            vq.put(vpool.add(Volume.create(f"perf-m-{uuid4().hex[:12]}", config=cfg)))

        with sandbox_pool() as pool:
            def create_mounted() -> None:
                vol = vq.get()
                pool.add(Sandbox.create(
                    cfg.template_id,
                    timeout=120,
                    volume_mounts=[vol.mount("/workspace")],
                    config=cfg,
                ))

            yield create_mounted
