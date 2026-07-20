# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot scenario: in-place rollback to a snapshot."""

from __future__ import annotations

from cubesandbox import Sandbox

from ...framework.registry import sandbox_benchmark


@sandbox_benchmark(
    "rollback",
    title="回滚（Rollback）",
    header=" [Perf] Rollback",
    fixture="snapshot",
)
def bench_rollback(sb: Sandbox, snap_id: str) -> None:
    """Benchmark: in-place rollback to a snapshot (this line *is* the measured op).

    The ``snapshot`` fixture spins up a fresh sandbox + one of its snapshots each
    round; the framework drives the concurrency sweep and tears both down
    (delete snapshot, then kill box) after every op.
    """
    sb.rollback(snap_id)
