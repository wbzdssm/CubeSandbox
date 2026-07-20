# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""ivshmem scenario: host-side ivshmem shared-memory mmap read/write.

Skipped by default — set ``CUBE_RUN_IVSHMEM=1`` to enable. Needs an
ivshmem-enabled template and must run on the CubeSandbox host so
``/dev/shm/ivshmem-{id}`` is reachable. The low-level mmap probe lives in the
sibling ``probe`` module.
"""

from __future__ import annotations

import os

from cubesandbox import Config

from ...framework.registry import benchmark
from ...framework.runner import PERF_RESULTS, PerfResult, PerfSample, sandbox, skip
from .probe import run_probe, wait_for_shm_file


@benchmark("ivshmem", opt_in_env="CUBE_RUN_IVSHMEM",
           skip_reason="needs an ivshmem-enabled template + host /dev/shm access")
def bench_ivshmem(cfg: Config) -> None:
    """Benchmark: host-side ivshmem shared-memory mmap read/write.

    Skipped by default — set ``CUBE_RUN_IVSHMEM=1`` to enable. Requires an
    ivshmem-enabled template (``CUBE_IVSHMEM_TEMPLATE_ID``, falls back to the
    default template) and must run on the host so ``/dev/shm/ivshmem-{id}`` is
    reachable. Measures single-byte latency plus 100 B / 1 KB / 100 KB block
    write latency and throughput.
    """
    print(f"\n{'='*60}")
    print(" [Perf] ivshmem Shared-Memory (host-side mmap)")
    print(f"{'='*60}")

    template = os.environ.get("CUBE_IVSHMEM_TEMPLATE_ID") or cfg.template_id
    iterations = int(os.environ.get("CUBE_IVSHMEM_ITERATIONS", "10000"))

    with sandbox(cfg, template) as sb:
        try:
            path = wait_for_shm_file(sb.sandbox_id)
        except FileNotFoundError as exc:
            skip("ivshmem shared-memory", str(exc))
            return

        results = run_probe(path, iterations)

        # Single-byte write: report latency (converted us -> ms) + ops/s.
        sbyte = results["single_byte"]
        r = PerfResult(scenario="ivshmem-write-1b")
        r.samples.append(PerfSample(
            label="ivshmem-1b",
            latency_ms=sbyte["latency_us"] / 1000.0,
            extra={"latency_us": sbyte["latency_us"], "ops_per_sec": sbyte["ops_per_sec"], "iterations": sbyte["iterations"]},
        ))
        PERF_RESULTS.append(r)
        print(f"  single-byte: {sbyte['latency_us']:.3f} us/op  {sbyte['ops_per_sec']:,} ops/s")

        # Block writes: report latency (us -> ms) + throughput (MB/s).
        for key, scenario, label in [
            ("block_100b", "ivshmem-write-100b", "100B"),
            ("block_1kb", "ivshmem-write-1kb", "1KB"),
            ("block_100kb", "ivshmem-write-100kb", "100KB"),
        ]:
            blk = results[key]
            r = PerfResult(scenario=scenario)
            r.samples.append(PerfSample(
                label=scenario,
                latency_ms=blk["latency_us"] / 1000.0,
                extra={"latency_us": blk["latency_us"], "throughput_mb": blk["throughput_mb"],
                       "block_size": blk["block_size"], "iterations": blk["iterations"]},
            ))
            PERF_RESULTS.append(r)
            print(f"  {label:>6} block: {blk['latency_us']:.3f} us/op  {blk['throughput_mb']} MB/s")
