# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle scenario: pause & resume latency."""

from __future__ import annotations

import statistics
import time

from cubesandbox import Config, Sandbox

from ...framework.config import CONCURRENCY_LEVELS, PERF_ROUNDS
from ...framework.registry import ReportChart, ReportSection, benchmark
from ...framework.runner import PERF_RESULTS, PerfResult, PerfSample, percentile, sandbox


@benchmark(
    "pause-resume",
    aliases=["pause", "resume"],
    report=ReportSection(
        table="pause_resume",
        order=9,
        title_zh="暂停与恢复（Pause & Resume）",
        title_en="Pause & Resume",
        method_zh="并发调用 `pause` 将沙箱内存写入持久化存储，再经 `connect` 恢复。当前 Pause 采用 **full-memory-copy** 模式，耗时与沙箱总内存量线性相关；后续将升级为 soft-dirty 增量模式。",
        method_en="`pause` persists sandbox memory, then `connect` resumes. Pause currently uses **full-memory-copy** (latency scales with total sandbox memory); a soft-dirty incremental mode is planned.",
        charts=(
            ReportChart("暂停（Pause）", prefix="pause"),
            ReportChart("恢复（Resume）", prefix="resume"),
        ),
    ),
)
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
            with sandbox(cfg) as sb:
                start = time.perf_counter()
                sb.pause(wait=False)
                pause_latencies.append((time.perf_counter() - start) * 1000)

                time.sleep(0.5)
                start = time.perf_counter()
                Sandbox.connect(sb.sandbox_id, config=cfg)
                resume_latencies.append((time.perf_counter() - start) * 1000)

        for label, lats in [("pause", pause_latencies), ("resume", resume_latencies)]:
            result = PerfResult(scenario=f"{label}-c{concurrency}")
            for lat in lats:
                result.samples.append(PerfSample(label=label, latency_ms=lat, concurrency=concurrency))
            PERF_RESULTS.append(result)

            avg = statistics.mean(lats) if lats else 0
            p95_val = percentile(lats, 95) if lats else 0
            print(f"  {label:>6} c={concurrency:>2}: avg={avg:.1f}ms min={min(lats) if lats else 0:.1f}ms "
                  f"p95={p95_val:.1f}ms max={max(lats) if lats else 0:.1f}ms")
