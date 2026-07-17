# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Report generation: JSON + Markdown, English & Chinese.

`build_report_data()` collects a single language-neutral snapshot of the run
(environment, functional results, perf results). `render_markdown()` and
`to_json()` then project that snapshot into the requested language.

Output files (base name from `CUBE_OUTPUT_REPORT`, default "report"):
    report.md      - Markdown, English
    report.zh.md    - Markdown, Chinese
    report.json     - JSON, English (summary/labels localized, data identical)
    report.zh.json   - JSON, Chinese
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Literal

from .config import DENSITY_COUNT, PERF_ROUNDS
from .env import EnvInfo
from .runner import FAIL, PASS, PERF_RESULTS, SKIP, RESULTS, PerfResult

Lang = Literal["en", "zh"]

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
# Markdown rendering
# ===========================================================================


def _fmt_ms(ms: float) -> str:
    return f"{ms:.1f} ms"


def _perf_table(perf: list[dict[str, Any]], scenario_prefix: str, lang: Lang) -> str:
    rows = [r for r in perf if r["scenario"].startswith(scenario_prefix)]
    if not rows:
        return "_No data_ / _无数据_\n" if lang == "zh" else "_No data_\n"

    if lang == "zh":
        header = "| 场景 | 次数 | 并发 | 平均值 | 最小值 | P50 | P95 | 最大值 | 总耗时 | 单次均摊 |"
    else:
        header = "| Scenario | N | Concurrency | avg | min | p50 | p95 | max | wall | per |"
    lines = [header, "|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|"]
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['count']} | {r['concurrency']} | "
            f"{_fmt_ms(r['avg_ms'])} | {_fmt_ms(r['min_ms'])} | {_fmt_ms(r['p50_ms'])} | "
            f"{_fmt_ms(r['p95_ms'])} | {_fmt_ms(r['max_ms'])} | "
            f"{_fmt_ms(r['wall_ms'])} | {_fmt_ms(r['per_ms'])} |"
        )
    return "\n".join(lines) + "\n"


def _render_markdown_en(data: dict[str, Any]) -> str:
    env = data["environment"]
    func = data["functional"]
    perf = data["perf"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    overall = "✅ ALL PASSED" if func["fail"] == 0 else f"❌ {func['fail']} FAILURES"

    return f"""# CubeSandbox Python SDK Integration & Performance Report

**Generated**: {now}
**SDK Version**: {env['sdk_version']}
**Python Version**: {env['python_version']}

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

## 2. Functional Test Results

| Result | Count |
|:---|:---|
| ✅ Passed | {func['pass']} |
| ❌ Failed | {func['fail']} |
| ⚠️ Skipped | {func['skip']} |
| **Total** | **{func['total']}** |

**Overall Status**: {overall}

---

## 3. Performance Benchmarks

> Measurements in milliseconds (ms). Each scenario runs {data['config']['perf_rounds']} rounds unless otherwise noted.
> **avg** / **min** / **p50** / **p95** / **max**: statistics across individual operations.
> **wall**: total wall-clock time for the batch. **per**: amortized per-operation time (wall ÷ N).

### 3.1 Template-Based Sandbox Creation

{_perf_table(perf, "template-create", "en")}

### 3.2 Snapshot Creation

{_perf_table(perf, "snapshot-create", "en")}

### 3.3 Create from Snapshot

{_perf_table(perf, "snapshot-create-from", "en")}

### 3.4 Rollback

{_perf_table(perf, "rollback", "en")}

### 3.5 Clone

{_perf_table(perf, "clone", "en")}

### 3.6 Pause & Resume

{_perf_table(perf, "pause", "en")}
{_perf_table(perf, "resume", "en")}

### 3.7 Deployment Density

{_perf_table(perf, "deployment-density", "en")}

---

## 4. Summary

- **Functional**: {func['pass']} passed, {func['fail']} failed, {func['skip']} skipped (total {func['total']} assertions)
- **Performance**: {len(perf)} benchmark scenarios collected

---

_Report generated by `tests/e2e` — CubeSandbox Python SDK v{env['sdk_version']}_
"""


def _render_markdown_zh(data: dict[str, Any]) -> str:
    env = data["environment"]
    func = data["functional"]
    perf = data["perf"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    overall = "✅ 全部通过" if func["fail"] == 0 else f"❌ {func['fail']} 项失败"

    return f"""# CubeSandbox Python SDK 集成与性能测试报告

**生成时间**: {now}
**SDK 版本**: {env['sdk_version']}
**Python 版本**: {env['python_version']}

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

## 2. 功能测试结果

| 结果 | 数量 |
|:---|:---|
| ✅ 通过 | {func['pass']} |
| ❌ 失败 | {func['fail']} |
| ⚠️ 跳过 | {func['skip']} |
| **总计** | **{func['total']}** |

**整体状态**: {overall}

---

## 3. 性能压测

> 单位：毫秒 (ms)。除特别说明外，每个场景运行 {data['config']['perf_rounds']} 轮。
> **平均值 / 最小值 / P50 / P95 / 最大值**：单次操作的统计指标。
> **总耗时**：整批操作的总墙钟时间。**单次均摊**：总耗时 ÷ 操作数（N）。

### 3.1 基于模板创建沙箱

{_perf_table(perf, "template-create", "zh")}

### 3.2 创建快照

{_perf_table(perf, "snapshot-create", "zh")}

### 3.3 基于快照创建沙箱

{_perf_table(perf, "snapshot-create-from", "zh")}

### 3.4 回滚（Rollback）

{_perf_table(perf, "rollback", "zh")}

### 3.5 克隆（Clone）

{_perf_table(perf, "clone", "zh")}

### 3.6 暂停与恢复

{_perf_table(perf, "pause", "zh")}
{_perf_table(perf, "resume", "zh")}

### 3.7 部署密度

{_perf_table(perf, "deployment-density", "zh")}

---

## 4. 总结

- **功能测试**：{func['pass']} 通过，{func['fail']} 失败，{func['skip']} 跳过（共 {func['total']} 项断言）
- **性能压测**：共采集 {len(perf)} 个压测场景

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
