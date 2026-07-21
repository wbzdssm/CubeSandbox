# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Clone scenario: sequential & concurrent fan-out clone."""

from __future__ import annotations

import time

from cubesandbox import Config

from ...framework.config import CONCURRENCY_LEVELS, PERF_ROUNDS
from ...framework.registry import ReportSection, benchmark
from ...framework.runner import PERF_RESULTS, PerfResult, PerfSample, sandbox


@benchmark(
    "clone",
    report=ReportSection(
        table="clone",
        order=8,
        title_zh="克隆（Clone）",
        title_en="Clone",
        method_zh="调用 `POST /sandboxes/{id}/clone`，从一个运行中沙箱派生出 N 个新沙箱，完整保留源沙箱状态。表中 wall 为每轮整批耗时的多轮分布。",
        method_en="`POST /sandboxes/{id}/clone` forks N new sandboxes from one running box, preserving full state. The wall columns are a distribution over rounds.",
    ),
)
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
            with sandbox(cfg, timeout=300) as sb:
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
