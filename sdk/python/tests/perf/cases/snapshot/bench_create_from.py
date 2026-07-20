# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot scenario: create sandbox from snapshot (concurrent)."""

from __future__ import annotations

from cubesandbox import Config, Sandbox

from ...framework.registry import ReportGroup, benchmark, parallel_sweep
from ...framework.runner import sandbox_pool, snapshot_pool


@benchmark("snapshot-create-from",
           aliases=["snapshot-cold-start", "cold-start", "coldstart", "restore"],
           report=ReportGroup("基于快照启动沙箱"))
@parallel_sweep("snapshot-create-from", header=" [Perf] Create from Snapshot")
def bench_snapshot_create_from(cfg: Config, concurrency: int, n: int):
    """Benchmark: create sandbox from a snapshot (single & concurrent).

    Restoring a sandbox from a snapshot *is* the measured op. Set-up builds one
    base snapshot per level (kept in a ``snapshot_pool`` so it is deleted on
    exit); each measured op restores a fresh sandbox from it, collected in a
    ``sandbox_pool`` the sweep kills per level. The framework owns the loop,
    timing, and stats line — see ``@parallel_sweep``.
    """
    with snapshot_pool(cfg) as snaps, sandbox_pool() as boxes:
        base = Sandbox.create(cfg.template_id, timeout=120, config=cfg)
        snap_id = snaps.add(base.create_snapshot().snapshot_id)
        try:
            base.kill()
        except Exception:
            pass
        yield lambda: boxes.add(Sandbox.create(snap_id, timeout=120, config=cfg))
