# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Official CubeSandbox performance baseline data.

Two reference baselines:
- BMI5 (x86_64): Intel Xeon Platinum 8255C, 96 logical cores, 375 GiB DDR4
  Source: https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html
  Date: 2026-06-01, CubeSandbox v0.4.x

- Vera A1P (ARM64): NVIDIA Vera A1P, 176 logical cores, 768 GB LPDDR5x
  Source: 2026-07-15-cubesandbox-perf-benchmark-vera.md
  Date: 2026-07-15, CubeSandbox v0.5.1

All latencies are in milliseconds (ms).
"""

from __future__ import annotations

from typing import Any

# ===========================================================================
# BMI5 (x86_64) Baseline
# ===========================================================================

BASELINE_BMI5_ENV: dict[str, Any] = {
    "label": "BMI5 (x86_64)",
    "machine_type": "Tencent Cloud BMI5 (bare-metal, nested virtualization)",
    "os": "OpenCloudOS (TencentOS Server 4)",
    "kernel": "6.6.119",
    "arch": "x86_64",
    "cpu_model": "Intel(R) Xeon(R) Platinum 8255C @ 2.50GHz",
    "cpu_config": "2S × 24C × 2T = 96 logical cores",
    "memory": "375 GiB DDR4-2933 MT/s ECC",
    "disk": "3.84 TB Intel NVMe SSD (XFS, /data)",
    "sandbox_spec": "2 vCPU / 2 GiB",
    "image": "cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest",
    "storage": "CoW reflink (XFS, /data/cubelet/storage/)",
    "memory_tracking": "soft-dirty (/proc/PID/clear_refs)",
    "cube_version": "v0.4.x",
    "test_date": "2026-06-01",
}

BASELINE_BMI5_PERF: dict[str, dict[str, Any]] = {
    # 2.1 Template-based sandbox creation
    "template-create-c1": {
        "concurrency": 1, "n": 20,
        "avg": 47.8, "min": 43.5, "p95": 57.4, "max": 60.4, "wall": 1116, "per": 55.8,
        "throughput": "17.9 /s",
    },
    "template-create-c10": {
        "concurrency": 10, "n": 200,
        "avg": 88.7, "min": 45.8, "p95": 116.9, "max": 119.1, "wall": 1973, "per": 9.9,
        "throughput": "101.4 /s",
    },
    "template-create-c20": {
        "concurrency": 20, "n": 300,
        "avg": 98.1, "min": 47.7, "p95": 175.8, "max": 232.6, "wall": 1658, "per": 5.5,
        "throughput": "180.9 /s",
    },
    "template-create-c50": {
        "concurrency": 50, "n": 500,
        "avg": 276.1, "min": 60.6, "p95": 508.4, "max": 681.3, "wall": 3388, "per": 6.8,
        "throughput": "147.6 /s",
    },
    # 2.2 Deployment density
    "deployment-density": {
        "overhead_per_sandbox_mb": 25.7,
        "samples": [
            {"count": 100, "free_gb": 357.4, "overhead_mb": 21.5},
            {"count": 300, "free_gb": 352.5, "overhead_mb": 23.8},
            {"count": 500, "free_gb": 347.3, "overhead_mb": 25.0},
            {"count": 1000, "free_gb": 334.3, "overhead_mb": 25.7},
        ],
    },
    # 3.1 Snapshot create
    "snapshot-create-c1": {
        "concurrency": 1, "rounds": 5,
        "avg": 49.8, "min": 47.3, "p95": 54.1, "max": 54.1, "wall": 249, "per": 49.8,
    },
    "snapshot-create-c5": {
        "concurrency": 5, "rounds": 5,
        "wall_avg": 71.0, "wall_min": 62.7, "wall_p95": 81.0, "wall_max": 81.0,
        "per": 14.2,
    },
    "snapshot-create-c10": {
        "concurrency": 10, "rounds": 5,
        "wall_avg": 127.2, "wall_min": 79.6, "wall_p95": 155.6, "wall_max": 155.6,
        "per": 12.7,
    },
    # 3.2 Snapshot create — dirty-page scaling
    "snapshot-dirty": {
        "samples": [
            {"write_mb": 0, "dirty_mb": 7.1, "snapshot_ms": 45.7, "create_from_ms": 64.8},
            {"write_mb": 10, "dirty_mb": 38.9, "snapshot_ms": 75.7, "create_from_ms": 60.7},
            {"write_mb": 50, "dirty_mb": 120.7, "snapshot_ms": 107.7, "create_from_ms": 64.4},
            {"write_mb": 100, "dirty_mb": 195.0, "snapshot_ms": 138.6, "create_from_ms": 66.5},
            {"write_mb": 200, "dirty_mb": 296.7, "snapshot_ms": 174.2, "create_from_ms": 63.7},
            {"write_mb": 500, "dirty_mb": 602.5, "snapshot_ms": 289.4, "create_from_ms": 64.0},
            {"write_mb": 800, "dirty_mb": 908.4, "snapshot_ms": 392.8, "create_from_ms": 60.9},
            {"write_mb": 1024, "dirty_mb": 1136.4, "snapshot_ms": 486.9, "create_from_ms": 68.4},
        ],
    },
    # 3.3 Create from snapshot
    "snapshot-create-from-c1": {
        "concurrency": 1, "n": 1, "rounds": 3,
        "avg": 63.9, "min": 62.5, "p95": 66.1, "max": 66.1, "wall": 192, "per": 63.9,
    },
    "snapshot-create-from-c10": {
        "concurrency": 10, "n": 10, "rounds": 3,
        "wall_avg": 89.9, "wall_min": 84.0, "wall_p95": 93.6, "wall_max": 93.6, "per": 9.0,
    },
    "snapshot-create-from-c20": {
        "concurrency": 20, "n": 20, "rounds": 3,
        "wall_avg": 118.9, "wall_min": 92.7, "wall_p95": 167.1, "wall_max": 167.1, "per": 5.9,
    },
    "snapshot-create-from-c50": {
        "concurrency": 50, "n": 50, "rounds": 3,
        "wall_avg": 180.3, "wall_min": 135.1, "wall_p95": 260.7, "wall_max": 260.7, "per": 3.6,
    },
    # 3.4 Rollback
    "rollback-c1": {
        "concurrency": 1, "rounds": 5,
        "wall_avg": 81.6, "wall_min": 74.7, "wall_p95": 97.4, "wall_max": 97.4, "per": 81.6,
    },
    "rollback-c5": {
        "concurrency": 5, "rounds": 5,
        "wall_avg": 189.6, "wall_min": 161.8, "wall_p95": 243.2, "wall_max": 243.2, "per": 37.9,
    },
    "rollback-c10": {
        "concurrency": 10, "rounds": 5,
        "wall_avg": 266.1, "wall_min": 236.1, "wall_p95": 305.1, "wall_max": 305.1, "per": 26.6,
    },
    # 3.5 Clone
    "clone-c1-n1": {
        "concurrency": 1, "n": 1, "rounds": 5,
        "wall_avg": 219.6, "wall_min": 213.6, "wall_p95": 234.7, "wall_max": 234.7, "per": 219.6,
    },
    "clone-c10-n100": {
        "concurrency": 10, "n": 100, "rounds": 2,
        "wall_avg": 870.4, "wall_min": 860.6, "wall_p95": 880.2, "wall_max": 880.2, "per": 8.7,
    },
    "clone-c20-n100": {
        "concurrency": 20, "n": 100, "rounds": 2,
        "wall_avg": 638.6, "wall_min": 620.8, "wall_p95": 656.3, "wall_max": 656.3, "per": 6.4,
    },
    "clone-c50-n100": {
        "concurrency": 50, "n": 100, "rounds": 2,
        "wall_avg": 540.9, "wall_min": 491.3, "wall_p95": 590.5, "wall_max": 590.5, "per": 5.4,
    },
    # 3.6 Pause (full-memory-copy mode)
    "pause-c1": {
        "concurrency": 1, "rounds": 5,
        "wall_avg": 558.4, "wall_min": 530.8, "wall_p95": 590.3, "wall_max": 590.3, "per": 558.4,
        "note": "full-memory-copy mode",
    },
    "pause-c5": {
        "concurrency": 5, "rounds": 5,
        "wall_avg": 656.9, "wall_min": 621.9, "wall_p95": 683.2, "wall_max": 683.2, "per": 131.4,
    },
    "pause-c10": {
        "concurrency": 10, "rounds": 5,
        "wall_avg": 682.1, "wall_min": 674.1, "wall_p95": 699.3, "wall_max": 699.3, "per": 68.2,
    },
    # 3.6 Resume
    "resume-c1": {
        "concurrency": 1, "rounds": 5,
        "wall_avg": 41.8, "wall_min": 18.7, "wall_p95": 65.1, "wall_max": 65.1, "per": 41.8,
    },
    "resume-c5": {
        "concurrency": 5, "rounds": 5,
        "wall_avg": 28.2, "wall_min": 17.6, "wall_p95": 34.2, "wall_max": 34.2, "per": 5.6,
    },
    "resume-c10": {
        "concurrency": 10, "rounds": 5,
        "wall_avg": 35.7, "wall_min": 30.6, "wall_p95": 41.7, "wall_max": 41.7, "per": 3.6,
    },
}

# ===========================================================================
# Vera A1P (ARM64) Baseline
# ===========================================================================

BASELINE_VERA_ENV: dict[str, Any] = {
    "label": "Vera A1P (ARM64)",
    "machine_type": "NVIDIA Vera A1P (ARM64 / AArch64; CPU codename Olympus)",
    "os": "Ubuntu 24.04.3 LTS (Noble Numbat)",
    "kernel": "6.17.0-1018-nvidia-64k (PREEMPT_DYNAMIC, 64 KB pages)",
    "arch": "aarch64",
    "cpu_model": "NVIDIA Vera A1P (ARMv9, ~3.4 GHz, Boost off)",
    "cpu_config": "1S × 88C × 2T = 176 logical cores",
    "memory": "768 GB Samsung LPDDR5x @ 9600 MT/s, Multi-bit ECC (8 × 96 GB)",
    "disk": "Samsung SSD 9100 PRO 4 TB NVMe (ext4, /) + 100 GB XFS loop (/data/cubelet)",
    "sandbox_spec": "2 vCPU / 2 GiB",
    "image": "cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest",
    "storage": "CoW reflink (XFS loop, /data/cubelet/storage/)",
    "memory_tracking": "soft-dirty (/proc/PID/clear_refs)",
    "cube_version": "v0.5.1",
    "test_date": "2026-07-15",
}

BASELINE_VERA_PERF: dict[str, dict[str, Any]] = {
    # Template-based sandbox creation (Vera ARM64)
    "template-create-c1": {
        "concurrency": 1, "n": 20,
        "avg": 39.4, "min": 33.7, "p95": 42.8, "max": 43.2, "per": 45.7,
        "throughput": "21.9 /s",
    },
    "template-create-c10": {
        "concurrency": 10, "n": 200,
        "avg": 62.5, "min": 40.7, "p95": 73.8, "max": 78.4, "per": 7.0,
        "throughput": "142.2 /s",
    },
    "template-create-c20": {
        "concurrency": 20, "n": 300,
        "avg": 73.5, "min": 46.2, "p95": 86.0, "max": 89.5, "per": 4.2,
        "throughput": "240.7 /s",
    },
    "template-create-c50": {
        "concurrency": 50, "n": 500,
        "avg": 96.8, "min": 55.6, "p95": 136.7, "max": 156.6, "per": 2.3,
        "throughput": "440.5 /s",
    },
    # Deployment density (Vera ARM64, 64 KB pages, tmpfs 5 GB)
    "deployment-density": {
        "overhead_per_sandbox_mb": 90.1,
        "samples": [
            {"count": 100, "free_gb": 622, "overhead_mb": 61.4},
            {"count": 300, "free_gb": 606, "overhead_mb": 75.1},
            {"count": 500, "free_gb": 587, "overhead_mb": 84.0},
            {"count": 1000, "free_gb": 540, "overhead_mb": 90.1},
        ],
        "note": "64 KB page size kernel; tmpfs tuned to 5 GB for 1000-sandbox test",
    },
    # Snapshot create (Vera ARM64)
    "snapshot-create-c1": {
        "concurrency": 1, "rounds": 5,
        "wall_avg": 101.3, "wall_min": 97.5, "wall_p95": 108.7, "wall_max": 108.7, "per": 101.3,
    },
    "snapshot-create-c5": {
        "concurrency": 5, "rounds": 5,
        "wall_avg": 154.2, "wall_min": 145.7, "wall_p95": 174.6, "wall_max": 174.6, "per": 30.8,
    },
    "snapshot-create-c10": {
        "concurrency": 10, "rounds": 5,
        "wall_avg": 190.3, "wall_min": 186.3, "wall_p95": 193.6, "wall_max": 193.6, "per": 19.0,
    },
    # Snapshot dirty-page scaling (Vera ARM64, 64 KB pages)
    "snapshot-dirty": {
        "samples": [
            {"write_mb": 0, "dirty_mb": 45.2, "snapshot_ms": 95.3, "create_from_ms": 38.6},
            {"write_mb": 10, "dirty_mb": 121.2, "snapshot_ms": 114.5, "create_from_ms": 39.0},
            {"write_mb": 50, "dirty_mb": 202.8, "snapshot_ms": 106.6, "create_from_ms": 39.3},
            {"write_mb": 100, "dirty_mb": 285.6, "snapshot_ms": 120.6, "create_from_ms": 41.9},
            {"write_mb": 200, "dirty_mb": 387.0, "snapshot_ms": 135.6, "create_from_ms": 39.0},
            {"write_mb": 500, "dirty_mb": 694.2, "snapshot_ms": 152.4, "create_from_ms": 40.4},
            {"write_mb": 800, "dirty_mb": 1000.4, "snapshot_ms": 180.4, "create_from_ms": 41.7},
            {"write_mb": 1024, "dirty_mb": 1228.0, "snapshot_ms": 192.7, "create_from_ms": 38.9},
        ],
        "note": "64 KB page size; baseline anonymous memory ~45 MB dirty",
    },
    # Create from snapshot (Vera ARM64)
    "snapshot-create-from-c1": {
        "concurrency": 1, "n": 1, "rounds": 3,
        "wall_avg": 44.3, "wall_min": 39.0, "wall_p95": 53.1, "wall_max": 53.1, "per": 44.3,
    },
    "snapshot-create-from-c10": {
        "concurrency": 10, "n": 10, "rounds": 3,
        "wall_avg": 73.0, "wall_min": 66.5, "wall_p95": 80.8, "wall_max": 80.8, "per": 7.3,
    },
    "snapshot-create-from-c20": {
        "concurrency": 20, "n": 20, "rounds": 3,
        "wall_avg": 92.5, "wall_min": 88.2, "wall_p95": 99.1, "wall_max": 99.1, "per": 4.6,
    },
    # c50: not completed on Vera ARM64
    # Rollback (Vera ARM64)
    "rollback-c1": {
        "concurrency": 1, "rounds": 5,
        "wall_avg": 71.4, "wall_min": 60.5, "wall_p95": 84.4, "wall_max": 84.4, "per": 71.4,
    },
    "rollback-c5": {
        "concurrency": 5, "rounds": 5,
        "wall_avg": 116.6, "wall_min": 110.2, "wall_p95": 124.7, "wall_max": 124.7, "per": 23.3,
    },
    "rollback-c10": {
        "concurrency": 10, "rounds": 5,
        "wall_avg": 187.4, "wall_min": 181.5, "wall_p95": 195.6, "wall_max": 195.6, "per": 18.7,
    },
    # Clone (Vera ARM64)
    "clone-c1-n1": {
        "concurrency": 1, "n": 1, "rounds": 5,
        "wall_avg": 142.6, "wall_min": 138.0, "wall_p95": 147.0, "wall_max": 147.0, "per": 142.6,
    },
    "clone-c5-n5": {
        "concurrency": 5, "n": 5, "rounds": 3,
        "wall_avg": 185, "per": 37.0,
        "note": "summary only, no min/p95/max",
    },
    "clone-c10-n10": {
        "concurrency": 10, "n": 10, "rounds": 3,
        "wall_avg": 181, "per": 18.1,
        "note": "summary only, no min/p95/max",
    },
    "clone-c20-n20": {
        "concurrency": 20, "n": 20, "rounds": 3,
        "wall_avg": 192, "per": 9.6,
        "note": "summary only, no min/p95/max",
    },
    "clone-c50-n50": {
        "concurrency": 50, "n": 50, "rounds": 3,
        "wall_avg": 243, "per": 4.9,
        "note": "summary only, no min/p95/max",
    },
    # Pause (Vera ARM64, full-memory-copy mode)
    "pause-c1": {
        "concurrency": 1, "rounds": 5,
        "wall_avg": 236.8, "wall_min": 230.5, "wall_p95": 243.5, "wall_max": 243.5, "per": 236.8,
        "note": "full-memory-copy mode",
    },
    "pause-c5": {
        "concurrency": 5, "rounds": 5,
        "wall_avg": 272.3, "wall_min": 262.5, "wall_p95": 283.5, "wall_max": 283.5, "per": 54.5,
    },
    "pause-c10": {
        "concurrency": 10, "rounds": 5,
        "wall_avg": 280.2, "wall_min": 270.8, "wall_p95": 287.8, "wall_max": 287.8, "per": 28.0,
    },
    # Resume (Vera ARM64)
    "resume-c1": {
        "concurrency": 1, "rounds": 5,
        "wall_avg": 16.5, "wall_min": 9.1, "wall_p95": 19.1, "wall_max": 19.1, "per": 16.5,
    },
    "resume-c5": {
        "concurrency": 5, "rounds": 5,
        "wall_avg": 16.3, "wall_min": 10.6, "wall_p95": 20.5, "wall_max": 20.5, "per": 3.3,
    },
    "resume-c10": {
        "concurrency": 10, "rounds": 5,
        "wall_avg": 20.7, "wall_min": 13.2, "wall_p95": 24.8, "wall_max": 24.8, "per": 2.1,
    },
}

# ===========================================================================
# Aggregated exports (backward-compatible)
# ===========================================================================

# Default baseline (BMI5) for backward compatibility
BASELINE_ENVIRONMENT = BASELINE_BMI5_ENV
BASELINE_PERF = BASELINE_BMI5_PERF

# All baselines indexed by label
ALL_BASELINES: dict[str, dict[str, Any]] = {
    "BMI5 (x86_64)": {
        "env": BASELINE_BMI5_ENV,
        "perf": BASELINE_BMI5_PERF,
        "source": "https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html",
        "date": "2026-06-01",
    },
    "Vera A1P (ARM64)": {
        "env": BASELINE_VERA_ENV,
        "perf": BASELINE_VERA_PERF,
        "source": "2026-07-15-cubesandbox-perf-benchmark-vera.md",
        "date": "2026-07-15",
    },
}

BASELINE_SOURCE = "https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html"
BASELINE_SOURCE_DATE = "2026-06-01"
