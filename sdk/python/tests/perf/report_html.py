# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""HTML performance report generator.

Produces a self-contained, interactive HTML page with:
- Environment overview (current run + all baseline comparison tabs)
- Multi-baseline comparison (dynamic — supports any number of baselines from ALL_BASELINES)
- Per-scenario tables with comparison badges against each baseline
- Bar charts with one bar per baseline + current run
- Multi-run data merge (multiple JSON files from different machines)
- Zero external dependencies — single self-contained HTML file
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baseline import ALL_BASELINES

# Baseline color palette — cycles for up to 8 baselines
_BASELINE_COLORS = [
    "#c4b5fd",  # purple (BMI5)
    "#86efac",  # green (Vera)
    "#fde68a",  # yellow (BMSA9)
    "#fca5a5",  # red (Kunpeng)
    "#93c5fd",  # blue
    "#d8b4fe",  # violet
    "#a5f3fc",  # cyan
    "#fed7aa",  # orange
]


def _build_env_tabs_html(baseline_keys: list[str], run_count: int) -> str:
    """Build dynamic environment tab buttons and content divs."""
    tabs_html = '<button class="tab active" data-tab="env-current">Current Run</button>\n'
    content_html = """  <div class="tab-content active" id="env-current">
    <div class="env-grid" id="env-current-grid"></div>
  </div>
"""
    for i, key in enumerate(baseline_keys):
        safe_id = f"env-bl-{i}"
        tabs_html += f'    <button class="tab" data-tab="{safe_id}">{key} Baseline</button>\n'
        content_html += f"""  <div class="tab-content" id="{safe_id}">
    <div class="env-grid" id="{safe_id}-grid"></div>
  </div>
"""
    tabs_html += f'    <button class="tab" data-tab="env-runs">All Runs ({run_count})</button>\n'
    content_html += """  <div class="tab-content" id="env-runs">
    <div class="run-summary" id="run-cards"></div>
  </div>
"""
    return tabs_html, content_html


def _build_baseline_col_headers(baseline_keys: list[str]) -> str:
    """Build <th> elements for each baseline comparison column."""
    parts = []
    for key in baseline_keys:
        short = key.split("(")[0].strip().replace(" ", "-")
        parts.append(f"<th>vs {short}</th>")
    return "".join(parts)


def _build_legend_html(baseline_keys: list[str]) -> str:
    """Build legend HTML for chart section."""
    items = ['<div class="legend-item"><div class="legend-swatch" style="background:linear-gradient(90deg,#667eea,#764ba2);"></div> Current Run</div>']
    for i, key in enumerate(baseline_keys):
        color = _BASELINE_COLORS[i % len(_BASELINE_COLORS)]
        items.append(f'<div class="legend-item"><div class="legend-swatch" style="background:{color};"></div> {key}</div>')
    return "\n    ".join(items)


_HTML_TEMPLATE_HEAD = """<!DOCTYPE html>
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
.container {{ max-width: 1300px; margin: 0 auto; padding: 24px; }}
.section {{ background: white; border-radius: 12px; padding: 24px; margin: 20px 0; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
.section h2 {{ font-size: 20px; color: #667eea; margin-bottom: 16px; border-bottom: 2px solid #e8ecf1; padding-bottom: 8px; display: flex; align-items: center; gap: 8px; }}
.section h3 {{ font-size: 16px; color: #555; margin: 16px 0 8px; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e8ecf1; }}
th {{ background: #f0f2f5; font-weight: 600; color: #555; white-space: nowrap; }}
tr:hover {{ background: #fafbfc; }}
.metric-good {{ color: #22c55e; font-weight: 600; }}
.metric-warn {{ color: #f59e0b; font-weight: 600; }}
.metric-bad {{ color: #ef4444; font-weight: 600; }}
.metric-na {{ color: #aaa; font-style: italic; }}
.bar-container {{ display: flex; align-items: center; gap: 8px; margin: 6px 0; }}
.bar-label {{ min-width: 160px; font-size: 12px; text-align: right; color: #666; }}
.bar-track {{ flex: 1; height: 22px; background: #e8ecf1; border-radius: 4px; overflow: hidden; position: relative; }}
.bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.5s ease; min-width: 2px; }}
.bar-fill.current {{ background: linear-gradient(90deg, #667eea, #764ba2); }}
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
.legend {{ display: flex; gap: 16px; margin-bottom: 12px; font-size: 12px; flex-wrap: wrap; }}
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
  .bar-label {{ min-width: 100px; }}
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
{env_tabs}
  </div>
{env_content}
</div>

<!-- Perf Results -->
<div class="section">
  <h2>Performance Benchmarks <span style="font-size:12px;color:#888;font-weight:400;">(ms)</span></h2>
  <div class="table-wrap" id="perf-tables"></div>
</div>

<!-- Charts -->
<div class="section">
  <h2>Latency Comparison (Current vs Baselines)</h2>
  <div class="legend">
{legend}
  </div>
  <div id="charts"></div>
</div>

<footer>
  Generated by CubeSandbox Python SDK perf suite &nbsp;|&nbsp;
  Baselines: {baseline_labels}
</footer>
</div>

<script>
// ---- Data ----
const ALL_BASELINES = {all_baselines_json};
const BASELINE_KEYS = {baseline_keys_json};
const BASELINE_COLORS = {baseline_colors_json};
const RUNS = {runs_json};
const PERF = {perf_json};
const ENV = {env_json};

// ---- Helpers ----
function fmtMs(v) {{
  if (v === null || v === undefined || v === '') return '-';
  return Number(v).toFixed(1);
}}

function cmpBadge(current, baseline) {{
  if (!baseline || !current || current <= 0) return '<span class="badge badge-na">-</span>';
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
    {{ label: 'Baselines', value: BASELINE_KEYS.length }},
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

  // Current run env
  renderEnv('env-current-grid', ENV);

  // Each baseline env tab
  BASELINE_KEYS.forEach((key, i) => {{
    const bl = ALL_BASELINES[key];
    if (bl && bl.env) {{
      renderEnv(`env-bl-${{i}}-grid`, bl.env);
    }}
  }});

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
    // Build header with dynamic baseline columns
    let cmpHeaders = BASELINE_KEYS.map(k => {{
      const short = k.split('(')[0].trim().replace(/\\s+/g, '-');
      return `<th>vs ${{short}}</th>`;
    }}).join('');
    let html = `<div class="scenario-group"><h4>${{name}}</h4>`;
    html += `<table><thead><tr>
      <th>Scenario</th><th>Count</th><th>Conc</th>
      <th>Avg</th><th>Min</th><th>P50</th><th>P95</th><th>Max</th>
      <th>Wall</th><th>Per</th>
      ${{cmpHeaders}}
    </tr></thead><tbody>`;

    rows.forEach(r => {{
      const perMs = r.per_ms || r.avg_ms || 0;

      // Build baseline comparison cells
      let cmpCells = '';
      BASELINE_KEYS.forEach(key => {{
        const bl = ALL_BASELINES[key];
        if (!bl || !bl.perf) {{ cmpCells += '<td><span class="badge badge-na">-</span></td>'; return; }}
        const bb = bl.perf[r.scenario];
        const blPer = bb ? (bb.per || bb.avg || 0) : 0;
        cmpCells += `<td>${{cmpBadge(perMs, blPer)}}</td>`;
      }});

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
        ${{cmpCells}}
      </tr>`;
    }});

    html += '</tbody></table></div>';
    container.innerHTML += html;
  }});
}})();

// ---- Charts ----
(function() {{
  const container = document.getElementById('charts');

  PERF.forEach(r => {{
    const currentPer = r.per_ms || r.avg_ms || 0;
    // Collect all baseline values for this scenario
    let allValues = currentPer > 0 ? [currentPer] : [];
    let barsData = [];
    if (currentPer > 0) {{
      barsData.push({{ label: 'Current', value: currentPer, cssClass: 'current', color: '#667eea' }});
    }}
    BASELINE_KEYS.forEach((key, i) => {{
      const bl = ALL_BASELINES[key];
      if (!bl || !bl.perf) return;
      const bb = bl.perf[r.scenario];
      const blPer = bb ? (bb.per || bb.avg || 0) : 0;
      if (blPer > 0) {{
        allValues.push(blPer);
        barsData.push({{
          label: key,
          value: blPer,
          cssClass: `bl-${{i}}`,
          color: BASELINE_COLORS[i % BASELINE_COLORS.length],
        }});
      }}
    }});

    if (barsData.length === 0) return;

    const maxVal = Math.max(...allValues) * 1.25;
    const pct = (v) => maxVal > 0 ? (v / maxVal * 100).toFixed(1) : 0;

    let barsHtml = barsData.map(bd => {{
      const styleAttr = bd.cssClass === 'current'
        ? `style="width:${{pct(bd.value)}}%"`
        : `style="width:${{pct(bd.value)}}%;background:${{bd.color}};"`;
      return `<div class="bar-container">
        <span class="bar-label">${{bd.label}}</span>
        <div class="bar-track"><div class="bar-fill" ${{styleAttr}}></div></div>
        <span class="bar-value" style="color:${{bd.color}};">${{bd.value.toFixed(1)}} ms</span>
      </div>`;
    }}).join('\\n        ');

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
    - Environment overview (current run + all baseline comparison tabs)
    - Multi-baseline comparison (all entries from ALL_BASELINES)
    - Per-scenario tables with comparison badges against each baseline
    - Bar charts with one bar per baseline + current run
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
    baseline_keys = list(ALL_BASELINES.keys())
    baseline_labels = ", ".join(baseline_keys)

    env_tabs, env_content = _build_env_tabs_html(baseline_keys, len(runs))
    legend = _build_legend_html(baseline_keys)

    html = _HTML_TEMPLATE_HEAD.format(
        title=title,
        generated_at=generated_at,
        run_count=len(runs),
        scenario_count=len(merged_perf),
        baseline_labels=baseline_labels,
        env_tabs=env_tabs,
        env_content=env_content,
        legend=legend,
        all_baselines_json=json.dumps(ALL_BASELINES, ensure_ascii=False),
        baseline_keys_json=json.dumps(baseline_keys, ensure_ascii=False),
        baseline_colors_json=json.dumps(_BASELINE_COLORS, ensure_ascii=False),
        runs_json=json.dumps(runs, ensure_ascii=False),
        perf_json=json.dumps(merged_perf, ensure_ascii=False),
        env_json=json.dumps(merged_env, ensure_ascii=False),
    )

    out = Path(output_path).resolve()
    out.write_text(html, encoding="utf-8")
    print(f"\n📄 HTML report written to: {out}")
    return str(out)
