# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""HTML performance report generator.

Produces a self-contained, interactive HTML page that:
- Renders environment info, multi-baseline comparison (BMI5 x86_64 + Vera ARM64),
  and benchmark results
- Supports multi-run data merge (multiple JSON files from different machines)
- Visualizes per-scenario latency with tables and bar charts (pure HTML/CSS/JS)
- Allows easy diffing for performance regression detection
- Zero external dependencies — single self-contained HTML file
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baseline import ALL_BASELINES, BASELINE_SOURCE_DATE

# Template constants
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f0f2f5; color: #1a1a2e; line-height: 1.6; }}
.header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 40px 24px; text-align: center; }}
.header h1 {{ font-size: 28px; margin-bottom: 8px; }}
.header .subtitle {{ opacity: 0.85; font-size: 14px; line-height: 1.8; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
.section {{ background: white; border-radius: 12px; padding: 24px; margin: 20px 0; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
.section h2 {{ font-size: 20px; color: #667eea; margin-bottom: 16px; border-bottom: 2px solid #e8ecf1; padding-bottom: 8px; display: flex; align-items: center; gap: 8px; }}
.section h3 {{ font-size: 16px; color: #555; margin: 16px 0 8px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e8ecf1; }}
th {{ background: #f0f2f5; font-weight: 600; color: #555; white-space: nowrap; position: sticky; top: 0; z-index: 1; }}
tr:hover {{ background: #fafbfc; }}
.metric-good {{ color: #22c55e; font-weight: 600; }}
.metric-warn {{ color: #f59e0b; font-weight: 600; }}
.metric-bad {{ color: #ef4444; font-weight: 600; }}
.metric-na {{ color: #aaa; font-style: italic; }}
.bar-container {{ display: flex; align-items: center; gap: 8px; margin: 6px 0; }}
.bar-label {{ min-width: 180px; font-size: 12px; text-align: right; color: #666; }}
.bar-track {{ flex: 1; height: 22px; background: #e8ecf1; border-radius: 4px; overflow: hidden; position: relative; }}
.bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.5s ease; min-width: 2px; }}
.bar-fill.current {{ background: linear-gradient(90deg, #667eea, #764ba2); }}
.bar-fill.baseline-bmi5 {{ background: #c4b5fd; }}
.bar-fill.baseline-vera {{ background: #86efac; }}
.bar-value {{ min-width: 85px; font-size: 12px; font-weight: 600; text-align: right; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }}
.badge-good {{ background: #dcfce7; color: #16a34a; }}
.badge-warn {{ background: #fef3c7; color: #d97706; }}
.badge-bad {{ background: #fee2e2; color: #dc2626; }}
.badge-na {{ background: #f0f0f0; color: #999; }}
.tabs {{ display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 2px solid #e8ecf1; flex-wrap: wrap; }}
.tab {{ padding: 8px 16px; cursor: pointer; border: none; background: none; font-size: 14px; color: #888; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; white-space: nowrap; }}
.tab:hover {{ color: #667eea; }}
.tab.active {{ color: #667eea; border-bottom-color: #667eea; font-weight: 600; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.run-summary {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; margin-bottom: 16px; }}
.run-card {{ background: #f8f9fb; border: 1px solid #e8ecf1; border-radius: 8px; padding: 14px; }}
.run-card h4 {{ font-size: 14px; color: #667eea; margin-bottom: 6px; }}
.run-card .meta {{ font-size: 12px; color: #888; margin: 2px 0; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }}
.summary-card {{ background: #f8f9fb; border-radius: 8px; padding: 16px; text-align: center; }}
.summary-card .value {{ font-size: 24px; font-weight: 700; color: #667eea; }}
.summary-card .label {{ font-size: 12px; color: #888; margin-top: 4px; }}
footer {{ text-align: center; padding: 24px; color: #aaa; font-size: 12px; }}
footer a {{ color: #667eea; }}
.env-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 6px; }}
.env-item {{ display: flex; padding: 5px 0; border-bottom: 1px solid #f0f0f0; }}
.env-key {{ min-width: 150px; font-weight: 600; color: #666; font-size: 13px; }}
.env-val {{ color: #333; font-size: 13px; word-break: break-all; }}
.chart-section {{ margin: 16px 0; }}
.chart-title {{ font-size: 14px; font-weight: 600; margin-bottom: 10px; color: #555; }}
.legend {{ display: flex; gap: 16px; margin-bottom: 12px; font-size: 12px; }}
.legend-item {{ display: flex; align-items: center; gap: 4px; }}
.legend-swatch {{ width: 14px; height: 14px; border-radius: 3px; }}
.scenario-group {{ margin: 20px 0; }}
.scenario-group h4 {{ font-size: 15px; color: #444; margin-bottom: 8px; padding: 6px 10px; background: #f8f9fb; border-radius: 6px; border-left: 3px solid #667eea; }}
@media (max-width: 768px) {{
  .container {{ padding: 12px; }}
  .header {{ padding: 24px 12px; }}
  .header h1 {{ font-size: 20px; }}
  table {{ font-size: 11px; }}
  th, td {{ padding: 6px 8px; }}
  .bar-label {{ min-width: 120px; }}
  .run-summary {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="header">
  <h1>CubeSandbox Performance Benchmark Report</h1>
  <div class="subtitle">
    Generated: {generated_at}<br>
    Runs: {run_count} &nbsp;|&nbsp; Scenarios: {scenario_count} &nbsp;|&nbsp;
    Baselines: {baseline_labels}
  </div>
</div>

<div class="container">

<!-- Overview -->
<div class="section">
  <h2>Overview</h2>
  <div class="summary-grid" id="summary-grid"></div>
</div>

<!-- Environment -->
<div class="section">
  <h2>Environment</h2>
  <div class="tabs" id="env-tabs">
    <button class="tab active" data-tab="env-current">Current Run</button>
    <button class="tab" data-tab="env-bmi5">BMI5 (x86_64) Baseline</button>
    <button class="tab" data-tab="env-vera">Vera A1P (ARM64) Baseline</button>
    <button class="tab" data-tab="env-runs">All Runs ({run_count})</button>
  </div>
  <div class="tab-content active" id="env-current">
    <div class="env-grid" id="env-current-grid"></div>
  </div>
  <div class="tab-content" id="env-bmi5">
    <div class="env-grid" id="env-bmi5-grid"></div>
  </div>
  <div class="tab-content" id="env-vera">
    <div class="env-grid" id="env-vera-grid"></div>
  </div>
  <div class="tab-content" id="env-runs">
    <div class="run-summary" id="run-cards"></div>
  </div>
</div>

<!-- Perf Results -->
<div class="section">
  <h2>Performance Benchmarks <span style="font-size:12px;color:#888;font-weight:400;">(ms)</span></h2>
  <div id="perf-tables"></div>
</div>

<!-- Charts -->
<div class="section">
  <h2>Latency Comparison (Current vs Baselines)</h2>
  <div class="legend">
    <div class="legend-item"><div class="legend-swatch" style="background:linear-gradient(90deg,#667eea,#764ba2);"></div> Current Run</div>
    <div class="legend-item"><div class="legend-swatch" style="background:#c4b5fd;"></div> BMI5 (x86_64)</div>
    <div class="legend-item"><div class="legend-swatch" style="background:#86efac;"></div> Vera A1P (ARM64)</div>
  </div>
  <div id="charts"></div>
</div>

<footer>
  Generated by CubeSandbox Python SDK perf suite &nbsp;|&nbsp;
  Baseline sources: <a href="https://cubesandbox.com/zh/blog/posts/2026-06-01-cubesandbox-perf-benchmark.html" target="_blank">BMI5</a>,
  <a href="#">Vera A1P</a>
</footer>
</div>

<script>
// ---- Data ----
const ALL_BASELINES = {all_baselines_json};
const RUNS = {runs_json};
const PERF = {perf_json};
const ENV = {env_json};

// ---- Helpers ----
function fmtMs(v) {{
  if (v === null || v === undefined || v === '') return '-';
  return Number(v).toFixed(1);
}}

function cmpBadge(current, baseline) {{
  if (!baseline || !current) return '<span class="badge badge-na">-</span>';
  const ratio = current / baseline;
  if (ratio <= 1.05) return `<span class="badge badge-good">=${{(ratio*100).toFixed(0)}}%</span>`;
  if (ratio <= 1.20) return `<span class="badge badge-warn">+${{((ratio-1)*100).toFixed(0)}}%</span>`;
  return `<span class="badge badge-bad">+${{((ratio-1)*100).toFixed(0)}}%</span>`;
}}

// ---- Summary ----
(function() {{
  const grid = document.getElementById('summary-grid');
  const cards = [
    {{ label: 'Runs', value: RUNS.length }},
    {{ label: 'Scenarios', value: PERF.length }},
    {{ label: 'Total Samples', value: PERF.reduce((s, r) => s + (r.count || 0), 0) }},
    {{ label: 'Baselines', value: Object.keys(ALL_BASELINES).length }},
  ];
  cards.forEach(c => {{
    grid.innerHTML += `<div class="summary-card"><div class="value">${{c.value}}</div><div class="label">${{c.label}}</div></div>`;
  }});
}})();

// ---- Environment ----
(function() {{
  function renderEnv(gridId, envObj) {{
    const grid = document.getElementById(gridId);
    if (!grid) return;
    Object.entries(envObj).forEach(([k, v]) => {{
      if (v === null || v === undefined || v === '') return;
      const key = k.replace(/_/g, ' ');
      grid.innerHTML += `<div class="env-item"><span class="env-key">${{key}}</span><span class="env-val">${{v}}</span></div>`;
    }});
  }}

  renderEnv('env-current-grid', ENV);

  // BMI5 baseline env
  if (ALL_BASELINES['BMI5 (x86_64)']) {{
    renderEnv('env-bmi5-grid', ALL_BASELINES['BMI5 (x86_64)'].env);
  }}

  // Vera baseline env
  if (ALL_BASELINES['Vera A1P (ARM64)']) {{
    renderEnv('env-vera-grid', ALL_BASELINES['Vera A1P (ARM64)'].env);
  }}

  // All runs
  const cards = document.getElementById('run-cards');
  RUNS.forEach((run, i) => {{
    cards.innerHTML += `<div class="run-card">
      <h4>Run #${{i + 1}}: ${{run.file || 'N/A'}}</h4>
      <div class="meta">Host: ${{run.hostname || 'N/A'}}</div>
      <div class="meta">CPU: ${{run.cpu_model ? run.cpu_model.substring(0, 50) : 'N/A'}}</div>
      <div class="meta">Memory: ${{run.memory_total_gb || 'N/A'}} GiB &nbsp;|&nbsp; Arch: ${{run.arch || 'N/A'}}</div>
      <div class="meta">Time: ${{run.timestamp || 'N/A'}}</div>
    </div>`;
  }});
}})();

// ---- Tab switching ----
document.querySelectorAll('.tab').forEach(tab => {{
  tab.addEventListener('click', function() {{
    const tabGroup = this.parentElement;
    tabGroup.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    this.classList.add('active');
    const contentId = this.dataset.tab;
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById(contentId).classList.add('active');
  }});
}});

// ---- Perf Tables ----
(function() {{
  const container = document.getElementById('perf-tables');

  // Group by scenario family
  const groups = {{}};
  PERF.forEach(r => {{
    // Extract family: "template-create-c1" -> "template"
    const parts = r.scenario.split('-');
    let family = parts[0];
    if (parts.length > 1 && parts[1] === 'create') family = parts.slice(0, 2).join('-');
    if (!groups[family]) groups[family] = [];
    groups[family].push(r);
  }});

  const familyNames = {{
    'template': 'Template-Based Sandbox Creation',
    'template-create': 'Template-Based Sandbox Creation',
    'snapshot': 'Snapshot Creation',
    'snapshot-create': 'Create from Snapshot',
    'rollback': 'Rollback',
    'clone': 'Clone',
    'pause': 'Pause',
    'resume': 'Resume',
    'deployment': 'Deployment Density',
    'volume': 'Volume Operations',
  }};

  Object.entries(groups).forEach(([family, rows]) => {{
    const name = familyNames[family] || family;
    let html = `<div class="scenario-group"><h4>${{name}}</h4>`;
    html += `<table><thead><tr>
      <th>Scenario</th><th>Count</th><th>Conc</th>
      <th>Avg</th><th>Min</th><th>P50</th><th>P95</th><th>Max</th>
      <th>Wall</th><th>Per</th>
      <th>vs BMI5</th><th>vs Vera</th>
    </tr></thead><tbody>`;

    rows.forEach(r => {{
      const bmi5 = (ALL_BASELINES['BMI5 (x86_64)'] || {{}}).perf || {{}};
      const vera = (ALL_BASELINES['Vera A1P (ARM64)'] || {{}}).perf || {{}};
      const bmi5b = bmi5[r.scenario];
      const verab = vera[r.scenario];

      const perMs = r.per_ms || r.avg_ms || 0;
      const bmi5Per = bmi5b ? (bmi5b.per || bmi5b.avg || 0) : 0;
      const veraPer = verab ? (verab.per || verab.avg || 0) : 0;

      html += `<tr>
        <td><strong>${{r.scenario}}</strong></td>
        <td>${{r.count || '-'}}</td>
        <td>${{r.concurrency || '-'}}</td>
        <td>${{fmtMs(r.avg_ms)}}</td>
        <td>${{fmtMs(r.min_ms)}}</td>
        <td>${{fmtMs(r.p50_ms)}}</td>
        <td>${{fmtMs(r.p95_ms)}}</td>
        <td>${{fmtMs(r.max_ms)}}</td>
        <td>${{fmtMs(r.wall_ms)}}</td>
        <td>${{fmtMs(r.per_ms)}}</td>
        <td>${{cmpBadge(perMs, bmi5Per)}}</td>
        <td>${{cmpBadge(perMs, veraPer)}}</td>
      </tr>`;
    }});

    html += '</tbody></table></div>';
    container.innerHTML += html;
  }});
}})();

// ---- Charts ----
(function() {{
  const container = document.getElementById('charts');
  const bmi5Perf = (ALL_BASELINES['BMI5 (x86_64)'] || {{}}).perf || {{}};
  const veraPerf = (ALL_BASELINES['Vera A1P (ARM64)'] || {{}}).perf || {{}};

  PERF.forEach(r => {{
    const bmi5b = bmi5Perf[r.scenario];
    const verab = veraPerf[r.scenario];
    const currentPer = r.per_ms || r.avg_ms || 0;
    const bmi5Per = bmi5b ? (bmi5b.per || bmi5b.avg || 0) : 0;
    const veraPer = verab ? (verab.per || verab.avg || 0) : 0;

    if (!currentPer && !bmi5Per && !veraPer) return;

    const maxVal = Math.max(currentPer, bmi5Per, veraPer) * 1.25;
    const pct = (v) => maxVal > 0 ? (v / maxVal * 100).toFixed(1) : 0;

    let barsHtml = '';
    if (currentPer > 0) {{
      barsHtml += `<div class="bar-container">
        <span class="bar-label">Current</span>
        <div class="bar-track"><div class="bar-fill current" style="width:${{pct(currentPer)}}%"></div></div>
        <span class="bar-value" style="color:#667eea;">${{currentPer.toFixed(1)}} ms</span>
      </div>`;
    }}
    if (bmi5Per > 0) {{
      barsHtml += `<div class="bar-container">
        <span class="bar-label">BMI5 (x86_64)</span>
        <div class="bar-track"><div class="bar-fill baseline-bmi5" style="width:${{pct(bmi5Per)}}%"></div></div>
        <span class="bar-value" style="color:#8b5cf6;">${{bmi5Per.toFixed(1)}} ms</span>
      </div>`;
    }}
    if (veraPer > 0) {{
      barsHtml += `<div class="bar-container">
        <span class="bar-label">Vera A1P (ARM64)</span>
        <div class="bar-track"><div class="bar-fill baseline-vera" style="width:${{pct(veraPer)}}%"></div></div>
        <span class="bar-value" style="color:#22c55e;">${{veraPer.toFixed(1)}} ms</span>
      </div>`;
    }}

    container.innerHTML += `
      <div class="chart-section">
        <div class="chart-title">${{r.scenario}} — per-operation latency (ms)</div>
        ${{barsHtml}}
      </div>`;
  }});
}})();
</script>
</body>
</html>"""


def _scenario_sort_key(name: str) -> tuple[int, ...]:
    """Sort scenarios: template < snapshot < rollback < clone < pause < resume < density < volume."""
    order = {
        "template": 0, "template-create": 0,
        "snapshot": 1, "snapshot-create": 1,
        "rollback": 2, "clone": 3,
        "pause": 4, "resume": 5,
        "deployment": 6, "volume": 7,
    }
    for prefix, idx in order.items():
        if name.startswith(prefix):
            return (idx, name)
    return (99, name)


def _merge_runs(data_files: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Load and merge multiple run data files into a combined view.

    Returns (runs, merged_env, merged_perf).
    """
    runs: list[dict[str, Any]] = []
    all_perf: list[dict[str, Any]] = []

    for path in data_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"Warning: skipping {path}: {exc}")
            continue

        env = data.get("environment", {})
        run_info = {
            "file": os.path.basename(path),
            "hostname": env.get("hostname", ""),
            "cpu_model": env.get("cpu_model", ""),
            "arch": env.get("arch", ""),
            "memory_total_gb": env.get("memory_total_gb", ""),
            "os_name": env.get("os_name", ""),
            "kernel": env.get("kernel", ""),
            "sdk_version": env.get("sdk_version", ""),
            "cubeapi_version": env.get("cubeapi_version", ""),
            "timestamp": env.get("timestamp", ""),
        }
        runs.append(run_info)

        for p in data.get("perf", []):
            p["_run_file"] = os.path.basename(path)
            all_perf.append(p)

    # Merge perf: average across runs for same scenario
    merged: dict[str, dict[str, Any]] = {}
    for p in all_perf:
        key = p["scenario"]
        if key not in merged:
            merged[key] = {"scenario": key, "samples": []}
        merged[key]["samples"].append(p)

    merged_perf: list[dict[str, Any]] = []
    for key in sorted(merged, key=_scenario_sort_key):
        samples = merged[key]["samples"]
        if not samples:
            continue
        first = samples[0]
        count = sum(s.get("count", 0) for s in samples)
        entry: dict[str, Any] = {
            "scenario": key,
            "count": count,
            "concurrency": first.get("concurrency", 0),
            "avg_ms": round(sum(s.get("avg_ms", 0) for s in samples) / len(samples), 2),
            "min_ms": round(min(s.get("min_ms", float("inf")) for s in samples), 2),
            "p50_ms": round(sum(s.get("p50_ms", 0) for s in samples) / len(samples), 2),
            "p95_ms": round(sum(s.get("p95_ms", 0) for s in samples) / len(samples), 2),
            "max_ms": round(max(s.get("max_ms", 0) for s in samples), 2),
            "wall_ms": round(sum(s.get("wall_ms", 0) for s in samples) / len(samples), 2),
            "per_ms": round(sum(s.get("per_ms", 0) for s in samples) / len(samples), 2),
            "runs": len(samples),
        }
        merged_perf.append(entry)

    # Use the last run's environment as merged_env
    merged_env: dict[str, Any] = {}
    if runs:
        try:
            with open(data_files[-1], encoding="utf-8") as f:
                merged_env = json.load(f).get("environment", {})
        except Exception:
            pass

    return runs, merged_env, merged_perf


def generate_html(
    data_files: list[str],
    output_path: str = "perf_report.html",
    title: str = "CubeSandbox Performance Benchmark Report",
) -> str:
    """Generate an HTML performance report from one or more run data JSON files.

    The HTML report includes:
    - Environment overview (current run + baseline comparison tabs)
    - Multi-baseline comparison (BMI5 x86_64 + Vera A1P ARM64)
    - Per-scenario tables with baseline comparison badges
    - Bar charts for visual latency comparison
    - Multi-run merge support

    Args:
        data_files: Paths to JSON data files (produced by the perf suite).
        output_path: Where to write the HTML file.
        title: Page title.

    Returns:
        The absolute path to the generated HTML file.
    """
    runs, merged_env, merged_perf = _merge_runs(data_files)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    baseline_labels = ", ".join(ALL_BASELINES.keys())

    html = _HTML_TEMPLATE.format(
        title=title,
        generated_at=generated_at,
        run_count=len(runs),
        scenario_count=len(merged_perf),
        baseline_labels=baseline_labels,
        all_baselines_json=json.dumps(ALL_BASELINES, ensure_ascii=False),
        runs_json=json.dumps(runs, ensure_ascii=False),
        perf_json=json.dumps(merged_perf, ensure_ascii=False),
        env_json=json.dumps(merged_env, ensure_ascii=False),
    )

    out = Path(output_path).resolve()
    out.write_text(html, encoding="utf-8")
    print(f"\n📄 HTML report written to: {out}")
    return str(out)
