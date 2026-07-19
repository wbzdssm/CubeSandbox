# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Host-side ivshmem shared-memory probe used by the perf suite.

Measures host access to ``/dev/shm/ivshmem-{sandbox_id}`` via ``mmap`` — the
same core measurement as ``examples/ivshmem/ivshmem_benchmark.py``, packaged
here so the ``perf`` suite stays self-contained. It measures *host-side* mmap
read/write only; it does not, by itself, prove guest communication.
"""

from __future__ import annotations

import mmap
import os
import time
from typing import Any

IVSHMEM_SIZE = 1024 * 1024
DEFAULT_SHM_WAIT_TIMEOUT = 60


def shm_path(sandbox_id: str) -> str:
    """Return the host path of a sandbox's ivshmem file."""
    return f"/dev/shm/ivshmem-{sandbox_id}"


def wait_for_shm_file(sandbox_id: str, timeout: int = DEFAULT_SHM_WAIT_TIMEOUT) -> str:
    """Wait until the ivshmem file for *sandbox_id* exists; return its path.

    Raises ``FileNotFoundError`` if it never appears within *timeout* seconds
    (e.g. the template is not ivshmem-enabled).
    """
    path = shm_path(sandbox_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return path
        time.sleep(1)
    raise FileNotFoundError(f"ivshmem file not created: {path}")


def _measure_single_byte(mm: mmap.mmap, iterations: int) -> dict[str, Any]:
    start = time.perf_counter()
    for i in range(iterations):
        mm[i % IVSHMEM_SIZE] = 65
    elapsed = time.perf_counter() - start
    return {
        "latency_us": round((elapsed / iterations) * 1_000_000, 3),
        "ops_per_sec": int(iterations / elapsed) if elapsed else 0,
        "iterations": iterations,
    }


def _measure_block(mm: mmap.mmap, block_size: int, iterations: int) -> dict[str, Any]:
    block = b"X" * block_size
    start = time.perf_counter()
    for i in range(iterations):
        offset = (i * block_size) % (IVSHMEM_SIZE - block_size)
        mm[offset : offset + block_size] = block
    elapsed = time.perf_counter() - start
    return {
        "block_size": block_size,
        "latency_us": round((elapsed / iterations) * 1_000_000, 3),
        "throughput_mb": round((block_size * iterations) / elapsed / (1024 * 1024), 2) if elapsed else 0,
        "iterations": iterations,
    }


def run_probe(ivshmem_path: str, iterations: int = 10000) -> dict[str, dict[str, Any]]:
    """Run the host-side mmap probe against *ivshmem_path*.

    Returns a mapping of measurement name -> metrics dict. Raises
    ``FileNotFoundError`` if the path does not exist.
    """
    if not os.path.exists(ivshmem_path):
        raise FileNotFoundError(f"ivshmem file not found: {ivshmem_path}")

    with open(ivshmem_path, "r+b", buffering=0) as f, mmap.mmap(f.fileno(), IVSHMEM_SIZE) as mm:
        return {
            "single_byte": _measure_single_byte(mm, iterations),
            "block_100b": _measure_block(mm, 100, iterations),
            "block_1kb": _measure_block(mm, 1024, iterations),
            "block_100kb": _measure_block(mm, 100 * 1024, min(iterations, 1000)),
        }
