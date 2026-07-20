# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Official CubeSandbox performance baseline data.

Four reference baselines from the iWiki cross-machine comparison:
- BMI5 (x86_64): Intel Xeon Platinum 8255C, 96 logical cores, 375 GiB DDR4
- BMSA9 (x86_64): newer x86 bare-metal, 6.6.119-47.8 kernel
- Vera A1P (ARM64): NVIDIA Vera A1P, 176 logical cores, 768 GB LPDDR5x
- Kunpeng 920 (ARM64): Huawei Kunpeng 920, 6.6.119-50.12 kernel

Source: https://iwiki.woa.com/p/4026284308 (cross-machine perf comparison)

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
    "os": "OpenCloudOS 6.6.119-49.6",
    "kernel": "6.6.119-49.6",
    "arch": "x86_64",
    "cpu_model": "Intel(R) Xeon(R) Platinum 8255C @ 2.50GHz",
    "cpu_config": "2S × 24C × 2T = 96 logical cores",
    "memory": "375 GiB DDR4-2933 MT/s ECC",
    "disk": "3.84 TB Intel NVMe SSD (XFS, /data)",
    "sandbox_spec": "2 vCPU / 2 GiB",
    "cube_version": "v0.4.x",
    "test_date": "2026-06-01",
}

BASELINE_BMI5_PERF: dict[str, dict[str, Any]] = {
    # --- Template-based sandbox creation ---
    "template-create-c1": {"concurrency": 1, "n": 20, "avg": 47.8, "min": 43.5, "p95": 57.4, "max": 60.4, "per": 55.8, "throughput": "17.9 /s"},
    "template-create-c10": {"concurrency": 10, "n": 200, "avg": 88.7, "min": 45.8, "p95": 116.9, "max": 119.1, "per": 9.9, "throughput": "101.4 /s"},
    "template-create-c20": {"concurrency": 20, "n": 300, "avg": 98.1, "min": 47.7, "p95": 175.8, "max": 232.6, "per": 5.5, "throughput": "180.9 /s"},
    "template-create-c50": {"concurrency": 50, "n": 500, "avg": 276.1, "min": 60.6, "p95": 508.4, "max": 681.3, "per": 6.8, "throughput": "147.6 /s"},
    # --- Deployment density ---
    "deployment-density": {
        "overhead_per_sandbox_mb": 25.7,
        "samples": [
            {"count": 100, "free_gb": 357.4, "overhead_mb": 21.5},
            {"count": 300, "free_gb": 352.5, "overhead_mb": 23.8},
            {"count": 500, "free_gb": 347.3, "overhead_mb": 25.0},
            {"count": 1000, "free_gb": 334.3, "overhead_mb": 25.7},
        ],
    },
    # --- Snapshot create ---
    "snapshot-create-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 49.8, "wall_min": 47.3, "wall_p95": 54.1, "wall_max": 54.1, "per": 49.8},
    "snapshot-create-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 71.0, "wall_min": 62.7, "wall_p95": 81.0, "wall_max": 81.0, "per": 14.2},
    "snapshot-create-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 127.2, "wall_min": 79.6, "wall_p95": 155.6, "wall_max": 155.6, "per": 12.7},
    # --- Snapshot dirty-page scaling ---
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
    # --- Create from snapshot ---
    "snapshot-create-from-c1": {"concurrency": 1, "n": 1, "rounds": 3, "wall_avg": 63.9, "wall_min": 62.5, "wall_p95": 66.1, "wall_max": 66.1, "per": 63.9},
    "snapshot-create-from-c10": {"concurrency": 10, "n": 10, "rounds": 3, "wall_avg": 89.9, "wall_min": 84.0, "wall_p95": 93.6, "wall_max": 93.6, "per": 9.0},
    "snapshot-create-from-c20": {"concurrency": 20, "n": 20, "rounds": 3, "wall_avg": 118.9, "wall_min": 92.7, "wall_p95": 167.1, "wall_max": 167.1, "per": 5.9},
    "snapshot-create-from-c50": {"concurrency": 50, "n": 50, "rounds": 3, "wall_avg": 180.3, "wall_min": 135.1, "wall_p95": 260.7, "wall_max": 260.7, "per": 3.6},
    # --- Rollback ---
    "rollback-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 81.6, "wall_min": 74.7, "wall_p95": 97.4, "wall_max": 97.4, "per": 81.6},
    "rollback-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 189.6, "wall_min": 161.8, "wall_p95": 243.2, "wall_max": 243.2, "per": 37.9},
    "rollback-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 266.1, "wall_min": 236.1, "wall_p95": 305.1, "wall_max": 305.1, "per": 26.6},
    # --- Clone ---
    "clone-c1-n1": {"concurrency": 1, "n": 1, "rounds": 5, "wall_avg": 219.6, "wall_min": 213.6, "wall_p95": 234.7, "wall_max": 234.7, "per": 219.6},
    "clone-c10-n100": {"concurrency": 10, "n": 100, "rounds": 2, "wall_avg": 870.4, "wall_min": 860.6, "wall_p95": 880.2, "wall_max": 880.2, "per": 8.7},
    "clone-c20-n100": {"concurrency": 20, "n": 100, "rounds": 2, "wall_avg": 638.6, "wall_min": 620.8, "wall_p95": 656.3, "wall_max": 656.3, "per": 6.4},
    "clone-c50-n100": {"concurrency": 50, "n": 100, "rounds": 2, "wall_avg": 540.9, "wall_min": 491.3, "wall_p95": 590.5, "wall_max": 590.5, "per": 5.4},
    # --- Pause ---
    "pause-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 558.4, "wall_min": 530.8, "wall_p95": 590.3, "wall_max": 590.3, "per": 558.4, "note": "full-memory-copy mode"},
    "pause-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 656.9, "wall_min": 621.9, "wall_p95": 683.2, "wall_max": 683.2, "per": 131.4},
    "pause-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 682.1, "wall_min": 674.1, "wall_p95": 699.3, "wall_max": 699.3, "per": 68.2},
    # --- Resume ---
    "resume-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 41.8, "wall_min": 18.7, "wall_p95": 65.1, "wall_max": 65.1, "per": 41.8},
    "resume-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 28.2, "wall_min": 17.6, "wall_p95": 34.2, "wall_max": 34.2, "per": 5.6},
    "resume-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 35.7, "wall_min": 30.6, "wall_p95": 41.7, "wall_max": 41.7, "per": 3.6},
}

# ===========================================================================
# BMSA9 (x86_64) Baseline
# ===========================================================================

BASELINE_BMSA9_ENV: dict[str, Any] = {
    "label": "BMSA9 (x86_64)",
    "machine_type": "BMSA9 bare-metal (x86_64)",
    "os": "TencentOS Server 4",
    "kernel": "6.6.119-47.8.tl4",
    "arch": "x86_64",
    "cpu_model": "BMSA9 x86_64",
    "cpu_config": "—",
    "memory": "—",
    "disk": "—",
    "sandbox_spec": "2 vCPU / 2 GiB",
    "cube_version": "v0.5.x",
    "test_date": "2026-07",
}

BASELINE_BMSA9_PERF: dict[str, dict[str, Any]] = {
    "template-create-c1": {"concurrency": 1, "n": 20, "avg": 41.8, "min": 37.9, "p95": 44.3, "max": 58.5, "per": 50.2, "throughput": "19.9 /s"},
    "template-create-c10": {"concurrency": 10, "n": 200, "avg": 45.2, "min": 38.9, "p95": 58.7, "max": 68.7, "per": 5.4, "throughput": "185.3 /s"},
    "template-create-c20": {"concurrency": 20, "n": 300, "avg": 50.0, "min": 37.6, "p95": 68.7, "max": 74.1, "per": 3.1, "throughput": "319.0 /s"},
    "template-create-c50": {"concurrency": 50, "n": 500, "avg": 92.4, "min": 43.0, "p95": 154.7, "max": 181.5, "per": 2.3, "throughput": "441.4 /s"},
    "deployment-density": {
        "overhead_per_sandbox_mb": 28.75,
        "samples": [
            {"count": 100, "used_mb": 109708, "overhead_mb": 19.77},
            {"count": 300, "used_mb": 114644, "overhead_mb": 23.04},
            {"count": 500, "used_mb": 120195, "overhead_mb": 24.93},
            {"count": 1000, "used_mb": 136484, "overhead_mb": 28.75},
        ],
        "note": "measured via used mem (MiB) delta",
    },
    "snapshot-create-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 88.7, "wall_min": 77.9, "wall_p95": 100.0, "wall_max": 100.0, "per": 88.7},
    "snapshot-create-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 117.1, "wall_min": 107.7, "wall_p95": 127.6, "wall_max": 127.6, "per": 23.4},
    "snapshot-create-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 144.6, "wall_min": 138.4, "wall_p95": 155.3, "wall_max": 155.3, "per": 14.5},
    "snapshot-dirty": {
        "samples": [
            {"write_mb": 0, "dirty_mb": 8.4, "snapshot_ms": 94.0, "create_from_ms": 78.4},
            {"write_mb": 10, "dirty_mb": 42.7, "snapshot_ms": 100.4, "create_from_ms": 78.1},
            {"write_mb": 50, "dirty_mb": 123.8, "snapshot_ms": 122.5, "create_from_ms": 73.4},
            {"write_mb": 100, "dirty_mb": 196.7, "snapshot_ms": 139.4, "create_from_ms": 83.1},
            {"write_mb": 200, "dirty_mb": 298.5, "snapshot_ms": 160.2, "create_from_ms": 71.9},
            {"write_mb": 500, "dirty_mb": 605.0, "snapshot_ms": 206.5, "create_from_ms": 75.2},
            {"write_mb": 800, "dirty_mb": 910.6, "snapshot_ms": 257.9, "create_from_ms": 114.9},
            {"write_mb": 1024, "dirty_mb": 1138.6, "snapshot_ms": 279.0, "create_from_ms": 79.6},
        ],
    },
    "snapshot-create-from-c1": {"concurrency": 1, "n": 1, "rounds": 3, "wall_avg": 69.7, "wall_min": 65.9, "wall_p95": 72.8, "wall_max": 72.8, "per": 69.7},
    "snapshot-create-from-c10": {"concurrency": 10, "n": 10, "rounds": 3, "wall_avg": 98.1, "wall_min": 85.0, "wall_p95": 107.6, "wall_max": 107.6, "per": 9.8},
    "snapshot-create-from-c20": {"concurrency": 20, "n": 20, "rounds": 3, "wall_avg": 106.5, "wall_min": 102.3, "wall_p95": 112.9, "wall_max": 112.9, "per": 5.3},
    "snapshot-create-from-c50": {"concurrency": 50, "n": 50, "rounds": 3, "wall_avg": 141.2, "wall_min": 135.4, "wall_p95": 151.7, "wall_max": 151.7, "per": 2.8},
    "rollback-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 141.7, "wall_min": 104.6, "wall_p95": 181.6, "wall_max": 181.6, "per": 141.7},
    "rollback-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 213.6, "wall_min": 194.8, "wall_p95": 261.1, "wall_max": 261.1, "per": 42.7},
    "rollback-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 242.4, "wall_min": 208.0, "wall_p95": 276.1, "wall_max": 276.1, "per": 24.2},
    "clone-c1-n1": {"concurrency": 1, "n": 1, "rounds": 5, "wall_avg": 433.1, "wall_min": 315.9, "wall_p95": 833.5, "wall_max": 833.5, "per": 433.1},
    "clone-c10-n100": {"concurrency": 10, "n": 100, "rounds": 2, "wall_avg": 849.5, "wall_min": 843.4, "wall_p95": 855.6, "wall_max": 855.6, "per": 8.5},
    "clone-c20-n100": {"concurrency": 20, "n": 100, "rounds": 2, "wall_avg": 755.1, "wall_min": 627.1, "wall_p95": 883.1, "wall_max": 883.1, "per": 7.6},
    "clone-c50-n100": {"concurrency": 50, "n": 100, "rounds": 2, "wall_avg": 1013.2, "wall_min": 943.5, "wall_p95": 1082.9, "wall_max": 1082.9, "per": 10.1},
    "pause-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 236.8, "wall_min": 230.5, "wall_p95": 243.5, "wall_max": 243.5, "per": 236.8, "note": "full-memory-copy mode"},
    "pause-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 272.3, "wall_min": 262.5, "wall_p95": 283.5, "wall_max": 283.5, "per": 54.5},
    "pause-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 280.2, "wall_min": 270.8, "wall_p95": 287.8, "wall_max": 287.8, "per": 28.0},
    "resume-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 56.7, "wall_min": 41.0, "wall_p95": 84.8, "wall_max": 84.8, "per": 56.7},
    "resume-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 27.8, "wall_min": 17.0, "wall_p95": 48.6, "wall_max": 48.6, "per": 5.6},
    "resume-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 45.0, "wall_min": 36.1, "wall_p95": 57.2, "wall_max": 57.2, "per": 4.5},
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
    "cube_version": "v0.5.1",
    "test_date": "2026-07-15",
}

BASELINE_VERA_PERF: dict[str, dict[str, Any]] = {
    "template-create-c1": {"concurrency": 1, "n": 20, "avg": 39.4, "min": 33.7, "p95": 42.8, "max": 43.2, "per": 45.7, "throughput": "21.9 /s"},
    "template-create-c10": {"concurrency": 10, "n": 200, "avg": 62.5, "min": 40.7, "p95": 73.8, "max": 78.4, "per": 7.0, "throughput": "142.2 /s"},
    "template-create-c20": {"concurrency": 20, "n": 300, "avg": 73.5, "min": 46.2, "p95": 86.0, "max": 89.5, "per": 4.2, "throughput": "240.7 /s"},
    "template-create-c50": {"concurrency": 50, "n": 500, "avg": 96.8, "min": 55.6, "p95": 136.7, "max": 156.6, "per": 2.3, "throughput": "440.5 /s"},
    "deployment-density": {
        "overhead_per_sandbox_mb": 89.0,
        "samples": [
            {"count": 100, "delta_gb": 5, "overhead_mb": 51},
            {"count": 300, "delta_gb": 22, "overhead_mb": 75},
            {"count": 500, "delta_gb": 40, "overhead_mb": 82},
            {"count": 1000, "delta_gb": 87, "overhead_mb": 89},
        ],
        "note": "64 KB page size kernel; tmpfs tuned to 5 GB for 1000-sandbox test",
    },
    "snapshot-create-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 101.3, "wall_min": 97.5, "wall_p95": 108.7, "wall_max": 108.7, "per": 101.3},
    "snapshot-create-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 154.2, "wall_min": 145.7, "wall_p95": 174.6, "wall_max": 174.6, "per": 30.8},
    "snapshot-create-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 190.3, "wall_min": 186.3, "wall_p95": 193.6, "wall_max": 193.6, "per": 19.0},
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
    "snapshot-create-from-c1": {"concurrency": 1, "n": 1, "rounds": 3, "wall_avg": 44.3, "wall_min": 39.0, "wall_p95": 53.1, "wall_max": 53.1, "per": 44.3},
    "snapshot-create-from-c10": {"concurrency": 10, "n": 10, "rounds": 3, "wall_avg": 73.0, "wall_min": 66.5, "wall_p95": 80.8, "wall_max": 80.8, "per": 7.3},
    "snapshot-create-from-c20": {"concurrency": 20, "n": 20, "rounds": 3, "wall_avg": 92.5, "wall_min": 88.2, "wall_p95": 99.1, "wall_max": 99.1, "per": 4.6},
    "rollback-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 71.4, "wall_min": 60.5, "wall_p95": 84.4, "wall_max": 84.4, "per": 71.4},
    "rollback-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 116.6, "wall_min": 110.2, "wall_p95": 124.7, "wall_max": 124.7, "per": 23.3},
    "rollback-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 187.4, "wall_min": 181.5, "wall_p95": 195.6, "wall_max": 195.6, "per": 18.7},
    "clone-c1-n1": {"concurrency": 1, "n": 1, "rounds": 5, "wall_avg": 142.6, "wall_min": 138.0, "wall_p95": 147.0, "wall_max": 147.0, "per": 142.6},
    "clone-c5-n5": {"concurrency": 5, "n": 5, "rounds": 3, "wall_avg": 185, "per": 37.0, "note": "summary only, no min/p95/max"},
    "clone-c10-n10": {"concurrency": 10, "n": 10, "rounds": 3, "wall_avg": 181, "per": 18.1, "note": "summary only"},
    "clone-c20-n20": {"concurrency": 20, "n": 20, "rounds": 3, "wall_avg": 192, "per": 9.6, "note": "summary only"},
    "clone-c50-n50": {"concurrency": 50, "n": 50, "rounds": 3, "wall_avg": 243, "per": 4.9, "note": "summary only"},
    "pause-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 347.6, "wall_min": 302.8, "wall_p95": 423.0, "wall_max": 423.0, "per": 347.6, "note": "full-memory-copy mode"},
    "pause-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 458.1, "wall_min": 438.3, "wall_p95": 477.8, "wall_max": 477.8, "per": 91.6},
    "pause-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 497.2, "wall_min": 393.3, "wall_p95": 551.6, "wall_max": 551.6, "per": 49.7},
    "resume-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 16.5, "wall_min": 9.1, "wall_p95": 19.1, "wall_max": 19.1, "per": 16.5},
    "resume-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 16.3, "wall_min": 10.6, "wall_p95": 20.5, "wall_max": 20.5, "per": 3.3},
    "resume-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 20.7, "wall_min": 13.2, "wall_p95": 24.8, "wall_max": 24.8, "per": 2.1},
}

# ===========================================================================
# Kunpeng 920 (ARM64) Baseline
# ===========================================================================

BASELINE_KUNPENG_ENV: dict[str, Any] = {
    "label": "Kunpeng 920 (ARM64)",
    "machine_type": "Huawei Kunpeng 920 (ARM64 / AArch64)",
    "os": "TencentOS Server 4",
    "kernel": "6.6.119-50.12.tl4",
    "arch": "aarch64",
    "cpu_model": "Huawei Kunpeng 920",
    "cpu_config": "—",
    "memory": "—",
    "disk": "—",
    "sandbox_spec": "2 vCPU / 2 GiB",
    "cube_version": "v0.5.x",
    "test_date": "2026-07",
    "note": "ARM64 server-grade SoC; snapshot and clone ops significantly slower than Vera/x86",
}

BASELINE_KUNPENG_PERF: dict[str, dict[str, Any]] = {
    "template-create-c1": {"concurrency": 1, "n": 5, "avg": 137.2, "min": 85.3, "p95": 184.6, "max": 184.6, "per": 137.2},
    "template-create-c5": {"concurrency": 5, "n": 5, "avg": 177.0, "min": 147.9, "p95": 203.9, "max": 203.9, "per": 35.4},
    "template-create-c10": {"concurrency": 10, "n": 5, "avg": 270.5, "min": 251.9, "p95": 286.8, "max": 286.8, "per": 27.1},
    # no deployment-density data for Kunpeng in the report
    "snapshot-create-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 102.7, "wall_min": 92.8, "wall_p95": 111.9, "wall_max": 111.9, "per": 102.7},
    "snapshot-create-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 181.2, "wall_min": 150.3, "wall_p95": 223.9, "wall_max": 223.9, "per": 36.2},
    "snapshot-create-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 249.9, "wall_min": 216.5, "wall_p95": 301.8, "wall_max": 301.8, "per": 25.0},
    "snapshot-dirty": {
        "samples": [
            {"write_mb": 0, "dirty_mb": 9.7, "snapshot_ms": 120.6, "create_from_ms": 1204.2},
            {"write_mb": 10, "dirty_mb": 40.6, "snapshot_ms": 125.6, "create_from_ms": 1433.7},
            {"write_mb": 50, "dirty_mb": 122.2, "snapshot_ms": 164.1, "create_from_ms": 1291.9},
            {"write_mb": 100, "dirty_mb": 194.8, "snapshot_ms": 183.9, "create_from_ms": 1127.9},
            {"write_mb": 200, "dirty_mb": 296.6, "snapshot_ms": 202.8, "create_from_ms": 1381.0},
            {"write_mb": 500, "dirty_mb": 602.0, "snapshot_ms": 283.9, "create_from_ms": 1194.2},
            {"write_mb": 800, "dirty_mb": 907.4, "snapshot_ms": 325.6, "create_from_ms": 1070.2},
            {"write_mb": 1024, "dirty_mb": 1136.5, "snapshot_ms": 387.7, "create_from_ms": 1188.0},
        ],
        "note": "create_from_ms ~1000-1500 ms — Kunpeng restore from snapshot is 10-20x slower than Vera",
    },
    "snapshot-create-from-c1": {"concurrency": 1, "n": 1, "rounds": 3, "wall_avg": 300.4, "wall_min": 292.2, "wall_p95": 311.6, "wall_max": 311.6, "per": 300.4},
    "snapshot-create-from-c10": {"concurrency": 10, "n": 10, "rounds": 3, "wall_avg": 471.4, "wall_min": 436.9, "wall_p95": 510.3, "wall_max": 510.3, "per": 47.1},
    "snapshot-create-from-c20": {"concurrency": 20, "n": 20, "rounds": 3, "wall_avg": 467.3, "wall_min": 455.4, "wall_p95": 481.0, "wall_max": 481.0, "per": 23.4},
    "snapshot-create-from-c50": {"concurrency": 50, "n": 50, "rounds": 3, "wall_avg": 847.8, "wall_min": 658.0, "wall_p95": 1166.7, "wall_max": 1166.7, "per": 17.0},
    "rollback-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 355.1, "wall_min": 342.7, "wall_p95": 363.8, "wall_max": 363.8, "per": 355.1},
    "rollback-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 4715.2, "wall_min": 4456.5, "wall_p95": 4994.5, "wall_max": 4994.5, "per": 471.5},
    # no rollback-c5 data for Kunpeng
    "clone-c1-n1": {"concurrency": 1, "n": 1, "rounds": 5, "wall_avg": 840.8, "wall_min": 784.3, "wall_p95": 875.5, "wall_max": 875.5, "per": 840.8},
    "clone-c10-n100": {"concurrency": 10, "n": 100, "rounds": 2, "wall_avg": 5583.1, "wall_min": 5295.2, "wall_p95": 5870.9, "wall_max": 5870.9, "per": 55.8},
    "clone-c20-n100": {"concurrency": 20, "n": 100, "rounds": 2, "wall_avg": 3815.4, "wall_min": 3518.9, "wall_p95": 4111.9, "wall_max": 4111.9, "per": 38.2},
    "clone-c50-n100": {"concurrency": 50, "n": 100, "rounds": 2, "wall_avg": 2596.1, "wall_min": 2404.6, "wall_p95": 2787.5, "wall_max": 2787.5, "per": 26.0},
    "pause-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 366.3, "wall_min": 356.4, "wall_p95": 389.0, "wall_max": 389.0, "per": 366.3, "note": "full-memory-copy mode"},
    "pause-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 455.1, "wall_min": 440.0, "wall_p95": 477.5, "wall_max": 477.5, "per": 91.0},
    "pause-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 513.9, "wall_min": 487.9, "wall_p95": 543.1, "wall_max": 543.1, "per": 51.4},
    "resume-c1": {"concurrency": 1, "rounds": 5, "wall_avg": 19.3, "wall_min": 18.4, "wall_p95": 20.4, "wall_max": 20.4, "per": 19.3},
    "resume-c5": {"concurrency": 5, "rounds": 5, "wall_avg": 41.0, "wall_min": 26.8, "wall_p95": 55.7, "wall_max": 55.7, "per": 8.2},
    "resume-c10": {"concurrency": 10, "rounds": 5, "wall_avg": 42.4, "wall_min": 35.9, "wall_p95": 62.0, "wall_max": 62.0, "per": 4.2},
}

# ===========================================================================
# Aggregated exports (backward-compatible)
# ===========================================================================

BASELINE_ENVIRONMENT = BASELINE_BMI5_ENV
BASELINE_PERF = BASELINE_BMI5_PERF

ALL_BASELINES: dict[str, dict[str, Any]] = {
    "BMI5 (x86_64)": {
        "env": BASELINE_BMI5_ENV,
        "perf": BASELINE_BMI5_PERF,
        "source": "https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html",
        "date": "2026-06-01",
    },
    "BMSA9 (x86_64)": {
        "env": BASELINE_BMSA9_ENV,
        "perf": BASELINE_BMSA9_PERF,
        "source": "https://iwiki.woa.com/p/4026284308",
        "date": "2026-07",
    },
    "Vera A1P (ARM64)": {
        "env": BASELINE_VERA_ENV,
        "perf": BASELINE_VERA_PERF,
        "source": "2026-07-15-cubesandbox-perf-benchmark-vera.md",
        "date": "2026-07-15",
    },
    "Kunpeng 920 (ARM64)": {
        "env": BASELINE_KUNPENG_ENV,
        "perf": BASELINE_KUNPENG_PERF,
        "source": "https://iwiki.woa.com/p/4026284308",
        "date": "2026-07",
    },
}

BASELINE_SOURCE = "https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html"
BASELINE_SOURCE_DATE = "2026-06-01"
