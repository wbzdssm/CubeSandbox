# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Clone scenario: sequential & concurrent fan-out clone."""

from __future__ import annotations

import time

from cubesandbox import Config

from ...framework.config import CONCURRENCY_LEVELS, PERF_ROUNDS
from ...framework.registry import benchmark
from ...framework.runner import PERF_RESULTS, PerfResult, PerfSample, sandbox


@benchmark("clone")
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
