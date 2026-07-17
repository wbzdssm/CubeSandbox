# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Official CubeSandbox performance baseline data.

Source: https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html

These values serve as reference baselines for HTML report comparison.
All latencies are in milliseconds (ms).
"""

from __future__ import annotations

from typing import Any

BASELINE_ENVIRONMENT: dict[str, Any] = {
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
}

# Baseline performance results keyed by scenario name.
# Each entry: {"avg": ..., "min": ..., "p95": ..., "max": ..., "wall": ..., "per": ..., "concurrency": ...}
BASELINE_PERF: dict[str, dict[str, Any]] = {
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
        "avg": None, "min": None, "p95": None, "max": None,  # per-operation not reported
        "wall_avg": 71.0, "wall_min": 62.7, "wall_p95": 81.0, "wall_max": 81.0,
        "per": 14.2,
    },
    "snapshot-create-c10": {
        "concurrency": 10, "rounds": 5,
        "avg": None, "min": None, "p95": None, "max": None,
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
    # 3.6 Pause
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

BASELINE_SOURCE = "https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html"
BASELINE_SOURCE_DATE = "2026-06-01"
