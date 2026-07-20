# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot scenario: snapshot creation (single & concurrent)."""

from __future__ import annotations

from cubesandbox import Sandbox

from ...framework.registry import sandbox_benchmark
from ...framework.runner import snapshot_pool


@sandbox_benchmark(
    "snapshot-create",
    title="创建快照（并发）",
    header=" [Perf] Snapshot Creation",
    aliases=["snapshot"],
    pool=snapshot_pool,
    metrics=("avg", "p50", "p95", "max"),
)
def snapshot_create(sb: Sandbox, snaps) -> None:
    """Benchmark: snapshot a fresh sandbox (this one line *is* the measured op).

    The framework spins up / kills the throwaway box each round, drives the
    concurrency sweep, and deletes the collected snapshots after the level.
    """
    snaps.add(sb.create_snapshot().snapshot_id)
