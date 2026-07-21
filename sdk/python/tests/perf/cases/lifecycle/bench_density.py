# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle scenario: deployment density (memory overhead per sandbox)."""

from __future__ import annotations

from cubesandbox import Config, Sandbox
from cubesandbox._exceptions import ApiError

from ...framework.config import DENSITY_COUNT
from ...framework.env import get_free_mem_gb
from ...framework.registry import ReportSection, benchmark
from ...framework.runner import PERF_RESULTS, PerfResult, PerfSample, sandbox_pool

# CubeMaster error code returned when a node cannot admit any more sandboxes.
_NO_MORE_RESOURCE_CODE = 130597


def _is_resource_exhausted(exc: ApiError) -> bool:
    """Whether an ApiError means the node hit its capacity ceiling.

    For the density test, running out of node resources is the *expected*
    stopping condition (we pack sandboxes until the node refuses more), not a
    failure — so we detect it and end the ramp-up gracefully.
    """
    return exc.status_code == _NO_MORE_RESOURCE_CODE or "no more resource" in str(exc).lower()


@benchmark(
    "density",
    opt_out_env="CUBE_SKIP_DENSITY",
    report=ReportSection(
        table="density",
        order=3,
        title_zh="单机部署密度（内存开销）",
        title_en="Deployment Density (Memory Overhead)",
        method_zh="累积启动沙箱，通过可用内存变化计算单 VM 均摊开销，直到达到上限或节点资源耗尽。",
        method_en="ramp up sandboxes and derive per-VM overhead from the change in free memory, until the cap or node capacity is hit.",
    ),
)
def bench_deployment_density(cfg: Config) -> None:
    """Benchmark: Deployment density (memory overhead per sandbox).

    Ramps up sandboxes one by one until either ``CUBE_DENSITY_COUNT`` is
    reached or the node runs out of resources (CubeMaster error 130597). The
    latter is a normal end-of-test condition, so the result is still recorded
    with whatever count was achieved rather than aborting the whole suite.
    """
    print(f"\n{'='*60}")
    print(" [Perf] Deployment Density (Memory Overhead)")
    print(f"{'='*60}")

    baseline = get_free_mem_gb()
    print(f"  Baseline free memory: {baseline:.1f} GiB")

    count = min(DENSITY_COUNT, 100)
    stopped_reason = ""
    with sandbox_pool() as pool:
        for i in range(count):
            try:
                pool.add(Sandbox.create(cfg.template_id, timeout=300, config=cfg))
            except ApiError as exc:
                if _is_resource_exhausted(exc):
                    stopped_reason = (
                        f"node capacity reached at {len(pool)} sandboxes "
                        f"(code {exc.status_code})"
                    )
                    print(f"  ! {stopped_reason}; stopping density ramp-up gracefully")
                    break
                raise
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
                       extra={"count": n, "baseline_gb": baseline, "final_free_gb": final_free,
                              "stopped_reason": stopped_reason})
        ]))
