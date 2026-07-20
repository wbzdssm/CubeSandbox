# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle scenario: deployment density (memory overhead per sandbox)."""

from __future__ import annotations

from cubesandbox import Config, Sandbox

from ...framework.config import DENSITY_COUNT
from ...framework.env import get_free_mem_gb
from ...framework.registry import benchmark
from ...framework.runner import PERF_RESULTS, PerfResult, PerfSample, sandbox_pool


@benchmark("density", opt_out_env="CUBE_SKIP_DENSITY")
def bench_deployment_density(cfg: Config) -> None:
    """Benchmark: Deployment density (memory overhead per sandbox)."""
    print(f"\n{'='*60}")
    print(" [Perf] Deployment Density (Memory Overhead)")
    print(f"{'='*60}")

    baseline = get_free_mem_gb()
    print(f"  Baseline free memory: {baseline:.1f} GiB")

    count = min(DENSITY_COUNT, 100)
    with sandbox_pool() as pool:
        for i in range(count):
            pool.add(Sandbox.create(cfg.template_id, timeout=300, config=cfg))
            if (i + 1) % max(count // 5, 1) == 0 or i == count - 1:
                free = get_free_mem_gb()
                overhead = round((baseline - free) / (i + 1) * 1024, 1) if (i + 1) > 0 else 0
                print(f"  sandboxes={i+1:>4}: free={free:.1f}GiB per_sandbox_overhead≈{overhead:.1f}MB")

        final_free = get_free_mem_gb()
        n = len(pool)
        per_overhead = round((baseline - final_free) / n * 1024, 1) if n else 0
        print(f"  Final: {n} sandboxes, per-sandbox overhead ≈ {per_overhead:.1f} MB")
        PERF_RESULTS.append(PerfResult(scenario="deployment-density", samples=[
            PerfSample(label=f"density-{n}", latency_ms=per_overhead,
                       extra={"count": n, "baseline_gb": baseline, "final_free_gb": final_free})
        ]))
