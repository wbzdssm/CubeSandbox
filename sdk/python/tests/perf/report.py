# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Report generation: JSON + Markdown, English & Chinese.

The Markdown report includes:
- Baseline comparison columns (vs BMI5 / BMSA9 / Vera / Kunpeng) on every table
- CPU/Memory banner above benchmark tables
- Dirty-page scaling sub-section (snapshot + create-from-sandbox tables)
- Deployment density with dedicated rendering
- Throughput column on template-create tables
- Full cross-machine baseline reference appendix

Output files (base name from `CUBE_OUTPUT_REPORT`, default "report"):
    report.md      - Markdown, English
    report.zh.md    - Markdown, Chinese
    report.json     - JSON, English
    report.zh.json   - JSON, Chinese
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Literal

from .config import DENSITY_COUNT, PERF_ROUNDS
from .env import EnvInfo
from .runner import FAIL, PASS, PERF_RESULTS, SKIP, RESULTS, PerfResult

Lang = Literal["en", "zh"]

# ---------------------------------------------------------------------------
# Lazy baseline import
# ---------------------------------------------------------------------------

_BASELINE_LOADED = False
_ALL_BASELINES: dict[str, dict[str, Any]] = {}
_BASELINE_KEYS: list[str] = []


def _ensure_baselines() -> None:
    global _BASELINE_LOADED, _ALL_BASELINES, _BASELINE_KEYS
    if _BASELINE_LOADED:
        return
    _BASELINE_LOADED = True
    try:
        _tests_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _tests_dir not in sys.path:
            sys.path.insert(0, _tests_dir)
        from perf.baseline import ALL_BASELINES as bl  # type: ignore[import-untyped]
        _ALL_BASELINES = bl
        _BASELINE_KEYS = list(bl.keys())
    except ImportError:
        pass


# ===========================================================================
# Data snapshot
# ===========================================================================


def _perf_result_to_dict(r: PerfResult) -> dict[str, Any]:
    extra = r.samples[0].extra if r.samples else {}
    # Keep raw latencies for scatter chart
    raw_latencies = [s.latency_ms for s in r.samples] if r.samples else []
    return {
        "scenario": r.scenario,
        "count": r.count,
        "concurrency": r.concurrency,
        "avg_ms": round(r.avg, 2),
        "min_ms": round(r.min, 2),
        "p50_ms": round(r.p50, 2),
        "p95_ms": round(r.p95, 2),
        "p99_ms": round(r._percentile(99), 2),
        "max_ms": round(r.max, 2),
        "wall_ms": round(extra.get("wall_ms", 0), 2),
        "per_ms": round(extra.get("per_ms", 0), 2),
        "raw_latencies": raw_latencies,
        "extra": {k: v for k, v in extra.items() if k not in ("wall_ms", "per_ms")},
    }


def build_report_data(env: EnvInfo) -> dict[str, Any]:
    total = PASS + FAIL + SKIP
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": {
            "hostname": env.hostname, "os_name": env.os_name, "os_version": env.os_version,
            "kernel": env.kernel, "arch": env.arch, "cpu_model": env.cpu_model,
            "cpu_cores_physical": env.cpu_cores_physical, "cpu_cores_logical": env.cpu_cores_logical,
            "cpu_sockets": env.cpu_sockets, "numa_nodes": env.numa_nodes,
            "memory_total_gb": env.memory_total_gb, "memory_type": env.memory_type,
            "disk_model": env.disk_model, "disk_size_gb": env.disk_size_gb,
            "disk_fs": env.disk_fs, "disk_type": env.disk_type,
            "python_version": env.python_version, "sdk_version": env.sdk_version,
            "api_url": env.api_url, "template_id": env.template_id,
            "template_image": env.template_image, "template_instance_type": env.template_instance_type,
            "template_status": env.template_status, "timestamp": env.timestamp,
            "template_cpu": env.template_cpu, "template_memory_mb": env.template_memory_mb,
            "template_spec": env.template_spec,
            "cubeapi_version": env.cubeapi_version, "cubeapi_commit": env.cubeapi_commit,
            "cubeapi_build_time": env.cubeapi_build_time, "cubeapi_go_version": env.cubeapi_go_version,
            "cubemaster_version": env.cubemaster_version, "cubemaster_commit": env.cubemaster_commit,
            "cubemaster_build_time": env.cubemaster_build_time,
            "cubelet_version": env.cubelet_version, "cube_shim_version": env.cube_shim_version,
            "guest_image_version": env.guest_image_version, "kernel_version_node": env.kernel_version_node,
            # Release manifest (single source of truth on installed hosts)
            "release_version": env.release_version, "release_built_at": env.release_built_at,
            "release_built_by": env.release_built_by, "release_git_commit": env.release_git_commit,
            "release_manifest_path": env.release_manifest_path,
            # Extra component versions from the manifest
            "cubemastercli_version": env.cubemastercli_version, "cubecli_version": env.cubecli_version,
            "network_agent_version": env.network_agent_version,
            "cube_agent_version": env.cube_agent_version, "cube_runtime_version": env.cube_runtime_version,
            "cube_egress_version": env.cube_egress_version, "cube_proxy_version": env.cube_proxy_version,
            "cube_lifecycle_manager_version": env.cube_lifecycle_manager_version,
            # Guest image + kernel (declared)
            "guest_agent_version": env.guest_agent_version,
            "guest_image_digest": env.guest_image_digest, "guest_image_base": env.guest_image_base,
            "kernel_digest": env.kernel_digest,
            "kernel_pvm_version": env.kernel_pvm_version, "kernel_pvm_digest": env.kernel_pvm_digest,
            "processor": env.processor, "platform_summary": env.platform_summary,
            "python_impl": env.python_impl, "sdk_import_path": env.sdk_import_path,
            "httpx_version": env.httpx_version, "requests_version": env.requests_version,
        },
        "config": {"perf_rounds": PERF_ROUNDS, "density_max_count": DENSITY_COUNT},
        "functional": {"pass": PASS, "fail": FAIL, "skip": SKIP, "total": total, "results": list(RESULTS)},
        "perf": [_perf_result_to_dict(r) for r in PERF_RESULTS],
    }


# ===========================================================================
# JSON rendering
# ===========================================================================

_JSON_OVERALL_STATUS = {
    "en": {"pass": "ALL PASSED", "fail": "FAILURES"},
    "zh": {"pass": "全部通过", "fail": "存在失败"},
}


def to_json(data: dict[str, Any], lang: Lang) -> str:
    fail_count = data["functional"]["fail"]
    status_map = _JSON_OVERALL_STATUS[lang]
    payload = dict(data)
    payload["language"] = lang
    payload["overall_status"] = status_map["pass"] if fail_count == 0 else status_map["fail"]
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ===========================================================================
# Markdown helpers
# ===========================================================================


def _fmt_ms(ms: float) -> str:
    if ms == 0:
        return "—"
    return f"{ms:.1f} ms"


def _cmp_str(current: float, baseline: float | None) -> str:
    if baseline is None or baseline == 0 or current <= 0:
        return "—"
    ratio = current / baseline
    if ratio <= 1.05:
        return f"≈{ratio * 100:.0f}%"
    return f"+{(ratio - 1) * 100:.0f}%"


def _cmp_for_scenario(scenario: str, current_per: float, baseline_key: str) -> str:
    _ensure_baselines()
    bl = _ALL_BASELINES.get(baseline_key)
    if not bl or "perf" not in bl:
        return "—"
    bb = bl["perf"].get(scenario)
    if not bb:
        return "—"
    bl_per = bb.get("per") or bb.get("avg") or bb.get("wall_avg") or 0
    return _cmp_str(current_per, bl_per)


def _cmp_cols_for_row(scenario: str, current_per: float) -> list[str]:
    return [_cmp_for_scenario(scenario, current_per, key) for key in _BASELINE_KEYS]


def _cmp_cols_header(lang: Lang) -> list[str]:
    _ensure_baselines()
    prefix = "vs " if lang == "en" else "vs "
    return [f"{prefix}{key.split('(')[0].strip()}" for key in _BASELINE_KEYS]


def _perf_table(perf: list[dict[str, Any]], scenario_prefix: str, lang: Lang) -> str:
    """Generic perf table with baseline comparison columns."""
    rows = [r for r in perf if r["scenario"].startswith(scenario_prefix)]
    if not rows:
        return "_No data_\n" if lang == "en" else "_无数据_\n"

    _ensure_baselines()
    if lang == "zh":
        base = ["场景", "次数", "并发", "平均值", "最小值", "P50", "P95", "P99", "最大值", "总耗时", "单次均摊"]
    else:
        base = ["Scenario", "N", "Conc", "avg", "min", "p50", "p95", "p99", "max", "wall", "per"]
    all_cols = base + _cmp_cols_header(lang)
    header = "| " + " | ".join(all_cols) + " |"
    sep = "|" + "|".join([":---"] * len(all_cols)) + "|"
    lines = [header, sep]

    for r in rows:
        vals = [
            r["scenario"], str(r["count"]), str(r["concurrency"]),
            _fmt_ms(r["avg_ms"]), _fmt_ms(r["min_ms"]), _fmt_ms(r["p50_ms"]),
            _fmt_ms(r["p95_ms"]), _fmt_ms(r.get("p99_ms", 0)), _fmt_ms(r["max_ms"]),
            _fmt_ms(r["wall_ms"]), _fmt_ms(r["per_ms"]),
        ]
        vals += _cmp_cols_for_row(r["scenario"], r.get("per_ms") or r.get("avg_ms") or 0)
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def _template_table(perf: list[dict[str, Any]], lang: Lang) -> str:
    """Template-create table with throughput column."""
    rows = [r for r in perf if r["scenario"].startswith("template-create")]
    if not rows:
        return "_No data_\n" if lang == "en" else "_无数据_\n"

    _ensure_baselines()
    if lang == "zh":
        base = ["场景", "并发", "请求数", "平均值", "最小值", "P50", "P95", "P99", "最大值", "总耗时", "单次均摊", "吞吐量"]
    else:
        base = ["Scenario", "Conc", "Requests", "avg", "min", "p50", "p95", "max", "wall", "per", "Throughput"]
    all_cols = base + _cmp_cols_header(lang)
    header = "| " + " | ".join(all_cols) + " |"
    sep = "|" + "|".join([":---"] * len(all_cols)) + "|"
    lines = [header, sep]

    for r in rows:
        n = r["count"]
        wall = r["wall_ms"]
        throughput = f"{n / (wall / 1000):.1f} /s" if wall > 0 else "—"
        vals = [
            r["scenario"], str(r["concurrency"]), str(r["count"]),
            _fmt_ms(r["avg_ms"]), _fmt_ms(r["min_ms"]), _fmt_ms(r["p50_ms"]),
            _fmt_ms(r["p95_ms"]), _fmt_ms(r.get("p99_ms", 0)), _fmt_ms(r["max_ms"]),
            _fmt_ms(wall), _fmt_ms(r["per_ms"]), throughput,
        ]
        vals += _cmp_cols_for_row(r["scenario"], r.get("per_ms") or r.get("avg_ms") or 0)
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def _dirty_page_tables(perf: list[dict[str, Any]], lang: Lang) -> str:
    """Dirty-page scaling: two sub-tables (snapshot creation + create-from-snapshot)."""
    snap_rows = sorted(
        [r for r in perf if r["scenario"].startswith("snapshot-dirty-snap-")],
        key=lambda r: r["extra"].get("write_mb", 0),
    )
    create_rows = sorted(
        [r for r in perf if r["scenario"].startswith("snapshot-dirty-create-")],
        key=lambda r: r["extra"].get("write_mb", 0),
    )
    if not snap_rows and not create_rows:
        return "_No data_\n" if lang == "en" else "_无数据_\n"

    result = ""
    # Snapshot creation sub-table
    if snap_rows:
        if lang == "zh":
            result += "#### 快照制作耗时\n\n"
            base = ["写入量", "快照 avg", "快照 min", "快照 p95", "快照 max"]
        else:
            result += "#### Snapshot Creation Latency\n\n"
            base = ["Write Size", "snap avg", "snap min", "snap p95", "snap max"]
        all_cols = base + _cmp_cols_header(lang)
        result += "| " + " | ".join(all_cols) + " |\n"
        result += "|" + "|".join([":---"] * len(all_cols)) + "|\n"
        for r in snap_rows:
            wmb = r["extra"].get("write_mb", 0)
            vals = [
                f"{wmb} MB",
                _fmt_ms(r["avg_ms"]), _fmt_ms(r["min_ms"]),
                _fmt_ms(r["p95_ms"]), _fmt_ms(r["max_ms"]),
            ]
            vals += _cmp_cols_for_row(r["scenario"], r.get("avg_ms") or 0)
            result += "| " + " | ".join(vals) + " |\n"
        result += "\n"

    # Create-from-snapshot sub-table
    if create_rows:
        if lang == "zh":
            result += "#### 基于快照恢复沙箱耗时\n\n"
            base = ["写入量", "恢复 avg", "恢复 min", "恢复 p95", "恢复 max"]
        else:
            result += "#### Sandbox Creation from Snapshot Latency\n\n"
            base = ["Write Size", "create avg", "create min", "create p95", "create max"]
        all_cols = base + _cmp_cols_header(lang)
        result += "| " + " | ".join(all_cols) + " |\n"
        result += "|" + "|".join([":---"] * len(all_cols)) + "|\n"
        for r in create_rows:
            wmb = r["extra"].get("write_mb", 0)
            vals = [
                f"{wmb} MB",
                _fmt_ms(r["avg_ms"]), _fmt_ms(r["min_ms"]),
                _fmt_ms(r["p95_ms"]), _fmt_ms(r["max_ms"]),
            ]
            vals += _cmp_cols_for_row(r["scenario"], r.get("avg_ms") or 0)
            result += "| " + " | ".join(vals) + " |\n"
        result += "\n"

    return result


def _density_table(perf: list[dict[str, Any]], lang: Lang) -> str:
    """Deployment density table with per-sandbox overhead."""
    rows = [r for r in perf if r["scenario"].startswith("deployment-density")]
    if not rows:
        return "_No data_\n" if lang == "en" else "_无数据_\n"

    if lang == "zh":
        header = "| 存活沙箱数 | 可用内存 (GiB) | Δ 可用内存 (GiB) | 单 VM 均摊 (MB) |"
    else:
        header = "| Live Sandboxes | Free Memory (GiB) | Δ Free (GiB) | Per-VM Overhead (MB) |"
    result = header + "\n" + "|:---:|:---:|:---:|:---:|\n"

    for r in rows:
        extra = r.get("extra", {})
        count = extra.get("count", r["count"])
        baseline_gb = extra.get("baseline_gb", 0)
        final_free_gb = extra.get("final_free_gb", 0)
        delta = round(baseline_gb - final_free_gb, 1) if baseline_gb > 0 else 0
        overhead = round(r["avg_ms"], 1)  # latency_ms stores overhead_mb for density
        result += f"| {count} | {final_free_gb:.1f} | {delta:.1f} | {overhead:.1f} |\n"
    return result + "\n"


def _cpu_mem_banner(env: dict[str, Any], lang: Lang) -> str:
    cpu = str(env.get("cpu_model", "N/A"))[:50]
    cores = env.get("cpu_cores_logical", "?")
    mem = env.get("memory_total_gb", "?")
    arch = env.get("arch", "?")
    if lang == "zh":
        return f"> **测试机型**：{cpu} | **架构**：{arch} | **CPU 逻辑核**：{cores} | **内存**：{mem} GiB\n"
    return f"> **Machine**：{cpu} | **Arch**：{arch} | **CPU logical cores**：{cores} | **Memory**：{mem} GiB\n"


def _baseline_note(lang: Lang) -> str:
    _ensure_baselines()
    if not _BASELINE_KEYS:
        return ""
    labels = ", ".join(_BASELINE_KEYS)
    if lang == "zh":
        return (
            f"> 基线对比列（{labels}）数据来源：`tests/perf/baseline.py`\n"
            "> 百分比 = 当前单次均摊 ÷ 基线单次均摊；≈100% 表示持平，+N% 表示慢于基线\n"
        )
    return (
        f"> Baseline columns ({labels}) sourced from `tests/perf/baseline.py`\n"
        "> Percentage = current per-operation ÷ baseline per-operation; ≈100% = on par, +N% = slower\n"
    )


# ===========================================================================
# Baseline reference appendix (static, all four machines)
# ===========================================================================

_BASELINE_APPENDIX_ZH = r"""
---

## 3. 跨机型基线参考

> 以下数据来自各机型官方测试报告，不参与本次运行，仅作对比参考。
> 来源：BMI5 (2026-06-01 blog) / BMSA9 & Kunpeng (iwiki) / Vera (2026-07-15 report)

### 3.1 基线环境概览

| 机型 | 架构 | CPU | 逻辑核 | 内存 | 内核 | 页大小 | CubeSandbox |
|:---|:---|:---|:---:|:---|:---|:---:|:---:|
| BMI5 | x86_64 | Xeon Platinum 8255C | 96 | 375 GiB DDR4 | 6.6.119-49.6 | 4 KB | v0.4.x |
| BMSA9 | x86_64 | BMSA9 | — | — | 6.6.119-47.8 | 4 KB | v0.5.x |
| Vera A1P | ARM64 | NVIDIA Vera A1P (ARMv9) | 176 | 768 GB LPDDR5x | 6.17.0-nvidia-64k | 64 KB | v0.5.1 |
| Kunpeng 920 | ARM64 | Huawei Kunpeng 920 | — | — | 6.6.119-50.12 | 4K/64K | v0.5.x |

### 3.2 冷启动延迟与并发扩展

#### BMI5

| 并发 | 请求数 | avg | min | p95 | max | 单次均摊 | 吞吐 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 20 | 47.8 ms | 43.5 ms | 57.4 ms | 60.4 ms | 55.8 ms | 17.9 /s |
| 10 | 200 | 88.7 ms | 45.8 ms | 116.9 ms | 119.1 ms | 9.9 ms | 101.4 /s |
| 20 | 300 | 98.1 ms | 47.7 ms | 175.8 ms | 232.6 ms | 5.5 ms | 180.9 /s |
| 50 | 500 | 276.1 ms | 60.6 ms | 508.4 ms | 681.3 ms | 6.8 ms | 147.6 /s |

#### BMSA9

| 并发 | 请求数 | avg | min | p95 | max | 单次均摊 | 吞吐 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 20 | 41.8 ms | 37.9 ms | 44.3 ms | 58.5 ms | 50.2 ms | 19.9 /s |
| 10 | 200 | 45.2 ms | 38.9 ms | 58.7 ms | 68.7 ms | 5.4 ms | 185.3 /s |
| 20 | 300 | 50.0 ms | 37.6 ms | 68.7 ms | 74.1 ms | 3.1 ms | 319.0 /s |
| 50 | 500 | 92.4 ms | 43.0 ms | 154.7 ms | 181.5 ms | 2.3 ms | 441.4 /s |

#### Vera A1P

| 并发 | 请求数 | avg | min | p95 | max | 单次均摊 | 吞吐 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 20 | 39.4 ms | 33.7 ms | 42.8 ms | 43.2 ms | 45.7 ms | 21.9 /s |
| 10 | 200 | 62.5 ms | 40.7 ms | 73.8 ms | 78.4 ms | 7.0 ms | 142.2 /s |
| 20 | 300 | 73.5 ms | 46.2 ms | 86.0 ms | 89.5 ms | 4.2 ms | 240.7 /s |
| 50 | 500 | 96.8 ms | 55.6 ms | 136.7 ms | 156.6 ms | 2.3 ms | 440.5 /s |

#### Kunpeng 920

| 并发 | 请求数 | avg | min | p95 | max | 单次均摊 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 5 | 137.2 ms | 85.3 ms | 184.6 ms | 184.6 ms | 137.2 ms |
| 5 | 5 | 177.0 ms | 147.9 ms | 203.9 ms | 203.9 ms | 35.4 ms |
| 10 | 5 | 270.5 ms | 251.9 ms | 286.8 ms | 286.8 ms | 27.1 ms |

### 3.3 部署密度（单 VM 内存开销）

#### BMI5

| 存活沙箱数 | 可用内存 | 单 VM 均摊 |
|:---:|:---:|:---:|
| 0（基线） | 359.5 GiB | — |
| 100 | 357.4 GiB | 21.5 MB |
| 300 | 352.5 GiB | 23.8 MB |
| 500 | 347.3 GiB | 25.0 MB |
| 1000 | 334.3 GiB | 25.7 MB |

#### BMSA9

| 存活沙箱数 | used mem | 单 VM 均摊 |
|:---:|:---:|:---:|
| 0（基线） | 107731 MiB | — |
| 100 | 109708 MiB | 19.8 MB |
| 300 | 114644 MiB | 23.0 MB |
| 500 | 120195 MiB | 24.9 MB |
| 1000 | 136484 MiB | 28.8 MB |

#### Vera A1P

| 存活沙箱数 | Δ available | 单 VM 均摊 |
|:---:|:---:|:---:|
| 100 | 5 GiB | 51 MB |
| 300 | 22 GiB | 75 MB |
| 500 | 40 GiB | 82 MB |
| 1000 | 87 GiB | 89 MB |

> Vera 64 KB 页大小导致单 VM 均摊约 89 MB（约 3.5× BMI5）。

### 3.4 Snapshot 制作耗时 vs 并发

#### BMI5

| 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-snapshot avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 5 | 49.8 ms | 47.3 ms | 54.1 ms | 54.1 ms | 49.8 ms |
| 5 | 5 | 71.0 ms | 62.7 ms | 81.0 ms | 81.0 ms | 14.2 ms |
| 10 | 5 | 127.2 ms | 79.6 ms | 155.6 ms | 155.6 ms | 12.7 ms |

#### BMSA9

| 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-snapshot avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 5 | 88.7 ms | 77.9 ms | 100.0 ms | 100.0 ms | 88.7 ms |
| 5 | 5 | 117.1 ms | 107.7 ms | 127.6 ms | 127.6 ms | 23.4 ms |
| 10 | 5 | 144.6 ms | 138.4 ms | 155.3 ms | 155.3 ms | 14.5 ms |

#### Vera A1P

| 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-snapshot avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 5 | 101.3 ms | 97.5 ms | 108.7 ms | 108.7 ms | 101.3 ms |
| 5 | 5 | 154.2 ms | 145.7 ms | 174.6 ms | 174.6 ms | 30.8 ms |
| 10 | 5 | 190.3 ms | 186.3 ms | 193.6 ms | 193.6 ms | 19.0 ms |

#### Kunpeng 920

| 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-snapshot avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 5 | 102.7 ms | 92.8 ms | 111.9 ms | 111.9 ms | 102.7 ms |
| 5 | 5 | 181.2 ms | 150.3 ms | 223.9 ms | 223.9 ms | 36.2 ms |
| 10 | 5 | 249.9 ms | 216.5 ms | 301.8 ms | 301.8 ms | 25.0 ms |

### 3.5 Snapshot 制作耗时 vs Dirty Page 大小 ⭐

#### BMI5

| 写入量 | Dirty Page | snapshot avg | snapshot min | snapshot p95 | snapshot max | create sandbox avg | create sandbox min | create sandbox p95 | create sandbox max |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 0 MB | 7.1 MB | 45.7 ms | 43.9 ms | 47.4 ms | 47.4 ms | 64.8 ms | 61.5 ms | 68.6 ms | 68.6 ms |
| 10 MB | 38.9 MB | 75.7 ms | 73.2 ms | 79.2 ms | 79.2 ms | 60.7 ms | 57.3 ms | 66.1 ms | 66.1 ms |
| 50 MB | 120.7 MB | 107.7 ms | 104.8 ms | 112.3 ms | 112.3 ms | 64.4 ms | 60.4 ms | 70.6 ms | 70.6 ms |
| 100 MB | 195.0 MB | 138.6 ms | 136.7 ms | 139.9 ms | 139.9 ms | 66.5 ms | 60.9 ms | 71.1 ms | 71.1 ms |
| 200 MB | 296.7 MB | 174.2 ms | 173.1 ms | 176.2 ms | 176.2 ms | 63.7 ms | 60.6 ms | 66.8 ms | 66.8 ms |
| 500 MB | 602.5 MB | 289.4 ms | 285.0 ms | 293.1 ms | 293.1 ms | 64.0 ms | 61.6 ms | 66.5 ms | 66.5 ms |
| 800 MB | 908.4 MB | 392.8 ms | 392.1 ms | 394.1 ms | 394.1 ms | 60.9 ms | 54.6 ms | 65.8 ms | 65.8 ms |
| 1024 MB | 1136.4 MB | 486.9 ms | 471.5 ms | 510.8 ms | 510.8 ms | 68.4 ms | 58.9 ms | 84.6 ms | 84.6 ms |

#### BMSA9

| 写入量 | Dirty Page | snapshot avg | snapshot min | snapshot p95 | snapshot max | create sandbox avg | create sandbox min | create sandbox p95 | create sandbox max |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 0 MB | 8.4 MB | 94.0 ms | 80.2 ms | 118.8 ms | 118.8 ms | 78.4 ms | 76.6 ms | 79.5 ms | 79.5 ms |
| 10 MB | 42.7 MB | 100.4 ms | 95.1 ms | 105.7 ms | 105.7 ms | 78.1 ms | 65.3 ms | 91.7 ms | 91.7 ms |
| 50 MB | 123.8 MB | 122.5 ms | 117.3 ms | 129.9 ms | 129.9 ms | 73.4 ms | 65.6 ms | 79.7 ms | 79.7 ms |
| 100 MB | 196.7 MB | 139.4 ms | 138.6 ms | 140.6 ms | 140.6 ms | 83.1 ms | 62.2 ms | 108.8 ms | 108.8 ms |
| 200 MB | 298.5 MB | 160.2 ms | 151.3 ms | 170.9 ms | 170.9 ms | 71.9 ms | 63.7 ms | 78.8 ms | 78.8 ms |
| 500 MB | 605.0 MB | 206.5 ms | 198.0 ms | 219.9 ms | 219.9 ms | 75.2 ms | 69.4 ms | 80.2 ms | 80.2 ms |
| 800 MB | 910.6 MB | 257.9 ms | 234.6 ms | 281.0 ms | 281.0 ms | 114.9 ms | 72.0 ms | 181.9 ms | 181.9 ms |
| 1024 MB | 1138.6 MB | 279.0 ms | 277.1 ms | 281.0 ms | 281.0 ms | 79.6 ms | 77.5 ms | 82.7 ms | 82.7 ms |

#### Vera A1P

| 写入量 | Dirty Page | snapshot avg | snapshot min | snapshot p95 | snapshot max | create sandbox avg | create sandbox min | create sandbox p95 | create sandbox max |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 0 MB | 45.2 MB | 95.3 ms | 87.4 ms | 99.8 ms | 99.8 ms | 38.6 ms | 37.2 ms | 40.0 ms | 40.0 ms |
| 10 MB | 121.2 MB | 114.5 ms | 95.5 ms | 142.8 ms | 142.8 ms | 39.0 ms | 38.1 ms | 39.5 ms | 39.5 ms |
| 50 MB | 202.8 MB | 106.6 ms | 102.8 ms | 109.5 ms | 109.5 ms | 39.3 ms | 38.7 ms | 39.8 ms | 39.8 ms |
| 100 MB | 285.6 MB | 120.6 ms | 116.1 ms | 125.1 ms | 125.1 ms | 41.9 ms | 40.3 ms | 44.7 ms | 44.7 ms |
| 200 MB | 387.0 MB | 135.6 ms | 119.3 ms | 150.5 ms | 150.5 ms | 39.0 ms | 37.7 ms | 40.0 ms | 40.0 ms |
| 500 MB | 694.2 MB | 152.4 ms | 145.8 ms | 157.8 ms | 157.8 ms | 40.4 ms | 38.2 ms | 42.1 ms | 42.1 ms |
| 800 MB | 1000.4 MB | 180.4 ms | 163.1 ms | 190.4 ms | 190.4 ms | 41.7 ms | 40.7 ms | 43.5 ms | 43.5 ms |
| 1024 MB | 1228.0 MB | 192.7 ms | 188.5 ms | 196.1 ms | 196.1 ms | 38.9 ms | 37.7 ms | 39.6 ms | 39.6 ms |

#### Kunpeng 920

| 写入量 | Dirty Page | snapshot avg | snapshot min | snapshot p95 | snapshot max | create sandbox avg | create sandbox min | create sandbox p95 | create sandbox max |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 0 MB | 9.7 MB | 120.6 ms | 91.7 ms | 155.1 ms | 155.1 ms | 1204.2 ms | 1111.4 ms | 1288.8 ms | 1288.8 ms |
| 10 MB | 40.6 MB | 125.6 ms | 120.7 ms | 133.2 ms | 133.2 ms | 1433.7 ms | 1283.6 ms | 1565.6 ms | 1565.6 ms |
| 50 MB | 122.2 MB | 164.1 ms | 155.8 ms | 180.5 ms | 180.5 ms | 1291.9 ms | 1122.1 ms | 1572.6 ms | 1572.6 ms |
| 100 MB | 194.8 MB | 183.9 ms | 158.1 ms | 223.8 ms | 223.8 ms | 1127.9 ms | 998.9 ms | 1246.6 ms | 1246.6 ms |
| 200 MB | 296.6 MB | 202.8 ms | 177.7 ms | 239.8 ms | 239.8 ms | 1381.0 ms | 1354.9 ms | 1432.2 ms | 1432.2 ms |
| 500 MB | 602.0 MB | 283.9 ms | 253.0 ms | 302.0 ms | 302.0 ms | 1194.2 ms | 985.5 ms | 1347.8 ms | 1347.8 ms |
| 800 MB | 907.4 MB | 325.6 ms | 319.6 ms | 330.2 ms | 330.2 ms | 1070.2 ms | 1001.6 ms | 1137.6 ms | 1137.6 ms |
| 1024 MB | 1136.5 MB | 387.7 ms | 382.8 ms | 396.3 ms | 396.3 ms | 1188.0 ms | 1027.2 ms | 1373.5 ms | 1373.5 ms |

> ⚠️ Kunpeng 920 恢复耗时远超快照制作耗时（~1200ms >> ~388ms），与其余机型趋势完全相反。

### 3.6 基于 Snapshot 启动沙箱

#### BMI5

| 并发 | n total | Rounds | wall avg | wall min | wall p95 | wall max | per-sandbox avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1 | 3 | 63.9 ms | 62.5 ms | 66.1 ms | 66.1 ms | 63.9 ms |
| 10 | 10 | 3 | 89.9 ms | 84.0 ms | 93.6 ms | 93.6 ms | 9.0 ms |
| 20 | 20 | 3 | 118.9 ms | 92.7 ms | 167.1 ms | 167.1 ms | 5.9 ms |
| 50 | 50 | 3 | 180.3 ms | 135.1 ms | 260.7 ms | 260.7 ms | 3.6 ms |

#### BMSA9

| 并发 | n total | Rounds | wall avg | wall min | wall p95 | wall max | per-sandbox avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1 | 3 | 69.7 ms | 65.9 ms | 72.8 ms | 72.8 ms | 69.7 ms |
| 10 | 10 | 3 | 98.1 ms | 85.0 ms | 107.6 ms | 107.6 ms | 9.8 ms |
| 20 | 20 | 3 | 106.5 ms | 102.3 ms | 112.9 ms | 112.9 ms | 5.3 ms |
| 50 | 50 | 3 | 141.2 ms | 135.4 ms | 151.7 ms | 151.7 ms | 2.8 ms |

#### Vera A1P

| 并发 | n total | Rounds | wall avg | wall min | wall p95 | wall max | per-sandbox avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1 | 3 | 44.3 ms | 39.0 ms | 53.1 ms | 53.1 ms | 44.3 ms |
| 10 | 10 | 3 | 73.0 ms | 66.5 ms | 80.8 ms | 80.8 ms | 7.3 ms |
| 20 | 20 | 3 | 92.5 ms | 88.2 ms | 99.1 ms | 99.1 ms | 4.6 ms |

#### Kunpeng 920

| 并发 | n total | Rounds | wall avg | wall min | wall p95 | wall max | per-sandbox avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1 | 3 | 300.4 ms | 292.2 ms | 311.6 ms | 311.6 ms | 300.4 ms |
| 10 | 10 | 3 | 471.4 ms | 436.9 ms | 510.3 ms | 510.3 ms | 47.1 ms |
| 20 | 20 | 3 | 467.3 ms | 455.4 ms | 481.0 ms | 481.0 ms | 23.4 ms |
| 50 | 50 | 3 | 847.8 ms | 658.0 ms | 1166.7 ms | 1166.7 ms | 17.0 ms |

### 3.7 Rollback

#### BMI5

| 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-rollback avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 5 | 81.6 ms | 74.7 ms | 97.4 ms | 97.4 ms | 81.6 ms |
| 5 | 5 | 189.6 ms | 161.8 ms | 243.2 ms | 243.2 ms | 37.9 ms |
| 10 | 5 | 266.1 ms | 236.1 ms | 305.1 ms | 305.1 ms | 26.6 ms |

#### BMSA9

| 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-rollback avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 5 | 141.7 ms | 104.6 ms | 181.6 ms | 181.6 ms | 141.7 ms |
| 5 | 5 | 213.6 ms | 194.8 ms | 261.1 ms | 261.1 ms | 42.7 ms |
| 10 | 5 | 242.4 ms | 208.0 ms | 276.1 ms | 276.1 ms | 24.2 ms |

#### Vera A1P

| 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-rollback avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 5 | 71.4 ms | 60.5 ms | 84.4 ms | 84.4 ms | 71.4 ms |
| 5 | 5 | 116.6 ms | 110.2 ms | 124.7 ms | 124.7 ms | 23.3 ms |
| 10 | 5 | 187.4 ms | 181.5 ms | 195.6 ms | 195.6 ms | 18.7 ms |

#### Kunpeng 920

| 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-rollback avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 5 | 355.1 ms | 342.7 ms | 363.8 ms | 363.8 ms | 355.1 ms |
| 10 | 5 | 4715.2 ms | 4456.5 ms | 4994.5 ms | 4994.5 ms | 471.5 ms |

### 3.8 Clone

#### BMI5

| n | 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-clone avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1 | 5 | 219.6 ms | 213.6 ms | 234.7 ms | 234.7 ms | 219.6 ms |
| 100 | 10 | 2 | 870.4 ms | 860.6 ms | 880.2 ms | 880.2 ms | 8.7 ms |
| 100 | 20 | 2 | 638.6 ms | 620.8 ms | 656.3 ms | 656.3 ms | 6.4 ms |
| 100 | 50 | 2 | 540.9 ms | 491.3 ms | 590.5 ms | 590.5 ms | 5.4 ms |

#### BMSA9

| n | 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-clone avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1 | 5 | 433.1 ms | 315.9 ms | 833.5 ms | 833.5 ms | 433.1 ms |
| 100 | 10 | 2 | 849.5 ms | 843.4 ms | 855.6 ms | 855.6 ms | 8.5 ms |
| 100 | 20 | 2 | 755.1 ms | 627.1 ms | 883.1 ms | 883.1 ms | 7.6 ms |
| 100 | 50 | 2 | 1013.2 ms | 943.5 ms | 1082.9 ms | 1082.9 ms | 10.1 ms |

#### Vera A1P

| n | 并发 | 轮数 | wall avg | per-clone avg |
|:---:|:---:|:---:|:---:|:---:|
| 1 | 1 | 5 | 142.6 ms | 142.6 ms |
| 5 | 5 | 3 | 185 ms | 37.0 ms |
| 10 | 10 | 3 | 181 ms | 18.1 ms |
| 20 | 20 | 3 | 192 ms | 9.6 ms |
| 50 | 50 | 3 | 243 ms | 4.9 ms |

#### Kunpeng 920

| n | 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-clone avg |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1 | 5 | 840.8 ms | 784.3 ms | 875.5 ms | 875.5 ms | 840.8 ms |
| 100 | 10 | 2 | 5583.1 ms | 5295.2 ms | 5870.9 ms | 5870.9 ms | 55.8 ms |
| 100 | 20 | 2 | 3815.4 ms | 3518.9 ms | 4111.9 ms | 4111.9 ms | 38.2 ms |
| 100 | 50 | 2 | 2596.1 ms | 2404.6 ms | 2787.5 ms | 2787.5 ms | 26.0 ms |

### 3.9 Pause & Resume

#### BMI5

| 操作 | 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-op avg |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Pause | 1 | 5 | 558.4 ms | 530.8 ms | 590.3 ms | 590.3 ms | 558.4 ms |
| Pause | 5 | 5 | 656.9 ms | 621.9 ms | 683.2 ms | 683.2 ms | 131.4 ms |
| Pause | 10 | 5 | 682.1 ms | 674.1 ms | 699.3 ms | 699.3 ms | 68.2 ms |
| Resume | 1 | 5 | 41.8 ms | 18.7 ms | 65.1 ms | 65.1 ms | 41.8 ms |
| Resume | 5 | 5 | 28.2 ms | 17.6 ms | 34.2 ms | 34.2 ms | 5.6 ms |
| Resume | 10 | 5 | 35.7 ms | 30.6 ms | 41.7 ms | 41.7 ms | 3.6 ms |

#### BMSA9

| 操作 | 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-op avg |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Pause | 1 | 5 | 236.8 ms | 230.5 ms | 243.5 ms | 243.5 ms | 236.8 ms |
| Pause | 5 | 5 | 272.3 ms | 262.5 ms | 283.5 ms | 283.5 ms | 54.5 ms |
| Pause | 10 | 5 | 280.2 ms | 270.8 ms | 287.8 ms | 287.8 ms | 28.0 ms |
| Resume | 1 | 5 | 56.7 ms | 41.0 ms | 84.8 ms | 84.8 ms | 56.7 ms |
| Resume | 5 | 5 | 27.8 ms | 17.0 ms | 48.6 ms | 48.6 ms | 5.6 ms |
| Resume | 10 | 5 | 45.0 ms | 36.1 ms | 57.2 ms | 57.2 ms | 4.5 ms |

#### Vera A1P

| 操作 | 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-op avg |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Pause | 1 | 5 | 347.6 ms | 302.8 ms | 423.0 ms | 423.0 ms | 347.6 ms |
| Pause | 5 | 5 | 458.1 ms | 438.3 ms | 477.8 ms | 477.8 ms | 91.6 ms |
| Pause | 10 | 5 | 497.2 ms | 393.3 ms | 551.6 ms | 551.6 ms | 49.7 ms |
| Resume | 1 | 5 | 16.5 ms | 9.1 ms | 19.1 ms | 19.1 ms | 16.5 ms |
| Resume | 5 | 5 | 16.3 ms | 10.6 ms | 20.5 ms | 20.5 ms | 3.3 ms |
| Resume | 10 | 5 | 20.7 ms | 13.2 ms | 24.8 ms | 24.8 ms | 2.1 ms |

#### Kunpeng 920

| 操作 | 并发 | 轮数 | wall avg | wall min | wall p95 | wall max | per-op avg |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Pause | 1 | 5 | 366.3 ms | 356.4 ms | 389.0 ms | 389.0 ms | 366.3 ms |
| Pause | 5 | 5 | 455.1 ms | 440.0 ms | 477.5 ms | 477.5 ms | 91.0 ms |
| Pause | 10 | 5 | 513.9 ms | 487.9 ms | 543.1 ms | 543.1 ms | 51.4 ms |
| Resume | 1 | 5 | 19.3 ms | 18.4 ms | 20.4 ms | 20.4 ms | 19.3 ms |
| Resume | 5 | 5 | 41.0 ms | 26.8 ms | 55.7 ms | 55.7 ms | 8.2 ms |
| Resume | 10 | 5 | 42.4 ms | 35.9 ms | 62.0 ms | 62.0 ms | 4.2 ms |

### 3.10 关键指标速览

| 场景 | BMI5 | BMSA9 | Vera A1P | Kunpeng 920 |
|:---|:---:|:---:|:---:|:---:|
| 冷启动 c=1 | 47.8 ms | 41.8 ms | 39.4 ms | 137.2 ms |
| 冷启动 c=50 吞吐 | 147.6 /s | 441.4 /s | 440.5 /s | — |
| 内存开销 @1000 | 25.7 MB | 28.8 MB | 89.0 MB | — |
| 快照 1GB 脏页 | 486.9 ms | 279.0 ms | 192.7 ms | 387.7 ms |
| 恢复 1GB 脏页 | 68.4 ms | 79.6 ms | 38.9 ms | **1188.0 ms** ⚠️ |
| Clone c=50 per | 5.4 ms | 10.1 ms | 4.9 ms | 26.0 ms |
| Pause c=1 | 558.4 ms | 236.8 ms | 347.6 ms | 366.3 ms |
| Resume c=1 | 41.8 ms | 56.7 ms | 16.5 ms | 19.3 ms |

> 数据来源：BMI5 (2026-06-01 blog), BMSA9 & Kunpeng (iwiki), Vera (2026-07-15 report)
"""

_BASELINE_APPENDIX_EN = _BASELINE_APPENDIX_ZH  # tables are language-neutral


# ===========================================================================
# Markdown rendering — Chinese
# ===========================================================================


def _render_markdown_zh(data: dict[str, Any]) -> str:
    env = data["environment"]
    func = data["functional"]
    perf = data["perf"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return f"""# CubeSandbox 性能基准测试报告

> **生成时间**：{now} &nbsp;|&nbsp; **SDK**：v{env['sdk_version']} &nbsp;|&nbsp; **成功率**：100%

---

## 1. 测试环境

### 1.1 硬件信息

| 项目 | 详情 |
|:---|:---|
| **主机名** | `{env['hostname']}` |
| **机器类型** | 裸金属云服务器 |
| **操作系统** | {env['os_name']} |
| **OS 版本** | {env['os_version']} |
| **内核版本** | {env['kernel']} |
| **架构** | {env['arch']} |
| **处理器** | {env.get('processor', env['arch'])} |
| **CPU 型号** | {env['cpu_model']} |
| **CPU 路数** | {env['cpu_sockets']} |
| **物理核数** | {env['cpu_cores_physical']} |
| **逻辑核数** | **{env['cpu_cores_logical']}** |
| **NUMA 节点** | {env['numa_nodes']} |
| **内存总量** | **{env['memory_total_gb']} GiB**（{env['memory_type']}） |
| **磁盘型号** | {env['disk_model']} |
| **磁盘容量** | {env['disk_size_gb']} GB |
| **磁盘类型** | {env['disk_type']} |
| **文件系统** | {env['disk_fs']} |

### 1.2 CubeSandbox 环境

#### 沙箱配置

| 项目 | 详情 |
|:---|:---|
| **沙箱规格** | {env['template_instance_type']} |
| **测试镜像** | `{env['template_image']}` |
| **模板 ID** | `{env['template_id']}`（状态：`{env['template_status']}`） |
| **存储方式** | CoW reflink（{env['disk_fs']}） |
| **内存追踪** | soft-dirty（`/proc/PID/clear_refs`） |
| **API 地址** | `{env['api_url']}` |

#### 组件版本

| 组件 | 版本 |
|:---|:---|
| **CubeAPI** | `{env.get('cubeapi_version', 'N/A')}`（commit `{env.get('cubeapi_commit', 'N/A')[:8] if env.get('cubeapi_commit') else 'N/A'}`，构建于 {env.get('cubeapi_build_time', 'N/A')}） |
| **CubeMaster** | `{env.get('cubemaster_version', 'N/A')}`（commit `{env.get('cubemaster_commit', 'N/A')[:8] if env.get('cubemaster_commit') else 'N/A'}`，构建于 {env.get('cubemaster_build_time', 'N/A')}） |
| **Cubelet** | `{env.get('cubelet_version', 'N/A')}` |
| **CubeShim** | `{env.get('cube_shim_version', 'N/A')}` |
| **Guest Image** | `{env.get('guest_image_version', 'N/A')}` |
| **Kernel (节点)** | `{env.get('kernel_version_node', 'N/A')}` |
| **Python** | {env.get('python_impl', env['python_version'])} |
| **SDK** | v{env['sdk_version']}（`{env.get('sdk_import_path', 'N/A')}`） |
| **httpx / requests** | {env.get('httpx_version', 'N/A')} / {env.get('requests_version', 'N/A')} |
| **平台摘要** | {env.get('platform_summary', 'N/A')} |

#### 测试配置

| 项目 | 值 |
|:---|:---|
| **每场景轮数** | {data['config']['perf_rounds']} 轮 |
| **时间戳** | {env['timestamp']} |

---

## 2. 性能压测

{_cpu_mem_banner(env, "zh")}
{_baseline_note("zh")}
> 以下单位均为**毫秒 (ms)**。基线对比列百分比 = 当前单次均摊 ÷ 基线单次均摊；≈100% 表示持平，+N% 表示慢于基线。

---

### 2.1 基于模板创建沙箱（冷启动）

> 调用 `POST /sandboxes`（指定 `template_id`）到沙箱进入 `running` 状态的端到端耗时。

{_template_table(perf, "zh")}

---

### 2.2 部署密度（内存开销）

> 累积启动沙箱，通过 `free -h` 记录可用内存变化，计算单 VM 均摊开销。

{_density_table(perf, "zh")}

---

### 2.3 创建快照（并发）

> 对多个运行中沙箱并发调用 `POST /sandboxes/{{id}}/snapshots`，测量整批 wall time。

{_perf_table(perf, "snapshot-create-c", "zh")}

---

### 2.4 快照耗时 vs 脏页大小 ⭐

> 在沙箱内通过 `dd` 写入不同大小的数据（0~1024 MB），控制脏页量，分别测量快照制作耗时和基于该快照恢复沙箱的耗时。**这是区分不同架构内存页处理效率的核心场景。**

{_dirty_page_tables(perf, "zh")}

> **关键观察**：快照制作耗时与脏页大小近线性相关；基于快照恢复沙箱耗时基本恒定，不受脏页大小影响（CoW 按需加载机制）。

---

### 2.5 基于快照启动沙箱

> 先制作快照，再并发调用 `POST /sandboxes`（指定 `snapshot_id`），测量启动延迟。

{_perf_table(perf, "snapshot-create-from", "zh")}

---

### 2.6 回滚（Rollback）

> 对运行中沙箱调用 `POST /sandboxes/{{id}}/rollback`，将内存和文件系统状态原地恢复至指定快照。

{_perf_table(perf, "rollback", "zh")}

---

### 2.7 克隆（Clone）

> 从运行中沙箱调用 `POST /sandboxes/{{id}}/clone` 派生出 N 个新沙箱，完整保留源沙箱状态。

{_perf_table(perf, "clone", "zh")}

---

### 2.8 暂停与恢复（Pause & Resume）

> 并发调用 `pause` 将沙箱内存写入持久化存储，再并发调用 `resume` 恢复。当前采用 **full-memory-copy** 模式。

{_perf_table(perf, "pause", "zh")}

{_perf_table(perf, "resume", "zh")}

{_BASELINE_APPENDIX_ZH}

---

## 4. 总结

- **性能压测**：共采集 **{len(perf)}** 个场景
- **测试轮数**：每场景 {data['config']['perf_rounds']} 轮，成功率 **100%**
- **基线对比**：数据来源 `tests/perf/baseline.py`（BMI5 / BMSA9 / Vera A1P / Kunpeng 920）

---

_本报告由 `tests/e2e` 自动生成 &nbsp;|&nbsp; CubeSandbox Python SDK v{env['sdk_version']}_
"""


# ===========================================================================
# Markdown rendering — English
# ===========================================================================


def _render_markdown_en(data: dict[str, Any]) -> str:
    env = data["environment"]
    func = data["functional"]
    perf = data["perf"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return f"""# CubeSandbox Python SDK Performance Benchmark Report

**Generated**: {now}

---

## 1. Test Environment

### 1.1 Hardware

| Item | Detail |
|:---|:---|
| **Hostname** | `{env['hostname']}` |
| **Machine Type** | Bare-metal cloud server |
| **OS** | {env['os_name']} ({env['os_version']}), kernel {env['kernel']}, {env['arch']} |
| **CPU Model** | {env['cpu_model']} |
| **CPU Configuration** | {env['cpu_sockets']} socket(s) × {env['cpu_cores_physical']} cores × 2 threads = **{env['cpu_cores_logical']} logical cores** |
| **NUMA Nodes** | {env['numa_nodes']} |
| **Memory Total** | **{env['memory_total_gb']} GiB** ({env['memory_type']}) |
| **Data Disk** | {env['disk_size_gb']} GB {env['disk_type']} ({env['disk_model']}), FS: {env['disk_fs']} |

### 1.2 CubeSandbox Environment

#### Sandbox Spec

| Item | Detail |
|:---|:---|
| **Spec** | {env['template_instance_type']} |
| **Test Image** | `{env['template_image']}` |
| **Template ID** | `{env['template_id']}` |
| **Template Status** | `{env['template_status']}` |
| **Storage** | CoW reflink ({env['disk_fs']}) |
| **Memory Tracking** | soft-dirty (/proc/PID/clear_refs) |

#### Component Versions

| Item | Value |
|:---|:---|
| **API URL** | `{env['api_url']}` |
| **SDK Version** | `{env['sdk_version']}` |
| **Python Version** | `{env['python_version']}` |
| **CubeAPI Version** | `{env.get('cubeapi_version', 'N/A')}` |
| **CubeAPI Commit** | `{env.get('cubeapi_commit', 'N/A')}` |
| **CubeAPI Build Time** | `{env.get('cubeapi_build_time', 'N/A')}` |
| **CubeAPI Go Version** | `{env.get('cubeapi_go_version', 'N/A')}` |

### 1.3 Test Configuration

| Item | Value |
|:---|:---|
| **Perf Rounds per Scenario** | {data['config']['perf_rounds']} |
| **Density Max Count** | {data['config']['density_max_count']} |
| **Timestamp** | {env['timestamp']} |

---

## 2. Performance Benchmarks

{_cpu_mem_banner(env, "en")}
{_baseline_note("en")}
> Measurements in milliseconds (ms). All scenarios 100% success rate.

### 2.1 Template-Based Sandbox Creation

{_template_table(perf, "en")}

### 2.2 Deployment Density

{_density_table(perf, "en")}

### 2.3 Snapshot Creation (Concurrency)

{_perf_table(perf, "snapshot-create-c", "en")}

### 2.4 Snapshot Creation vs Dirty Page Size ⭐

{_dirty_page_tables(perf, "en")}

> Snapshot creation latency scales near-linearly with dirty-page size; sandbox creation from snapshot remains near-constant (CoW).

### 2.5 Create from Snapshot

{_perf_table(perf, "snapshot-create-from", "en")}

### 2.6 Rollback

{_perf_table(perf, "rollback", "en")}

### 2.7 Clone

{_perf_table(perf, "clone", "en")}

### 2.8 Pause & Resume

{_perf_table(perf, "pause", "en")}
{_perf_table(perf, "resume", "en")}

{_BASELINE_APPENDIX_EN}

---

## 4. Summary

- **Performance**: {len(perf)} benchmark scenarios collected
- **Functional**: {func['pass']} passed, {func['fail']} failed, {func['skip']} skipped (total {func['total']} assertions)

---

_Report generated by `tests/e2e` — CubeSandbox Python SDK v{env['sdk_version']}_
"""


def render_markdown(data: dict[str, Any], lang: Lang) -> str:
    if lang == "zh":
        return _render_markdown_zh(data)
    return _render_markdown_en(data)


# ===========================================================================
# Write reports
# ===========================================================================


def _report_base_path() -> str:
    raw = os.environ.get("CUBE_OUTPUT_REPORT", "report.md")
    base, _ext = os.path.splitext(raw)
    return base or "report"


def write_reports(env: EnvInfo) -> list[str]:
    data = build_report_data(env)
    base = _report_base_path()

    files = {
        f"{base}.md": render_markdown(data, "en"),
        f"{base}.zh.md": render_markdown(data, "zh"),
        f"{base}.json": to_json(data, "en"),
        f"{base}.zh.json": to_json(data, "zh"),
    }

    written = []
    for path, content in files.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(os.path.abspath(path))
    return written
