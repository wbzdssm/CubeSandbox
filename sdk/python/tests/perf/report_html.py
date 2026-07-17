# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""HTML performance report generator.

Produces a self-contained, interactive HTML page that:
- Renders environment info, baseline comparison, and benchmark results
- Supports multi-run data merge (multiple JSON files from different machines)
- Visualizes per-scenario latency with tables and simple bar charts (pure HTML/CSS/JS, no external deps)
- Allows easy diffing for performance regression detection
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baseline import (
    BASELINE_ENVIRONMENT,
    BASELINE_PERF,
    BASELINE_SOURCE,
    BASELINE_SOURCE_DATE,
)


def _render_html(
    *,
    title: str,
    generated_at: str,
    runs: list[dict[str, Any]],
    merged_env: dict[str, Any],
    merged_perf: list[dict[str, Any]],
) -> str:
    """Render the full self-contained HTML report page."""
    baseline_json = json.dumps(BASELINE_PERF, ensure_ascii=False)
    baseline_env_json = json.dumps(BASELINE_ENVIRONMENT, ensure_ascii=False)
    runs_json = json.dumps(runs, ensure_ascii=False)
    perf_json = json.dumps(merged_perf, ensure_ascii=False)
    env_json = json.dumps(merged_env, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f5f7fa; color: #1a1a2e; line-height: 1.6; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
.header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 40px 24px; text-align: center; }}
.header h1 {{ font-size: 28px; margin-bottom: 8px; }}
.header .subtitle {{ opacity: 0.85; font-size: 14px; }}
.section {{ background: white; border-radius: 12px; padding: 24px; margin: 20px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
.section h2 {{ font-size: 20px; color: #667eea; margin-bottom: 16px; border-bottom: 2px solid #e8ecf1; padding-bottom: 8px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e8ecf1; }}
th {{ background: #f0f2f5; font-weight: 600; color: #555; white-space: nowrap; }}
tr:hover {{ background: #fafbfc; }}
.metric-good {{ color: #22c55e; font-weight: 600; }}
.metric-warn {{ color: #f59e0b; font-weight: 600; }}
.metric-bad {{ color: #ef4444; font-weight: 600; }}
.bar-container {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; }}
.bar-label {{ min-width: 160px; font-size: 12px; text-align: right; }}
.bar-track {{ flex: 1; height: 20px; background: #e8ecf1; border-radius: 4px; overflow: hidden; position: relative; }}
.bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.4s ease; }}
.bar-fill.current {{ background: #667eea; }}
.bar-fill.baseline {{ background: #c4b5fd; }}
.bar-value {{ min-width: 70px; font-size: 12px; font-weight: 600; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }}
.badge-pass {{ background: #dcfce7; color: #16a34a; }}
.badge-fail {{ background: #fee2e2; color: #dc2626; }}
.tabs {{ display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 2px solid #e8ecf1; }}
.tab {{ padding: 8px 16px; cursor: pointer; border: none; background: none; font-size: 14px; color: #888; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; }}
.tab:hover {{ color: #667eea; }}
.tab.active {{ color: #667eea; border-bottom-color: #667eea; font-weight: 600; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.run-summary {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin-bottom: 16px; }}
.run-card {{ background: #f8f9fb; border: 1px solid #e8ecf1; border-radius: 8px; padding: 12px; }}
.run-card h4 {{ font-size: 14px; color: #667eea; margin-bottom: 4px; }}
.run-card .meta {{ font-size: 12px; color: #888; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }}
.summary-card {{ background: #f8f9fb; border-radius: 8px; padding: 16px; text-align: center; }}
.summary-card .value {{ font-size: 24px; font-weight: 700; color: #667eea; }}
.summary-card .label {{ font-size: 12px; color: #888; margin-top: 4px; }}
footer {{ text-align: center; padding: 24px; color: #aaa; font-size: 12px; }}
.env-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 8px; }}
.env-item {{ display: flex; padding: 6px 0; border-bottom: 1px solid #f0f0f0; }}
.env-key {{ min-width: 140px; font-weight: 600; color: #666; font-size: 13px; }}
.env-val {{ color: #333; font-size: 13px; word-break: break-all; }}
.chart-container {{ margin: 16px 0; }}
.chart-title {{ font-size: 14px; font-weight: 600; margin-bottom: 8px; color: #555; }}
@media (max-width: 768px) {{
  .container {{ padding: 12px; }}
  .header {{ padding: 24px 12px; }}
  .header h1 {{ font-size: 22px; }}
  table {{ font-size: 11px; }}
  th, td {{ padding: 6px 8px; }}
}}
</style>
</head>
<body>
<div class="header">
  <h1>🚀 CubeSandbox Performance Benchmark Report</h1>
  <div class="subtitle">Generated: {generated_at} | Runs: {len(runs)} | Baseline: {BASELINE_SOURCE_DATE}</div>
</div>

<div class="container">

<!-- Summary -->
<div class="section">
  <h2>📊 Overview</h2>
  <div class="summary-grid" id="summary-grid"></div>
</div>

<!-- Environment -->
<div class="section">
  <h2>🖥️ Environment</h2>
  <div class="tabs">
    <button class="tab active" onclick="switchTab('env-current')">Current Run</button>
    <button class="tab" onclick="switchTab('env-baseline')">Official Baseline</button>
    <button class="tab" onclick="switchTab('env-all')">All Runs</button>
  </div>
  <div class="tab-content active" id="env-current">
    <div class="env-grid" id="env-current-grid"></div>
  </div>
  <div class="tab-content" id="env-baseline">
    <div class="env-grid" id="env-baseline-grid"></div>
  </div>
  <div class="tab-content" id="env-all">
    <div class="run-summary" id="run-cards"></div>
  </div>
</div>

<!-- Perf Results -->
<div class="section">
  <h2>⚡ Performance Benchmarks <span style="font-size:12px;color:#888;font-weight:400;">(unit: ms)</span></h2>
  <div id="perf-tables"></div>
</div>

<!-- Charts -->
<div class="section">
  <h2>📈 Latency Comparison (Current vs Baseline)</h2>
  <div id="charts"></div>
</div>

<footer>
  Generated by CubeSandbox Python SDK perf suite | Baseline source: <a href="{BASELINE_SOURCE}" target="_blank">{BASELINE_SOURCE}</a>
</footer>
</div>

<script>
const BASELINE_PERF = {baseline_json};
const BASELINE_ENV = {baseline_env_json};
const RUNS = {runs_json};
const PERF = {perf_json};
const ENV = {env_json};

// ---- Summary ----
(function() {{
  const grid = document.getElementById('summary-grid');
  const scenarios = PERF.length;
  const totalSamples = PERF.reduce((s, r) => s + (r.count || 0), 0);
  const runs = RUNS.length;
  const cards = [
    {{ label: 'Runs', value: runs }},
    {{ label: 'Scenarios', value: scenarios }},
    {{ label: 'Total Samples', value: totalSamples }},
    {{ label: 'Baseline Date', value: '{BASELINE_SOURCE_DATE}' }},
  ];
  cards.forEach(c => {{
    grid.innerHTML += `<div class="summary-card"><div class="value">${{c.value}}</div><div class="label">${{c.label}}</div></div>`;
  }});
}})();

// ---- Environment ----
(function() {{
  // Current run env
  const grid = document.getElementById('env-current-grid');
  Object.entries(ENV).forEach(([k, v]) => {{
    if (v === null || v === undefined || v === '') return;
    grid.innerHTML += `<div class="env-item"><span class="env-key">${{k}}</span><span class="env-val">${{v}}</span></div>`;
  }});

  // Baseline env
  const bgrid = document.getElementById('env-baseline-grid');
  Object.entries(BASELINE_ENV).forEach(([k, v]) => {{
    bgrid.innerHTML += `<div class="env-item"><span class="env-key">${{k}}</span><span class="env-val">${{v}}</span></div>`;
  }});

  // All runs
  const cards = document.getElementById('run-cards');
  RUNS.forEach((run, i) => {{
    cards.innerHTML += `<div class="run-card">
      <h4>Run #${{i + 1}}</h4>
      <div class="meta">Host: ${{run.hostname || 'N/A'}}</div>
      <div class="meta">CPU: ${{run.cpu_model || 'N/A'}}</div>
      <div class="meta">Memory: ${{run.memory_total_gb || 'N/A'}} GiB</div>
      <div class="meta">Time: ${{run.timestamp || 'N/A'}}</div>
    </div>`;
  }});
}})();

// ---- Tab switching ----
function switchTab(tabId) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector(`[onclick="switchTab('${{tabId}}')"]`).classList.add('active');
  document.getElementById(tabId).classList.add('active');
}}

// ---- Perf Tables ----
(function() {{
  const container = document.getElementById('perf-tables');

  // Group by scenario family
  const groups = {{}};
  PERF.forEach(r => {{
    const family = r.scenario.split('-c')[0].split('-n')[0];
    if (!groups[family]) groups[family] = [];
    groups[family].push(r);
  }});

  const familyNames = {{
    'template': 'Template-Based Sandbox Creation',
    'snapshot': 'Snapshot Creation',
    'rollback': 'Rollback',
    'clone': 'Clone',
    'pause': 'Pause',
    'resume': 'Resume',
    'deployment': 'Deployment Density',
    'volume': 'Volume Operations',
  }};

  Object.entries(groups).forEach(([family, rows]) => {{
    const name = familyNames[family] || family;
    let html = `<h3 style="margin:16px 0 8px;color:#555;">${{name}}</h3>`;
    html += `<table><thead><tr>
      <th>Scenario</th><th>Count</th><th>Concurrency</th>
      <th>Avg</th><th>Min</th><th>P50</th><th>P95</th><th>Max</th>
      <th>Wall</th><th>Per</th><th>vs Baseline</th>
    </tr></thead><tbody>`;

    rows.forEach(r => {{
      const baseline = BASELINE_PERF[r.scenario];
      let cmp = '';
      if (baseline && baseline.per !== undefined && r.per_ms) {{
        const ratio = r.per_ms / baseline.per;
        if (ratio < 1.05) cmp = `<span class="badge badge-pass">~${{(ratio*100).toFixed(0)}}%</span>`;
        else if (ratio < 1.20) cmp = `<span class="badge" style="background:#fef3c7;color:#d97706;">+${{((ratio-1)*100).toFixed(0)}}%</span>`;
        else cmp = `<span class="badge badge-fail">+${{((ratio-1)*100).toFixed(0)}}%</span>`;
      }}

      html += `<tr>
        <td>${{r.scenario}}</td>
        <td>${{r.count || '-'}}</td>
        <td>${{r.concurrency || '-'}}</td>
        <td>${{(r.avg_ms || 0).toFixed(1)}}</td>
        <td>${{(r.min_ms || 0).toFixed(1)}}</td>
        <td>${{(r.p50_ms || 0).toFixed(1)}}</td>
        <td>${{(r.p95_ms || 0).toFixed(1)}}</td>
        <td>${{(r.max_ms || 0).toFixed(1)}}</td>
        <td>${{(r.wall_ms || 0).toFixed(0)}}</td>
        <td>${{(r.per_ms || 0).toFixed(1)}}</td>
        <td>${{cmp}}</td>
      </tr>`;
    }});

    html += '</tbody></table>';
    container.innerHTML += html;
  }});
}})();

// ---- Charts ----
(function() {{
  const container = document.getElementById('charts');

  PERF.forEach(r => {{
    const baseline = BASELINE_PERF[r.scenario];
    const currentPer = r.per_ms || r.avg_ms || 0;
    const baselinePer = baseline ? (baseline.per || baseline.avg || 0) : 0;
    if (!currentPer && !baselinePer) return;

    const maxVal = Math.max(currentPer, baselinePer) * 1.2;
    const currentPct = maxVal > 0 ? (currentPer / maxVal * 100) : 0;
    const baselinePct = maxVal > 0 ? (baselinePer / maxVal * 100) : 0;

    let cmpClass = 'metric-good';
    if (baselinePer > 0 && currentPer > baselinePer * 1.2) cmpClass = 'metric-bad';
    else if (baselinePer > 0 && currentPer > baselinePer * 1.05) cmpClass = 'metric-warn';

    container.innerHTML += `
      <div class="chart-container">
        <div class="chart-title">${{r.scenario}} (per-operation, ms)</div>
        <div class="bar-container">
          <span class="bar-label">Current</span>
          <div class="bar-track"><div class="bar-fill current" style="width:${{currentPct.toFixed(1)}}%"></div></div>
          <span class="bar-value ${{cmpClass}}">${{currentPer.toFixed(1)}} ms</span>
        </div>
        ${{baselinePer > 0 ? `
        <div class="bar-container">
          <span class="bar-label">Baseline (official)</span>
          <div class="bar-track"><div class="bar-fill baseline" style="width:${{baselinePct.toFixed(1)}}%"></div></div>
          <span class="bar-value" style="color:#888;">${{baselinePer.toFixed(1)}} ms</span>
        </div>` : ''}}
      </div>`;
  }});
}})();
</script>
</body>
</html>"""


def _scenario_sort_key(name: str) -> tuple[int, ...]:
    """Sort scenarios: template < snapshot < rollback < clone < pause < resume < density < volume."""
    order = {
        "template": 0, "snapshot": 1, "rollback": 2,
        "clone": 3, "pause": 4, "resume": 5,
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

        run_info = {
            "file": os.path.basename(path),
            "hostname": data.get("environment", {}).get("hostname", ""),
            "cpu_model": data.get("environment", {}).get("cpu_model", ""),
            "memory_total_gb": data.get("environment", {}).get("memory_total_gb", ""),
            "timestamp": data.get("environment", {}).get("timestamp", ""),
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

    Args:
        data_files: Paths to JSON data files (produced by the perf suite).
        output_path: Where to write the HTML file.
        title: Page title.

    Returns:
        The absolute path to the generated HTML file.
    """
    runs, merged_env, merged_perf = _merge_runs(data_files)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    html = _render_html(
        title=title,
        generated_at=generated_at,
        runs=runs,
        merged_env=merged_env,
        merged_perf=merged_perf,
    )

    out = Path(output_path).resolve()
    out.write_text(html, encoding="utf-8")
    print(f"\n📄 HTML report written to: {out}")
    return str(out)
