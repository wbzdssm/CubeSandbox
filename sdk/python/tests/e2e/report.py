# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Report generation: JSON + Markdown, English & Chinese.

The Markdown report now includes baseline comparison columns (vs BMI5 /
BMSA9 / Vera / Kunpeng) sourced from the sibling `tests/perf/baseline.py`
module. CPU and memory info is shown prominently in a summary banner above
the benchmark tables so that cross-machine comparisons are self-contained.

Output files (base name from `CUBE_OUTPUT_REPORT`, default "report"):
    report.md      - Markdown, English
    report.zh.md    - Markdown, Chinese
    report.json     - JSON, English (summary/labels localized, data identical)
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
# Lazy import of baseline data (the perf package may not be on sys.path
# when e2e is used standalone, but both live under tests/)
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
        # Ensure tests/ is on sys.path so that `perf` is importable
        _tests_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _tests_dir not in sys.path:
            sys.path.insert(0, _tests_dir)
        from perf.baseline import ALL_BASELINES as bl  # type: ignore[import-untyped]

        _ALL_BASELINES = bl
        _BASELINE_KEYS = list(bl.keys())
    except ImportError:
        pass


# ===========================================================================
# Data snapshot (language-neutral)
# ===========================================================================


def _perf_result_to_dict(r: PerfResult) -> dict[str, Any]:
    extra = r.samples[0].extra if r.samples else {}
    return {
        "scenario": r.scenario,
        "count": r.count,
        "concurrency": r.concurrency,
        "avg_ms": round(r.avg, 2),
        "min_ms": round(r.min, 2),
        "p50_ms": round(r.p50, 2),
        "p95_ms": round(r.p95, 2),
        "max_ms": round(r.max, 2),
        "wall_ms": round(extra.get("wall_ms", 0), 2),
        "per_ms": round(extra.get("per_ms", 0), 2),
        "extra": {k: v for k, v in extra.items() if k not in ("wall_ms", "per_ms")},
    }


def build_report_data(env: EnvInfo) -> dict[str, Any]:
    """Build a single language-neutral snapshot of the current run's results."""
    total = PASS + FAIL + SKIP
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": {
            "hostname": env.hostname,
            "os_name": env.os_name,
            "os_version": env.os_version,
            "kernel": env.kernel,
            "arch": env.arch,
            "cpu_model": env.cpu_model,
            "cpu_cores_physical": env.cpu_cores_physical,
            "cpu_cores_logical": env.cpu_cores_logical,
            "cpu_sockets": env.cpu_sockets,
            "numa_nodes": env.numa_nodes,
            "memory_total_gb": env.memory_total_gb,
            "memory_type": env.memory_type,
            "disk_model": env.disk_model,
            "disk_size_gb": env.disk_size_gb,
            "disk_fs": env.disk_fs,
            "disk_type": env.disk_type,
            "python_version": env.python_version,
            "sdk_version": env.sdk_version,
            "api_url": env.api_url,
            "template_id": env.template_id,
            "template_image": env.template_image,
            "template_instance_type": env.template_instance_type,
            "template_status": env.template_status,
            "timestamp": env.timestamp,
            "cubeapi_version": env.cubeapi_version,
            "cubeapi_commit": env.cubeapi_commit,
            "cubeapi_build_time": env.cubeapi_build_time,
            "cubeapi_go_version": env.cubeapi_go_version,
        },
        "config": {
            "perf_rounds": PERF_ROUNDS,
            "density_max_count": DENSITY_COUNT,
        },
        "functional": {
            "pass": PASS,
            "fail": FAIL,
            "skip": SKIP,
            "total": total,
            "results": list(RESULTS),
        },
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
    """Render *data* as a JSON string, adding a localized `language`/`overall_status`."""
    fail_count = data["functional"]["fail"]
    status_map = _JSON_OVERALL_STATUS[lang]
    payload = dict(data)
    payload["language"] = lang
    payload["overall_status"] = status_map["pass"] if fail_count == 0 else status_map["fail"]
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ===========================================================================
# Markdown rendering — helpers
# ===========================================================================


def _fmt_ms(ms: float) -> str:
    if ms == 0:
        return "—"
    return f"{ms:.1f} ms"


def _cmp_str(current: float, baseline: float | None) -> str:
    """Return a comparison string like `=95%` / `+12%` / `-8%` or `—`."""
    if baseline is None or baseline == 0 or current <= 0:
        return "—"
    ratio = current / baseline
    if ratio <= 1.05:
        return f"≈{ratio * 100:.0f}%"
    return f"+{(ratio - 1) * 100:.0f}%"


def _cmp_for_row(r: dict[str, Any], baseline_key: str) -> str:
    """Return a comparison badge for a given baseline key."""
    _ensure_baselines()
    bl = _ALL_BASELINES.get(baseline_key)
    if not bl or "perf" not in bl:
        return "—"
    bb = bl["perf"].get(r["scenario"])
    if not bb:
        return "—"
    bl_per = bb.get("per") or bb.get("avg") or bb.get("wall_avg") or 0
    current_per = r.get("per_ms") or r.get("avg_ms") or 0
    return _cmp_str(current_per, bl_per)


def _perf_table(perf: list[dict[str, Any]], scenario_prefix: str, lang: Lang) -> str:
    """Build a Markdown table for perf scenarios matching *scenario_prefix*,
    including baseline comparison columns.
    """
    rows = [r for r in perf if r["scenario"].startswith(scenario_prefix)]
    if not rows:
        return "_No data_ / _无数据_\n" if lang == "zh" else "_No data_\n"

    _ensure_baselines()

    if lang == "zh":
        base_cols = ["场景", "次数", "并发", "平均值", "最小值", "P50", "P95", "最大值", "总耗时", "单次均摊"]
    else:
        base_cols = ["Scenario", "N", "Concurrency", "avg", "min", "p50", "p95", "max", "wall", "per"]

    # Add baseline comparison columns
    cmp_cols: list[str] = []
    for key in _BASELINE_KEYS:
        short = key.split("(")[0].strip()
        cmp_cols.append(f"vs {short}" if lang == "en" else f"vs {short}")

    all_cols = base_cols + cmp_cols
    header = "| " + " | ".join(all_cols) + " |"
    sep = "|" + "|".join([":---"] * len(all_cols)) + "|"
    lines = [header, sep]

    for r in rows:
        base_vals = [
            r["scenario"],
            str(r["count"]),
            str(r["concurrency"]),
            _fmt_ms(r["avg_ms"]),
            _fmt_ms(r["min_ms"]),
            _fmt_ms(r["p50_ms"]),
            _fmt_ms(r["p95_ms"]),
            _fmt_ms(r["max_ms"]),
            _fmt_ms(r["wall_ms"]),
            _fmt_ms(r["per_ms"]),
        ]
        cmp_vals = [_cmp_for_row(r, key) for key in _BASELINE_KEYS]
        lines.append("| " + " | ".join(base_vals + cmp_vals) + " |")

    return "\n".join(lines) + "\n"


def _cpu_mem_banner(env: dict[str, Any], lang: Lang) -> str:
    """Return a one-line CPU/Memory summary for the benchmark section header."""
    cpu = f"{env.get('cpu_model', 'N/A')[:50]}"
    cores = env.get("cpu_cores_logical", "?")
    mem = env.get("memory_total_gb", "?")
    arch = env.get("arch", "?")
    if lang == "zh":
        return (
            f"> **测试机型**：{cpu} | **架构**：{arch} | "
            f"**CPU 逻辑核**：{cores} | **内存**：{mem} GiB\n"
        )
    return (
        f"> **Machine**：{cpu} | **Arch**：{arch} | "
        f"**CPU logical cores**：{cores} | **Memory**：{mem} GiB\n"
    )


def _baseline_note(lang: Lang) -> str:
    """Return a note explaining baseline comparison columns."""
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
# Markdown rendering — English
# ===========================================================================


def _render_markdown_en(data: dict[str, Any]) -> str:
    env = data["environment"]
    func = data["functional"]
    perf = data["perf"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    overall = "✅ ALL PASSED" if func["fail"] == 0 else f"❌ {func['fail']} FAILURES"

    return f"""# CubeSandbox Python SDK Integration & Performance Report

**Generated**: {now}

---

## 1. Test Environment

### 1.1 Host Machine

| Item | Value |
|:---|:---|
| **Hostname** | `{env['hostname']}` |
| **OS** | {env['os_name']} ({env['os_version']}) |
| **Kernel** | {env['kernel']} |
| **Architecture** | {env['arch']} |
| **CPU Model** | {env['cpu_model']} |
| **CPU Configuration** | {env['cpu_sockets']} socket(s) × {env['cpu_cores_physical']} cores × 2 threads = **{env['cpu_cores_logical']} logical cores** |
| **NUMA Nodes** | {env['numa_nodes']} |
| **Memory Total** | **{env['memory_total_gb']} GiB** ({env['memory_type']}) |
| **Data Disk** | {env['disk_size_gb']} GB {env['disk_type']} ({env['disk_model']}), FS: {env['disk_fs']} |

### 1.2 SDK & API

| Item | Value |
|:---|:---|
| **API URL** | `{env['api_url']}` |
| **SDK Version** | `{env['sdk_version']}` |
| **Python Version** | `{env['python_version']}` |
| **CubeAPI Version** | `{env.get('cubeapi_version', 'N/A')}` |
| **CubeAPI Commit** | `{env.get('cubeapi_commit', 'N/A')}` |
| **CubeAPI Build Time** | `{env.get('cubeapi_build_time', 'N/A')}` |
| **CubeAPI Go Version** | `{env.get('cubeapi_go_version', 'N/A')}` |
| **Template ID** | `{env['template_id']}` |
| **Template Image** | `{env['template_image']}` |
| **Template Instance Type** | `{env['template_instance_type']}` |
| **Template Status** | `{env['template_status']}` |

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
> Measurements in milliseconds (ms). Each scenario runs {data['config']['perf_rounds']} rounds unless otherwise noted.
> **avg** / **min** / **p50** / **p95** / **max**: statistics across individual operations.
> **wall**: total wall-clock time for the batch. **per**: amortized per-operation time (wall ÷ N).

### 2.1 Template-Based Sandbox Creation

{_perf_table(perf, "template-create", "en")}

### 2.2 Snapshot Creation

{_perf_table(perf, "snapshot-create", "en")}

### 2.3 Create from Snapshot

{_perf_table(perf, "snapshot-create-from", "en")}

### 2.4 Rollback

{_perf_table(perf, "rollback", "en")}

### 2.5 Clone

{_perf_table(perf, "clone", "en")}

### 2.6 Pause & Resume

{_perf_table(perf, "pause", "en")}
{_perf_table(perf, "resume", "en")}

### 2.7 Deployment Density

{_perf_table(perf, "deployment-density", "en")}

---

## 3. Summary

- **Performance**: {len(perf)} benchmark scenarios collected
- **Functional**: {func['pass']} passed, {func['fail']} failed, {func['skip']} skipped (total {func['total']} assertions)

---

_Report generated by `tests/e2e` — CubeSandbox Python SDK v{env['sdk_version']}_
"""


# ===========================================================================
# Markdown rendering — Chinese
# ===========================================================================


def _render_markdown_zh(data: dict[str, Any]) -> str:
    env = data["environment"]
    func = data["functional"]
    perf = data["perf"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    overall = "✅ 全部通过" if func["fail"] == 0 else f"❌ {func['fail']} 项失败"

    return f"""# CubeSandbox Python SDK 集成与性能测试报告

**生成时间**: {now}

---

## 1. 测试环境

### 1.1 主机信息

| 项目 | 值 |
|:---|:---|
| **主机名** | `{env['hostname']}` |
| **操作系统** | {env['os_name']} ({env['os_version']}) |
| **内核版本** | {env['kernel']} |
| **架构** | {env['arch']} |
| **CPU 型号** | {env['cpu_model']} |
| **CPU 配置** | {env['cpu_sockets']} 路 × {env['cpu_cores_physical']} 核 × 2 线程 = **{env['cpu_cores_logical']} 逻辑核心** |
| **NUMA 节点数** | {env['numa_nodes']} |
| **内存总量** | **{env['memory_total_gb']} GiB** ({env['memory_type']}) |
| **数据磁盘** | {env['disk_size_gb']} GB {env['disk_type']} ({env['disk_model']}), 文件系统: {env['disk_fs']} |

### 1.2 SDK 与 API

| 项目 | 值 |
|:---|:---|
| **API 地址** | `{env['api_url']}` |
| **SDK 版本** | `{env['sdk_version']}` |
| **Python 版本** | `{env['python_version']}` |
| **CubeAPI 版本** | `{env.get('cubeapi_version', 'N/A')}` |
| **CubeAPI Commit** | `{env.get('cubeapi_commit', 'N/A')}` |
| **CubeAPI 构建时间** | `{env.get('cubeapi_build_time', 'N/A')}` |
| **CubeAPI Go 版本** | `{env.get('cubeapi_go_version', 'N/A')}` |
| **模板 ID** | `{env['template_id']}` |
| **模板镜像** | `{env['template_image']}` |
| **模板实例类型** | `{env['template_instance_type']}` |
| **模板状态** | `{env['template_status']}` |

### 1.3 测试配置

| 项目 | 值 |
|:---|:---|
| **每个场景压测轮数** | {data['config']['perf_rounds']} |
| **密度测试最大数量** | {data['config']['density_max_count']} |
| **时间戳** | {env['timestamp']} |

---

## 2. 性能压测

{_cpu_mem_banner(env, "zh")}
{_baseline_note("zh")}
> 单位：毫秒 (ms)。除特别说明外，每个场景运行 {data['config']['perf_rounds']} 轮。
> **平均值 / 最小值 / P50 / P95 / 最大值**：单次操作的统计指标。
> **总耗时**：整批操作的总墙钟时间。**单次均摊**：总耗时 ÷ 操作数（N）。

### 2.1 基于模板创建沙箱

{_perf_table(perf, "template-create", "zh")}

### 2.2 创建快照

{_perf_table(perf, "snapshot-create", "zh")}

### 2.3 基于快照创建沙箱

{_perf_table(perf, "snapshot-create-from", "zh")}

### 2.4 回滚（Rollback）

{_perf_table(perf, "rollback", "zh")}

### 2.5 克隆（Clone）

{_perf_table(perf, "clone", "zh")}

### 2.6 暂停与恢复

{_perf_table(perf, "pause", "zh")}
{_perf_table(perf, "resume", "zh")}

### 2.7 部署密度

{_perf_table(perf, "deployment-density", "zh")}

---

## 3. 总结

- **性能压测**：共采集 {len(perf)} 个压测场景
- **功能测试**：{func['pass']} 通过，{func['fail']} 失败，{func['skip']} 跳过（共 {func['total']} 项断言）

---

_本报告由 `tests/e2e` 生成 — CubeSandbox Python SDK v{env['sdk_version']}_
"""


def render_markdown(data: dict[str, Any], lang: Lang) -> str:
    if lang == "zh":
        return _render_markdown_zh(data)
    return _render_markdown_en(data)


# ===========================================================================
# Top-level: write all report files
# ===========================================================================


def _report_base_path() -> str:
    raw = os.environ.get("CUBE_OUTPUT_REPORT", "report.md")
    base, _ext = os.path.splitext(raw)
    return base or "report"


def write_reports(env: EnvInfo) -> list[str]:
    """Build the report snapshot and write MD + JSON, English + Chinese, to disk.

    Returns the list of written file paths.
    """
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
