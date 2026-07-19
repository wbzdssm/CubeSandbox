# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""HTML performance report generator — multi-environment comparison layout.

Each report can hold several environments side by side:
- Input JSON files are grouped by an environment fingerprint
  (hostname + cpu_model + arch + kernel). Files sharing a fingerprint are
  averaged together (repeated runs of the same machine -> a stable line);
  files with different fingerprints become independent comparison series.
- Every scenario renders one chart: each measured environment is a solid
  line, each historical baseline is a dashed line. A summary table below
  lists every (concurrency, environment) row with a "vs first env" badge.
- The environment section is a multi-column table (one column per env).

A single input file degrades to a single series, matching the old behaviour.
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baseline import ALL_BASELINES

# Solid colors for measured environments (RUN_SERIES).
_RUN_COLORS = [
    "#667eea", "#e11d48", "#059669", "#d97706",
    "#7c3aed", "#0891b2", "#db2777", "#65a30d",
]
# Dashed, lighter colors for historical baselines.
_BASELINE_COLORS = [
    "#c4b5fd", "#86efac", "#fde68a", "#fca5a5",
    "#93c5fd", "#d8b4fe", "#a5f3fc", "#fed7aa",
]

# ---------------------------------------------------------------------------
# Report field definitions (env-var customizable)
# ---------------------------------------------------------------------------
# The environment / Cube-version sections render a fixed list of fields by
# default. Both lists can be tailored per run via environment variables so a
# report can surface any extra field an environment collector emits without a
# code change:
#   CUBE_REPORT_ENV_FIELDS   - replace the *entire* env field list
#   CUBE_REPORT_ENV_EXTRA    - append extra fields to the default env list
#   CUBE_REPORT_CUBE_FIELDS  - replace the *entire* Cube version field list
#   CUBE_REPORT_CUBE_EXTRA   - append extra fields to the default Cube list
# Each spec is a comma-separated list of `key` or `key:显示名`. When only a
# key is given, its default label is reused if known, otherwise the raw key.
#   e.g. CUBE_REPORT_ENV_EXTRA="git_commit:代码版本,region:地域"
_DEFAULT_ENV_FIELDS: list[tuple[str, str]] = [
    # Simplified "环境信息" block — mirrors the user-facing summary a human
    # would jot down (hostname/model/IP/OS/kernel/arch/CPU/mem/toolchains).
    # Add extras via CUBE_REPORT_ENV_EXTRA rather than editing this list.
    ("hostname", "主机名"),
    ("machine_type", "机型"),
    ("ip_address", "IP"),
    ("os_distro", "操作系统"),
    ("kernel", "内核版本"),
    ("arch", "架构"),
    ("cpu_cores_logical", "CPU 核数"),
    ("memory_total_gb", "内存 (GiB)"),
    ("gcc_version", "GCC 版本"),
    ("python_version", "Python 版本"),
    ("timestamp", "时间"),
]
_DEFAULT_CUBE_FIELDS: list[tuple[str, str]] = [
    # Simplified "Cube 信息" block — release + template + all component
    # versions, one field per line.  commit / build_time are still available
    # via CUBE_REPORT_CUBE_EXTRA when a diff needs sub-version detail.
    ("release_version", "Release 版本"),
    ("template_id", "模板 ID"),
    ("template_image", "镜像信息"),
    ("cubemaster_version", "CubeMaster 版本"),
    ("cubeapi_version", "CubeAPI 版本"),
    ("cubemastercli_version", "CubeMasterCLI 版本"),
    ("cubecli_version", "CubeCLI 版本"),
    ("cubelet_version", "Cubelet 版本"),
    ("cube_shim_version", "CubeShim 版本"),
    ("cube_runtime_version", "CubeRuntime 版本"),
    ("network_agent_version", "NetworkAgent 版本"),
    ("cube_egress_version", "CubeEgress 版本"),
    ("cube_lifecycle_manager_version", "CubeLifecycleManager 版本"),
    ("guest_image_version", "Guest Image 版本"),
    ("guest_agent_version", "Guest Agent 版本"),
    ("cube_agent_version", "CubeAgent 版本"),
    ("kernel_pvm_version", "PVM 内核"),
]


def _parse_field_spec(
    raw: str, defaults: list[tuple[str, str]]
) -> list[list[str]]:
    """Parse a "key" / "key:Label" comma list into [[key, label], ...].

    Unlabeled keys reuse the default label when known, else the raw key.
    """
    label_map = {k: lbl for k, lbl in defaults}
    out: list[list[str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            key, _, label = item.partition(":")
            key, label = key.strip(), label.strip()
        else:
            key, label = item, label_map.get(item, item)
        if key:
            out.append([key, label or key])
    return out


def _resolve_fields(
    override_var: str, extra_var: str, defaults: list[tuple[str, str]]
) -> list[list[str]]:
    """Resolve a display field list from env vars.

    If *override_var* is set it fully replaces the defaults; otherwise the
    defaults are used and any fields in *extra_var* are appended.
    """
    override = (os.environ.get(override_var) or "").strip()
    if override:
        return _parse_field_spec(override, defaults)
    fields = [[k, lbl] for k, lbl in defaults]
    extra = (os.environ.get(extra_var) or "").strip()
    if extra:
        fields += _parse_field_spec(extra, defaults)
    return fields


# ---------------------------------------------------------------------------
# Performance scenario groups (env-var customizable)
# ---------------------------------------------------------------------------
# Each scenario group renders one chart + summary table. The list is env-var
# customizable so a new benchmark scenario can be surfaced without a code edit:
#   CUBE_REPORT_SCENARIOS       - replace the *entire* scenario group list
#   CUBE_REPORT_SCENARIOS_EXTRA - append extra scenario groups
# Groups are separated by ";" and each group's fields by "|":
#   prefix|标题|xKey|fallback
# Only `prefix` is required. xKey defaults to "c"; fallback is a "+"-joined
# int list defaulting to "1+2+4". When `prefix` matches a default group its
# title/xKey/fallback are inherited for any omitted field.
#   e.g. CUBE_REPORT_SCENARIOS_EXTRA="fork|Fork 沙箱|c|1+2+4"
_DEFAULT_SCENARIOS: list[dict[str, Any]] = [
    {"id": "coldstart", "title": "基于模板创建沙箱（冷启动）", "prefix": "template-create", "xKey": "c", "fallback": [1, 2, 4], "xLabel": "并发数"},
    {"id": "snapshot", "title": "创建快照（并发）", "prefix": "snapshot-create", "xKey": "c", "fallback": [1, 2, 4], "xLabel": "并发数"},
    {"id": "createfrom", "title": "基于快照启动沙箱", "prefix": "snapshot-create-from", "xKey": "c", "fallback": [1, 2, 4], "xLabel": "并发数"},
    {"id": "rollback", "title": "回滚（Rollback）", "prefix": "rollback", "xKey": "c", "fallback": [1, 2, 4], "xLabel": "并发数"},
    {"id": "pause", "title": "暂停（Pause）", "prefix": "pause", "xKey": "c", "fallback": [1, 2, 4], "xLabel": "并发数"},
    {"id": "resume", "title": "恢复（Resume）", "prefix": "resume", "xKey": "c", "fallback": [1, 2, 4], "xLabel": "并发数"},
]

# Summary-table metric columns (env-var customizable)
#   CUBE_REPORT_METRICS       - replace the *entire* column list
#   CUBE_REPORT_METRICS_EXTRA - append extra columns
# Each column spec is "key" or "key:显示名" (comma-separated). `key` maps to a
# field of the per-scenario stats (avg_ms/min_ms/p50_ms/.../count/concurrency),
# plus three special keys:
#   scenario -> 场景名   env -> 环境标签(带色块)   vs -> vs 首环境对比徽标
#   e.g. CUBE_REPORT_METRICS_EXTRA="runs:重复次数"
_DEFAULT_METRICS: list[tuple[str, str]] = [
    ("scenario", "场景"), ("env", "环境"), ("count", "次数"),
    ("concurrency", "并发"), ("avg_ms", "平均值"), ("min_ms", "最小值"),
    ("p50_ms", "P50"), ("p95_ms", "P95"), ("p99_ms", "P99"),
    ("max_ms", "最大值"), ("wall_ms", "总耗时"), ("per_ms", "单次均摊"),
    ("vs", "vs 首环境"),
]


def _parse_scenarios(raw: str) -> list[dict[str, Any]]:
    """Parse a ";"-separated scenario spec into scenario-group dicts."""
    default_by_prefix = {g["prefix"]: g for g in _DEFAULT_SCENARIOS}
    out: list[dict[str, Any]] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        fields = [p.strip() for p in chunk.split("|")]
        prefix = fields[0]
        if not prefix:
            continue
        base = default_by_prefix.get(prefix, {})
        title = fields[1] if len(fields) > 1 and fields[1] else base.get("title", prefix)
        xkey = fields[2] if len(fields) > 2 and fields[2] else base.get("xKey", "c")
        fallback = base.get("fallback", [1, 2, 4])
        if len(fields) > 3 and fields[3]:
            try:
                fallback = [int(x) for x in fields[3].split("+") if x.strip()]
            except ValueError:
                pass
        out.append({
            "id": prefix.replace("-", "_"),
            "title": title,
            "prefix": prefix,
            "xKey": xkey,
            "fallback": fallback or [1, 2, 4],
            "xLabel": base.get("xLabel", "并发数"),
        })
    return out


def _resolve_scenarios() -> list[dict[str, Any]]:
    """Resolve the scenario-group list from env vars (override / extra)."""
    override = (os.environ.get("CUBE_REPORT_SCENARIOS") or "").strip()
    if override:
        return _parse_scenarios(override)
    groups = [dict(g) for g in _DEFAULT_SCENARIOS]
    extra = (os.environ.get("CUBE_REPORT_SCENARIOS_EXTRA") or "").strip()
    if extra:
        groups += _parse_scenarios(extra)
    return groups


_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f0f2f5; color: #1a1a2e; line-height: 1.6; }}
.header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 32px 24px; text-align: center; }}
.header h1 {{ font-size: 24px; margin-bottom: 6px; }}
.header .subtitle {{ opacity: 0.85; font-size: 13px; }}
.container {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
.section {{ background: white; border-radius: 10px; padding: 20px; margin: 16px 0; box-shadow: 0 1px 6px rgba(0,0,0,0.05); }}
.section h2 {{ font-size: 18px; color: #667eea; margin-bottom: 12px; border-bottom: 2px solid #e8ecf1; padding-bottom: 6px; }}
.env-text {{ font-size: 13px; }}
.env-text .env-name {{ font-weight: 600; margin: 10px 0 4px; }}
.env-text .env-lines {{ margin-bottom: 8px; }}
.env-text .env-lines div {{ padding: 2px 0; color: #333; }}
.env-text .env-lines .k {{ display: inline-block; min-width: 130px; color: #888; }}
.env-text .sub-title {{ font-size: 14px; font-weight: 600; color: #764ba2; margin: 12px 0 4px; }}
.chart-wrap {{ margin: 12px 0; }}
.chart-wrap canvas {{ max-height: 320px; }}
.data-table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 12px; }}
.data-table th, .data-table td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #e8ecf1; }}
.data-table th {{ background: #f0f2f5; font-weight: 600; color: #555; white-space: nowrap; }}
.data-table tr:hover {{ background: #fafbfc; }}
.badge {{ display: inline-block; padding: 2px 7px; border-radius: 8px; font-size: 11px; font-weight: 600; }}
.badge-good {{ background: #dcfce7; color: #16a34a; }}
.badge-warn {{ background: #fef3c7; color: #d97706; }}
.badge-bad {{ background: #fee2e2; color: #dc2626; }}
.badge-na {{ background: #f0f0f0; color: #999; }}
.legend {{ display: flex; gap: 14px; margin-bottom: 8px; font-size: 12px; flex-wrap: wrap; }}
.legend-item {{ display: flex; align-items: center; gap: 4px; }}
.legend-swatch {{ width: 16px; height: 3px; border-radius: 2px; }}
.legend-swatch.dashed {{ height: 0; border-top: 2px dashed; background: transparent !important; }}
.scenario-block {{ margin: 24px 0; }}
.scenario-block h3 {{ font-size: 16px; color: #444; margin-bottom: 8px; }}
.insight {{ background: #f6f4fd; border-left: 3px solid #764ba2; border-radius: 6px; padding: 10px 14px; margin: 8px 0 12px; font-size: 13px; color: #333; }}
.insight b {{ color: #764ba2; }}
.insight .hl {{ font-weight: 600; color: #e11d48; }}
.cube-compare td:first-child {{ font-weight: 600; color: #555; white-space: nowrap; }}
.cube-compare tr.diff-row {{ background: #fff4f4; }}
.cube-compare tr.diff-row td {{ color: #b91c1c; font-weight: 600; }}
.cube-compare tr.diff-row td:first-child {{ color: #b91c1c; }}
footer {{ text-align: center; padding: 20px; color: #aaa; font-size: 12px; }}
@media (max-width: 768px) {{ .container {{ padding: 10px; }} }}
</style>
</head>
<body>
<div class="header">
  <h1>CubeSandbox 性能基准测试报告</h1>
  <div class="subtitle">生成时间: {generated_at} &nbsp;|&nbsp; 对比环境: {env_count} &nbsp;|&nbsp; 历史基线: {baseline_count} &nbsp;|&nbsp; 场景数: {scenario_count}</div>
</div>

<div class="container">

<!-- Environment info -->
<div class="section">
  <h2>测试环境</h2>
  <div class="env-text" id="env-info"></div>
</div>

<!-- Cube component versions -->
<div class="section">
  <h2>Cube 组件版本</h2>
  <div class="env-text" id="cube-info"></div>
</div>

<!-- Perf Scenarios -->
<div class="section">
  <h2>性能压测结果</h2>
  <div class="legend" id="legend"></div>
  <div id="scenarios"></div>
</div>

<footer>CubeSandbox Python SDK 性能测试套件自动生成</footer>
</div>

<script>
const RUN_SERIES = {run_series_json};
const RUN_ENVS = {run_envs_json};
const RUN_COLORS = {run_colors_json};
const ALL_BASELINES = {all_baselines_json};
const BASELINE_KEYS = {baseline_keys_json};
const BASELINE_COLORS = {baseline_colors_json};

// ---- Helpers ----
function fmtMs(v) {{ return v === null || v === undefined || v === '' ? '-' : Number(v).toFixed(1); }}
function runColor(i) {{ return RUN_COLORS[i % RUN_COLORS.length]; }}
function baseColor(i) {{ return BASELINE_COLORS[i % BASELINE_COLORS.length]; }}
// Current value for a measured series at a scenario (per-op, fallback avg).
function runValueAt(series, scenario) {{
  const r = series.perf[scenario];
  return r ? (r.per_ms || r.avg_ms || null) : null;
}}
// Baseline value for a scenario (per / avg / wall_avg fallbacks).
function baseValueAt(key, scenario) {{
  const bl = ALL_BASELINES[key];
  if (!bl || !bl.perf) return null;
  const bb = bl.perf[scenario];
  return bb ? (bb.per || bb.avg || bb.wall_avg || null) : null;
}}
function cmpBadge(cur, ref) {{
  if (!ref || !cur || cur <= 0) return '<span class="badge badge-na">-</span>';
  const r = cur / ref;
  if (r <= 1.05) return `<span class="badge badge-good">≈${{(r*100).toFixed(0)}}%</span>`;
  if (r <= 1.20) return `<span class="badge badge-warn">+${{((r-1)*100).toFixed(0)}}%</span>`;
  if (r >= 0.80 && r < 0.95) return `<span class="badge badge-good">-${{((1-r)*100).toFixed(0)}}%</span>`;
  return `<span class="badge badge-bad">+${{((r-1)*100).toFixed(0)}}%</span>`;
}}

// ---- Environment info (plain text) ----
(function() {{
  // Field lists resolved in Python (env-var customizable) and injected here.
  const ENV_FIELDS = {env_fields_json};
  const CUBE_FIELDS = {cube_fields_json};

  function renderLines(env, fields) {{
    let html = '<div class="env-lines">';
    let any = false;
    fields.forEach(([k, label]) => {{
      const v = env[k];
      if (v === undefined || v === null || v === '') return;
      any = true;
      html += `<div><span class="k">${{label}}</span>${{v}}</div>`;
    }});
    html += '</div>';
    return any ? html : '';
  }}

  function fill(boxId, fields) {{
    const box = document.getElementById(boxId);
    let html = '';
    RUN_ENVS.forEach((e, i) => {{
      const lines = renderLines(e.env, fields);
      if (!lines) return;
      if (RUN_ENVS.length > 1) {{
        html += `<div class="env-name"><span style="color:${{runColor(i)}};">■</span> ${{e.label}}</div>`;
      }}
      html += lines;
    }});
    box.innerHTML = html || '<div class="env-lines"><div>-</div></div>';
  }}

  // Cube versions: single env -> plain text; multi env -> comparison table
  // with differing rows highlighted, so version-linked regressions stand out.
  function renderCube() {{
    const box = document.getElementById('cube-info');
    if (RUN_ENVS.length <= 1) {{
      const env = RUN_ENVS.length ? RUN_ENVS[0].env : {{}};
      box.innerHTML = renderLines(env, CUBE_FIELDS) || '<div class="env-lines"><div>-</div></div>';
      return;
    }}

    let head = '<tr><th>组件</th>';
    RUN_ENVS.forEach((e, i) => {{
      head += `<th><span style="color:${{runColor(i)}};">■</span> ${{e.label}}</th>`;
    }});
    head += '</tr>';

    let body = '';
    let diffCount = 0;
    CUBE_FIELDS.forEach(([k, label]) => {{
      const vals = RUN_ENVS.map(e => {{
        const v = e.env[k];
        return (v === undefined || v === null || v === '') ? '' : String(v);
      }});
      if (vals.every(v => v === '')) return;
      const differ = new Set(vals).size > 1;
      if (differ) diffCount++;
      body += `<tr class="${{differ ? 'diff-row' : ''}}"><td>${{label}}</td>`;
      vals.forEach(v => {{ body += `<td>${{v || '-'}}</td>`; }});
      body += '</tr>';
    }});

    const note = diffCount > 0
      ? `<div class="insight">检测到 <span class="hl">${{diffCount}}</span> 项组件版本在对比环境间存在差异（下表<span class="hl">高亮行</span>）。若下方性能压测中对应环境出现明显 <b>vs 首环境</b> 劣化，应优先排查这些版本差异导致的性能回退。</div>`
      : '<div class="insight">各对比环境的 Cube 组件版本一致，性能差异应归因于机器/负载而非组件版本。</div>';
    box.innerHTML = note + '<table class="data-table cube-compare">' + head + body + '</table>';
  }}

  fill('env-info', ENV_FIELDS);
  renderCube();
}})();

// ---- Legend ----
(function() {{
  const leg = document.getElementById('legend');
  RUN_ENVS.forEach((e, i) => {{
    leg.innerHTML += `<div class="legend-item"><div class="legend-swatch" style="background:${{runColor(i)}};"></div> ${{e.label}}</div>`;
  }});
  BASELINE_KEYS.forEach((k, i) => {{
    leg.innerHTML += `<div class="legend-item"><div class="legend-swatch dashed" style="border-color:${{baseColor(i)}};"></div> ${{k}}（基线）</div>`;
  }});
}})();

// ---- Scenario groups ----
// Scenario groups and summary-table metric columns are resolved in Python
// (env-var customizable, see module docstrings) and injected here. xValues
// are still discovered dynamically from the union of all series scenarios,
// so charts follow whatever CONCURRENCY_LEVELS was used at run time.
const SCENARIO_GROUPS = {scenarios_json};
const METRICS = {metrics_json};

(function() {{
  const container = document.getElementById('scenarios');

  // Union of every scenario name across measured series and baselines.
  const ALL_SCENARIOS = (function() {{
    const s = new Set();
    RUN_SERIES.forEach(sr => Object.keys(sr.perf).forEach(k => s.add(k)));
    BASELINE_KEYS.forEach(k => {{
      const bl = ALL_BASELINES[k];
      if (bl && bl.perf) Object.keys(bl.perf).forEach(x => s.add(x));
    }});
    return Array.from(s);
  }})();

  // Discover concurrency values for a prefix, e.g. "template-create"/"c" ->
  // [1, 5, 10]. The `$` anchor keeps "snapshot-create" from swallowing
  // "snapshot-create-from-*".
  function discoverXValues(prefix, xKey) {{
    const re = new RegExp('^' + prefix + '-' + xKey + '(\\\\d+)$');
    const found = new Set();
    ALL_SCENARIOS.forEach(name => {{
      const m = re.exec(name);
      if (m) found.add(parseInt(m[1], 10));
    }});
    return Array.from(found).sort((a, b) => a - b);
  }}

  function collectLineData(prefix, xKey, xValues) {{
    const runLines = RUN_SERIES.map((sr, i) => ({{
      label: sr.label, color: runColor(i),
      data: xValues.map((xv, xi) => ({{ x: xi, y: runValueAt(sr, `${{prefix}}-${{xKey}}${{xv}}`) }})),
    }}));
    const baseLines = BASELINE_KEYS.map((k, i) => ({{
      label: k, color: baseColor(i),
      data: xValues.map((xv, xi) => ({{ x: xi, y: baseValueAt(k, `${{prefix}}-${{xKey}}${{xv}}`) }})),
    }})).filter(l => l.data.some(p => p.y !== null));

    // Scatter of raw samples only when a single environment is compared,
    // otherwise the point cloud from many machines is unreadable.
    let scatters = [];
    if (RUN_SERIES.length === 1) {{
      xValues.forEach((xv, xi) => {{
        const r = RUN_SERIES[0].perf[`${{prefix}}-${{xKey}}${{xv}}`];
        if (r && r.raw_latencies) r.raw_latencies.forEach(lat => scatters.push({{ x: xi, y: lat }}));
      }});
    }}
    return {{ runLines, baseLines, scatters }};
  }}

  // Round a value up to a "nice" axis maximum (1/2/5 * 10^n).
  function niceCeil(v) {{
    if (v <= 0) return 1;
    const exp = Math.floor(Math.log10(v));
    const base = Math.pow(10, exp);
    const f = v / base;
    const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return nice * base;
  }}

  // Split a data array into continuous segments. When spanGaps is false a
  // null point breaks the line; when true nulls are simply skipped.
  function lineSegments(data, spanGaps) {{
    const segs = [];
    let cur = [];
    data.forEach((p, i) => {{
      if (p.y === null || p.y === undefined) {{
        if (!spanGaps && cur.length) {{ segs.push(cur); cur = []; }}
        return;
      }}
      cur.push({{ i: i, y: p.y }});
    }});
    if (cur.length) segs.push(cur);
    return segs;
  }}

  // Self-contained inline SVG line chart — no external chart library, so it
  // renders offline / inside the IDE preview where a CDN is unreachable.
  function renderChart(blockId, xLabels, runLines, baseLines, scatters) {{
    const W = 820, H = 320, padL = 52, padR = 16, padT = 14, padB = 38;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const n = xLabels.length;

    let maxY = 0;
    const consider = v => {{ if (v !== null && v !== undefined && v > maxY) maxY = v; }};
    runLines.forEach(l => l.data.forEach(p => consider(p.y)));
    baseLines.forEach(l => l.data.forEach(p => consider(p.y)));
    scatters.forEach(p => consider(p.y));
    const top = niceCeil(maxY);

    const xAt = i => padL + (n <= 1 ? plotW / 2 : plotW * i / (n - 1));
    const yAt = v => padT + plotH * (1 - v / top);

    const parts = [];
    parts.push(`<svg viewBox="0 0 ${{W}} ${{H}}" width="100%" style="max-height:320px;font-family:sans-serif;font-size:11px;">`);

    // Y gridlines + labels.
    const STEPS = 5;
    for (let s = 0; s <= STEPS; s++) {{
      const val = top * s / STEPS;
      const y = yAt(val);
      parts.push(`<line x1="${{padL}}" y1="${{y}}" x2="${{W - padR}}" y2="${{y}}" stroke="#eef0f4" stroke-width="1"/>`);
      parts.push(`<text x="${{padL - 6}}" y="${{y + 3}}" text-anchor="end" fill="#999">${{val.toFixed(0)}}</text>`);
    }}
    parts.push(`<text x="12" y="${{padT + plotH / 2}}" text-anchor="middle" fill="#888" transform="rotate(-90 12 ${{padT + plotH / 2}})">ms</text>`);

    // X axis labels.
    xLabels.forEach((lab, i) => {{
      parts.push(`<text x="${{xAt(i)}}" y="${{H - padB + 16}}" text-anchor="middle" fill="#666">${{lab}}</text>`);
    }});

    // Raw sample scatter (single-environment only).
    scatters.forEach(p => {{
      parts.push(`<circle cx="${{xAt(p.x)}}" cy="${{yAt(p.y)}}" r="2.5" fill="#667eea" fill-opacity="0.25"/>`);
    }});

    // Baselines: dashed polylines + dots.
    baseLines.forEach(l => {{
      lineSegments(l.data, true).forEach(seg => {{
        if (seg.length > 1) {{
          const pts = seg.map(pt => `${{xAt(pt.i)}},${{yAt(pt.y)}}`).join(' ');
          parts.push(`<polyline points="${{pts}}" fill="none" stroke="${{l.color}}" stroke-width="1.5" stroke-dasharray="6 3"/>`);
        }}
      }});
      l.data.forEach((p, i) => {{
        if (p.y === null || p.y === undefined) return;
        parts.push(`<circle cx="${{xAt(i)}}" cy="${{yAt(p.y)}}" r="2.5" fill="${{l.color}}"/>`);
      }});
    }});

    // Measured environments: solid polylines + square markers.
    runLines.forEach(l => {{
      if (l.data.every(p => p.y === null)) return;
      lineSegments(l.data, false).forEach(seg => {{
        if (seg.length > 1) {{
          const pts = seg.map(pt => `${{xAt(pt.i)}},${{yAt(pt.y)}}`).join(' ');
          parts.push(`<polyline points="${{pts}}" fill="none" stroke="${{l.color}}" stroke-width="2.5"/>`);
        }}
      }});
      l.data.forEach((p, i) => {{
        if (p.y === null || p.y === undefined) return;
        parts.push(`<rect x="${{xAt(i) - 3.5}}" y="${{yAt(p.y) - 3.5}}" width="7" height="7" rx="1.5" fill="${{l.color}}"/>`);
      }});
    }});

    parts.push('</svg>');

    const wrap = document.createElement('div');
    wrap.className = 'chart-wrap';
    wrap.innerHTML = parts.join('');
    return wrap;
  }}

  // Build a plain-language "key takeaway" for a scenario, focused on the
  // primary (first) measured environment. It separates two easily-confused
  // metrics: single-op latency (avg_ms) vs. amortized throughput (per_ms).
  function scenarioInsight(g, xValues) {{
    const primary = RUN_SERIES[0];
    if (!primary) return '';
    const rows = xValues.map(xv => {{
      const r = primary.perf[`${{g.prefix}}-${{g.xKey}}${{xv}}`];
      if (!r) return null;
      return {{ x: xv, avg: r.avg_ms, per: (r.per_ms || r.avg_ms), p95: r.p95_ms }};
    }}).filter(Boolean);
    if (!rows.length) return '';

    const first = rows[0], last = rows[rows.length - 1];
    // Fastest single-op latency and best amortized throughput across levels.
    let bestLat = rows[0], bestThr = rows[0];
    rows.forEach(r => {{
      if (r.avg < bestLat.avg) bestLat = r;
      if (r.per < bestThr.per) bestThr = r;
    }});

    const parts = [];
    parts.push(`<b>${{primary.label}}</b>：`);
    // Single-op latency trend.
    if (rows.length > 1 && Math.abs(last.avg - first.avg) > 0.05) {{
      const up = last.avg >= first.avg;
      const pct = first.avg > 0 ? Math.abs((last.avg - first.avg) / first.avg * 100).toFixed(0) : '0';
      parts.push(
        `单次耗时（平均延迟）${{g.xKey}}=${{first.x}} <span class="hl">${{first.avg.toFixed(1)}}ms</span>`
        + ` → ${{g.xKey}}=${{last.x}} <span class="hl">${{last.avg.toFixed(1)}}ms</span>`
        + `（并发上升延迟${{up ? '↑' : '↓'}}${{pct}}%）；`);
    }} else {{
      parts.push(`单次耗时（平均延迟）约 <span class="hl">${{bestLat.avg.toFixed(1)}}ms</span>；`);
    }}
    // Amortized throughput trend (only meaningful when it differs from latency).
    const perDiffers = rows.some(r => Math.abs(r.per - r.avg) > 0.5);
    if (perDiffers && rows.length > 1) {{
      const ratio = last.per > 0 ? (first.per / last.per) : 0;
      parts.push(
        `吞吐均摊 ${{g.xKey}}=${{first.x}} ${{first.per.toFixed(1)}}ms`
        + ` → ${{g.xKey}}=${{last.x}} ${{last.per.toFixed(1)}}ms`
        + (ratio > 1.1 ? `（吞吐提升约 ${{ratio.toFixed(1)}}x）` : '')
        + `，单机最优 <b>${{g.xKey}}=${{bestThr.x}} ${{bestThr.per.toFixed(1)}}ms/次</b>。`);
    }}
    const blocks = [`<div class="insight">${{parts.join('')}}</div>`];

    // Multi-environment comparison: at each concurrency, find the widest gap
    // between environments and name the slowest one. Because labels carry the
    // differing component version, this reads like "CubeMaster 0.5.1 慢 30%",
    // directly surfacing a version-induced regression.
    if (RUN_SERIES.length > 1) {{
      let worst = null;
      xValues.forEach(xv => {{
        const scen = `${{g.prefix}}-${{g.xKey}}${{xv}}`;
        const vals = RUN_SERIES.map(sr => {{
          const r = sr.perf[scen];
          return r ? {{ label: sr.label, v: (r.avg_ms || r.per_ms || 0) }} : null;
        }}).filter(v => v && v.v > 0);
        if (vals.length < 2) return;
        let fast = vals[0], slow = vals[0];
        vals.forEach(x => {{ if (x.v < fast.v) fast = x; if (x.v > slow.v) slow = x; }});
        const pct = fast.v > 0 ? (slow.v / fast.v - 1) * 100 : 0;
        if (!worst || pct > worst.pct) worst = {{ xv, fast, slow, pct }};
      }});
      if (worst && worst.pct >= 5) {{
        blocks.push(
          `<div class="insight"><b>环境对比</b>（${{g.xKey}}=${{worst.xv}}）：`
          + `<span class="hl">${{worst.slow.label}}</span> 平均 ${{worst.slow.v.toFixed(1)}}ms，`
          + `比 <b>${{worst.fast.label}}</b>（${{worst.fast.v.toFixed(1)}}ms）`
          + `<span class="hl">慢 ${{worst.pct.toFixed(0)}}%</span>`
          + `，疑似组件版本/机器差异导致的性能劣化。</div>`);
      }} else if (worst) {{
        blocks.push(
          `<div class="insight"><b>环境对比</b>：各环境该场景性能接近`
          + `（最大差异 ${{worst.pct.toFixed(0)}}%），未见明显版本相关劣化。</div>`);
      }}
    }}
    return blocks.join('');
  }}

  SCENARIO_GROUPS.forEach(g => {{
    let xValues = discoverXValues(g.prefix, g.xKey);
    if (xValues.length === 0) xValues = g.fallback;
    const xLabels = xValues.map(v => g.xKey + '=' + v);
    const {{ runLines, baseLines, scatters }} = collectLineData(g.prefix, g.xKey, xValues);

    const hasAnyData = runLines.some(l => l.data.some(p => p.y !== null))
      || baseLines.length > 0;

    // Summary table: one row per (concurrency, environment). Columns are
    // driven by METRICS (env-var customizable). The special "vs" column
    // renders a "vs first env" badge so machines are easy to rank.
    const tbl = document.createElement('table');
    tbl.className = 'data-table';
    let tableHtml = '<thead><tr>';
    METRICS.forEach(([k, label]) => {{ tableHtml += `<th>${{label}}</th>`; }});
    tableHtml += '</tr></thead><tbody>';
    let rowCount = 0;
    xValues.forEach(xv => {{
      const scenario = `${{g.prefix}}-${{g.xKey}}${{xv}}`;
      const refSeries = RUN_SERIES[0];
      const refPer = refSeries ? runValueAt(refSeries, scenario) : null;
      RUN_SERIES.forEach((sr, i) => {{
        const r = sr.perf[scenario];
        if (!r) return;
        rowCount++;
        const perMs = r.per_ms || r.avg_ms || 0;
        const vs = (i === 0)
          ? '<span class="badge badge-na">基准</span>'
          : cmpBadge(perMs, refPer);
        tableHtml += '<tr>';
        METRICS.forEach(([k]) => {{
          let cell;
          if (k === 'scenario') cell = scenario;
          else if (k === 'env') cell = `<span style="color:${{runColor(i)}};">■</span> ${{sr.label}}`;
          else if (k === 'vs') cell = vs;
          else if (k === 'per_ms') cell = fmtMs(perMs);
          else if (k === 'count' || k === 'concurrency' || k === 'runs') {{
            const v = r[k];
            cell = (v === undefined || v === null || v === '') ? '-' : v;
          }} else {{
            cell = fmtMs(r[k]);
          }}
          tableHtml += `<td>${{cell}}</td>`;
        }});
        tableHtml += '</tr>';
      }});
    }});
    tableHtml += '</tbody>';
    tbl.innerHTML = tableHtml;

    if (!hasAnyData && rowCount === 0) return;

    const block = document.createElement('div');
    block.className = 'scenario-block';

    const h3 = document.createElement('h3');
    h3.textContent = g.title;
    block.appendChild(h3);

    // Key takeaway first (explains which metric is the real cost, and — in a
    // multi-env run — which environment/version is slower).
    const insightHtml = scenarioInsight(g, xValues);
    if (insightHtml) {{
      const tmp = document.createElement('div');
      tmp.innerHTML = insightHtml;
      while (tmp.firstChild) block.appendChild(tmp.firstChild);
    }}

    // Chart before table.
    if (hasAnyData) {{
      block.appendChild(renderChart(g.id, xLabels, runLines, baseLines, scatters));
    }}
    block.appendChild(tbl);
    container.appendChild(block);
  }});
}})();
</script>
</body>
</html>"""


# Cube component version fields, ordered for display and diff detection.
# `release_version` is deliberately first: when the host has a release
# manifest, it uniquely identifies the whole install and makes the
# fingerprint diff-friendly ("v1.0.0 → v1.0.1") without depending on
# individual component versions matching perfectly.
_VERSION_FIELDS: list[tuple[str, str]] = [
    ("release_version", "Release"),
    ("cubemaster_version", "CubeMaster"),
    ("cubeapi_version", "CubeAPI"),
    ("cubelet_version", "Cubelet"),
    ("cube_shim_version", "CubeShim"),
    ("cube_runtime_version", "CubeRuntime"),
    ("guest_image_version", "GuestImage"),
    ("guest_agent_version", "GuestAgent"),
    ("kernel_version_node", "NodeKernel"),
]


def _env_fingerprint(env: dict[str, Any]) -> str:
    """Stable identity of an environment used to group input files.

    Component versions are part of the fingerprint so that the same machine
    running a different CubeMaster/CubeAPI/... build becomes a *separate*
    comparison series instead of being averaged together — otherwise a
    version-induced regression would be hidden.
    """
    keys = ("hostname", "cpu_model", "arch", "kernel")
    parts = [str(env.get(k, "")) for k in keys]
    parts += [str(env.get(k, "")) for k, _ in _VERSION_FIELDS]
    return "|".join(parts)


def _env_label(env: dict[str, Any]) -> str:
    """Short human label for a series/legend/table column."""
    host = env.get("hostname") or ""
    arch = env.get("arch") or ""
    if host and arch:
        return f"{host} ({arch})"
    return host or env.get("cpu_model") or "run"


def _disambiguate_labels(
    run_series: list[dict[str, Any]], run_envs: list[dict[str, Any]]
) -> None:
    """Append differing component versions to labels for multi-env reports.

    Only version fields that actually differ across environments are added,
    so a legend entry reads e.g. "VM-A (x86_64) · CubeMaster 0.5.1" — making
    it obvious which build each series belongs to.
    """
    if len(run_envs) < 2:
        return
    diff_fields = [
        (k, name)
        for k, name in _VERSION_FIELDS
        if len({(e["env"].get(k) or "") for e in run_envs}) > 1
    ]
    if not diff_fields:
        return
    for series, env_entry in zip(run_series, run_envs):
        tags = [f"{name} {env_entry['env'].get(k) or '-'}" for k, name in diff_fields]
        suffix = " · " + " / ".join(tags)
        series["label"] += suffix
        env_entry["label"] += suffix


def _agg_scenario(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate repeated samples of the same scenario within one environment."""
    n = len(samples)

    def mean(key: str) -> float:
        return round(sum(s.get(key, 0) for s in samples) / n, 2)

    raw: list[float] = []
    for s in samples:
        raw.extend(s.get("raw_latencies", []) or [])

    return {
        "count": sum(s.get("count", 0) for s in samples),
        "concurrency": samples[0].get("concurrency", 0),
        "avg_ms": mean("avg_ms"),
        "min_ms": round(min(s.get("min_ms", float("inf")) for s in samples), 2),
        "p50_ms": mean("p50_ms"),
        "p95_ms": mean("p95_ms"),
        "p99_ms": mean("p99_ms"),
        "max_ms": round(max(s.get("max_ms", 0) for s in samples), 2),
        "wall_ms": mean("wall_ms"),
        "per_ms": mean("per_ms"),
        "raw_latencies": raw,
        "runs": n,
    }


def _group_runs(
    data_files: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group input files by environment fingerprint into comparison series.

    Returns (run_series, run_envs):
    - run_series: [{"label", "perf": {scenario: aggregated_stats}}]
    - run_envs:   [{"label", "env": {...}, "files": [...]}]
    Files sharing a fingerprint are averaged; distinct fingerprints become
    separate series (multi-environment comparison).
    """
    groups: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for path in data_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"Warning: skipping {path}: {exc}")
            continue
        env = data.get("environment", {})
        fp = _env_fingerprint(env)
        g = groups.get(fp)
        if g is None:
            g = {"env": env, "samples": {}, "files": []}
            groups[fp] = g
        g["env"] = env  # keep the latest env for this fingerprint
        g["files"].append(os.path.basename(path))
        for p in data.get("perf", []):
            g["samples"].setdefault(p["scenario"], []).append(p)

    run_series: list[dict[str, Any]] = []
    run_envs: list[dict[str, Any]] = []
    for g in groups.values():
        label = _env_label(g["env"])
        perf = {scen: _agg_scenario(samples) for scen, samples in g["samples"].items()}
        run_series.append({"label": label, "perf": perf})
        run_envs.append({"label": label, "env": g["env"], "files": g["files"]})
    _disambiguate_labels(run_series, run_envs)
    return run_series, run_envs


def generate_html(
    data_files: list[str],
    output_path: str = "perf_report.html",
    title: str = "CubeSandbox 性能基准测试报告",
) -> str:
    run_series, run_envs = _group_runs(data_files)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    baseline_keys = list(ALL_BASELINES.keys())

    scenario_names: set[str] = set()
    for sr in run_series:
        scenario_names.update(sr["perf"].keys())

    # Field lists are env-var customizable (see module docstring near
    # _DEFAULT_ENV_FIELDS), resolved here and injected into the template.
    env_fields = _resolve_fields(
        "CUBE_REPORT_ENV_FIELDS", "CUBE_REPORT_ENV_EXTRA", _DEFAULT_ENV_FIELDS
    )
    cube_fields = _resolve_fields(
        "CUBE_REPORT_CUBE_FIELDS", "CUBE_REPORT_CUBE_EXTRA", _DEFAULT_CUBE_FIELDS
    )
    # Scenario groups and metric columns are likewise env-var customizable.
    scenarios = _resolve_scenarios()
    metrics = _resolve_fields(
        "CUBE_REPORT_METRICS", "CUBE_REPORT_METRICS_EXTRA", _DEFAULT_METRICS
    )

    html = _HTML.format(
        title=title,
        generated_at=generated_at,
        env_count=len(run_envs),
        baseline_count=len(baseline_keys),
        scenario_count=len(scenario_names),
        run_series_json=json.dumps(run_series, ensure_ascii=False),
        run_envs_json=json.dumps(run_envs, ensure_ascii=False),
        run_colors_json=json.dumps(_RUN_COLORS, ensure_ascii=False),
        all_baselines_json=json.dumps(ALL_BASELINES, ensure_ascii=False),
        baseline_keys_json=json.dumps(baseline_keys, ensure_ascii=False),
        baseline_colors_json=json.dumps(_BASELINE_COLORS, ensure_ascii=False),
        env_fields_json=json.dumps(env_fields, ensure_ascii=False),
        cube_fields_json=json.dumps(cube_fields, ensure_ascii=False),
        scenarios_json=json.dumps(scenarios, ensure_ascii=False),
        metrics_json=json.dumps(metrics, ensure_ascii=False),
    )

    out = Path(output_path).resolve()
    out.write_text(html, encoding="utf-8")
    print(f"\n📄 HTML report written to: {out}")
    return str(out)
