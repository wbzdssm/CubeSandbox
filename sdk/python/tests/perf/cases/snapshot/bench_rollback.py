# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot scenario: in-place rollback to a snapshot."""

from __future__ import annotations

from cubesandbox import Sandbox

from ...framework.registry import ReportChart, ReportSection, sandbox_benchmark


@sandbox_benchmark(
    "rollback",
    header=" [Perf] Rollback",
    fixture="snapshot",
    report=ReportSection(
        table="latency",
        order=7,
        title_zh="回滚（Rollback）",
        title_en="Rollback",
        method_zh="对运行中沙箱调用 `POST /sandboxes/{id}/rollback`，将内存和文件系统状态原地恢复至指定快照。CubeSandbox 只允许沙箱回滚到自己创建的 checkpoint，故每个并发沙箱独立完成「打快照 + 回滚」全流程。",
        method_en="`POST /sandboxes/{id}/rollback` restores memory + filesystem in place to a snapshot. Each concurrent sandbox does its own snapshot-then-rollback (a box may only roll back to its own checkpoint).",
        noun_zh="回滚",
        noun_en="rollback",
        charts=(ReportChart("回滚（Rollback）"),),
    ),
)
def bench_rollback(sb: Sandbox, snap_id: str) -> None:
    """Benchmark: in-place rollback to a snapshot (this line *is* the measured op).

    The ``snapshot`` fixture spins up a fresh sandbox + one of its snapshots each
    round; the framework drives the concurrency sweep and tears both down
    (delete snapshot, then kill box) after every op.
    """
    sb.rollback(snap_id)
