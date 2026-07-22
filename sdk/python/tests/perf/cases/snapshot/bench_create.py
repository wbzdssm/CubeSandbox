# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot scenario: snapshot creation (single & concurrent)."""

from __future__ import annotations

from cubesandbox import Sandbox

from ...framework.registry import ReportChart, ReportSection, sandbox_benchmark
from ...framework.runner import snapshot_pool


@sandbox_benchmark(
    "snapshot-create",
    header=" [Perf] Snapshot Creation",
    aliases=["snapshot"],
    pool=snapshot_pool,
    metrics=("avg", "p50", "p95", "max"),
    report=ReportSection(
        table="latency",
        order=4,
        title_zh="创建快照（并发）",
        title_en="Snapshot Creation (Concurrency)",
        method_zh="对多个运行中沙箱并发调用 `POST /sandboxes/{id}/snapshots`，测量整批 wall time 与单次延迟分布。",
        method_en="concurrently `POST /sandboxes/{id}/snapshots` against N running sandboxes; measure batch wall time and per-op latency.",
        noun_zh="快照制作",
        noun_en="snapshot",
        charts=(ReportChart("创建快照（并发）"),),
    ),
)
def snapshot_create(sb: Sandbox, snaps) -> None:
    """Benchmark: snapshot a fresh sandbox (this one line *is* the measured op).

    The framework spins up / kills the throwaway box each round, drives the
    concurrency sweep, and deletes the collected snapshots after the level.
    """
    snaps.add(sb.create_snapshot().snapshot_id)
