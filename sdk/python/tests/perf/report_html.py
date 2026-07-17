# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""HTML performance report generator — simplified single-page layout.

Each benchmark scenario gets:
- One line chart (current run vs all baselines)
- One summary table below the chart
Environment info is rendered as two simple tables (Hardware + CubeSandbox).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baseline import ALL_BASELINES

_BASELINE_COLORS = [
    "#c4b5fd", "#86efac", "#fde68a", "#fca5a5",
    "#93c5fd", "#d8b4fe", "#a5f3fc", "#fed7aa",
]

_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f0f2f5; color: #1a1a2e; line-height: 1.6; }}
.header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 32px 24px; text-align: center; }}
.header h1 {{ font-size: 24px; margin-bottom: 6px; }}
.header .subtitle {{ opacity: 0.85; font-size: 13px; }}
.container {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
.section {{ background: white; border-radius: 10px; padding: 20px; margin: 16px 0; box-shadow: 0 1px 6px rgba(0,0,0,0.05); }}
.section h2 {{ font-size: 18px; color: #667eea; margin-bottom: 12px; border-bottom: 2px solid #e8ecf1; padding-bottom: 6px; }}
.env-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.env-table td {{ padding: 6px 10px; border-bottom: 1px solid #f0f0f0; }}
.env-table td:first-child {{ font-weight: 600; color: #555; width: 140px; }}
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
.legend-swatch {{ width: 12px; height: 12px; border-radius: 3px; }}
.scenario-block {{ margin: 24px 0; }}
.scenario-block h3 {{ font-size: 16px; color: #444; margin-bottom: 8px; }}
footer {{ text-align: center; padding: 20px; color: #aaa; font-size: 12px; }}
@media (max-width: 768px) {{ .container {{ padding: 10px; }} }}
</style>
</head>
<body>
<div class="header">
  <h1>CubeSandbox 性能基准测试报告</h1>
  <div class="subtitle">生成时间: {generated_at} &nbsp;|&nbsp; 运行次数: {run_count} &nbsp;|&nbsp; 场景数: {scenario_count}</div>
</div>

<div class="container">

<!-- Environment -->
<div class="section">
  <h2>测试环境</h2>
  <h3 style="font-size:14px;color:#888;margin:12px 0 8px;">硬件信息</h3>
  <table class="env-table" id="env-hardware"></table>
  <h3 style="font-size:14px;color:#888;margin:16px 0 8px;">CubeSandbox 环境</h3>
  <table class="env-table" id="env-cube"></table>
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
const ALL_BASELINES = {all_baselines_json};
const BASELINE_KEYS = {baseline_keys_json};
const BASELINE_COLORS = {baseline_colors_json};
const ENV = {env_json};
const PERF = {perf_json};

// ---- Helpers ----
function fmtMs(v) {{ return v === null || v === undefined || v === '' ? '-' : Number(v).toFixed(1); }}
function cmpBadge(cur, bl) {{
  if (!bl || !cur || cur <= 0) return '<span class="badge badge-na">-</span>';
  const r = cur / bl;
  if (r <= 1.05) return `<span class="badge badge-good">≈${{(r*100).toFixed(0)}}%</span>`;
  if (r <= 1.20) return `<span class="badge badge-warn">+${{((r-1)*100).toFixed(0)}}%</span>`;
  return `<span class="badge badge-bad">+${{((r-1)*100).toFixed(0)}}%</span>`;
}}

// ---- Environment ----
(function() {{
  const hwKeys = ['hostname', 'machine_type', 'os', 'cpu_model', 'cpu_config', 'numa_nodes', 'memory', 'disk'];
  const hwLabels = {{ hostname:'主机名', machine_type:'机器类型', os:'操作系统', cpu_model:'CPU 型号', cpu_config:'CPU 配置', numa_nodes:'NUMA 节点', memory:'内存总量', disk:'数据盘' }};
  const hwTable = document.getElementById('env-hardware');
  hwKeys.forEach(k => {{
    let v = ENV[k];
    if (v === undefined || v === null || v === '') return;
    if (k === 'os') v = `${{ENV.os_name || ''}}（${{ENV.os_version || ''}}），内核 ${{ENV.kernel || ''}}，${{ENV.arch || ''}}`;
    if (k === 'cpu_config') v = `${{ENV.cpu_sockets || '?'}} 路 × ${{ENV.cpu_cores_physical || '?'}} 核 × 2 线程 = ${{ENV.cpu_cores_logical || '?'}} 逻辑核心`;
    if (k === 'memory') v = `${{ENV.memory_total_gb || '?'}} GiB（${{ENV.memory_type || 'N/A'}}）`;
    if (k === 'disk') v = `${{ENV.disk_size_gb || '?'}} GB ${{ENV.disk_type || ''}}（${{ENV.disk_model || ''}}），${{ENV.disk_fs || ''}}`;
    hwTable.innerHTML += `<tr><td>${{hwLabels[k] || k}}</td><td>${{v}}</td></tr>`;
  }});

  const cubeKeys = ['sandbox_spec', 'template_image', 'template_id', 'template_status', 'storage', 'memory_tracking', 'api_url', 'cubeapi', 'python_sdk', 'httpx_requests', 'sdk_path', 'rounds', 'timestamp'];
  const cubeLabels = {{ sandbox_spec:'沙箱规格', template_image:'测试镜像', template_id:'模板 ID', template_status:'模板状态', storage:'存储方式', memory_tracking:'内存追踪', api_url:'API 地址', cubeapi:'CubeAPI', python_sdk:'Python / SDK', httpx_requests:'httpx / requests', sdk_path:'SDK 路径', rounds:'每场景轮数', timestamp:'时间戳' }};
  const cubeTable = document.getElementById('env-cube');
  cubeKeys.forEach(k => {{
    let v = ENV[k];
    if (v === undefined || v === null || v === '') return;
    if (k === 'storage') v = `CoW reflink（${{ENV.disk_fs || ''}}）`;
    if (k === 'memory_tracking') v = 'soft-dirty（/proc/PID/clear_refs）';
    if (k === 'cubeapi') v = `${{ENV.cubeapi_version || 'N/A'}}（commit ${{(ENV.cubeapi_commit || 'N/A').substring(0,8)}}，Go ${{ENV.cubeapi_go_version || 'N/A'}}）`;
    if (k === 'python_sdk') v = `${{ENV.python_impl || ENV.python_version || '?'}} / v${{ENV.sdk_version || '?'}}`;
    if (k === 'httpx_requests') v = `${{ENV.httpx_version || 'N/A'}} / ${{ENV.requests_version || 'N/A'}}`;
    if (k === 'sdk_path') v = `${{ENV.sdk_import_path || 'N/A'}}`;
    cubeTable.innerHTML += `<tr><td>${{cubeLabels[k] || k}}</td><td>${{v}}</td></tr>`;
  }});
}})();

// ---- Legend ----
(function() {{
  const leg = document.getElementById('legend');
  leg.innerHTML += '<div class="legend-item"><div class="legend-swatch" style="background:#667eea;"></div> 当前运行</div>';
  BASELINE_KEYS.forEach((k, i) => {{
    leg.innerHTML += `<div class="legend-item"><div class="legend-swatch" style="background:${{BASELINE_COLORS[i % BASELINE_COLORS.length]}};"></div> ${{k}}</div>`;
  }});
}})();

// ---- Scenario groups ----
const SCENARIO_GROUPS = [
  {{ id: 'coldstart', title: '基于模板创建沙箱（冷启动）', prefix: 'template-create', xKey: 'c', xValues: [1, 10, 20, 50], xLabel: '并发数' }},
  {{ id: 'snapshot', title: '创建快照（并发）', prefix: 'snapshot-create', xKey: 'c', xValues: [1, 5, 10], xLabel: '并发数' }},
  {{ id: 'createfrom', title: '基于快照启动沙箱', prefix: 'snapshot-create-from', xKey: 'c', xValues: [1, 10, 20, 50], xLabel: '并发数' }},
  {{ id: 'rollback', title: '回滚（Rollback）', prefix: 'rollback', xKey: 'c', xValues: [1, 5, 10], xLabel: '并发数' }},
  {{ id: 'pause', title: '暂停（Pause）', prefix: 'pause', xKey: 'c', xValues: [1, 5, 10], xLabel: '并发数' }},
  {{ id: 'resume', title: '恢复（Resume）', prefix: 'resume', xKey: 'c', xValues: [1, 5, 10], xLabel: '并发数' }},
];

(function() {{
  const container = document.getElementById('scenarios');

  function collectLineData(prefix, xKey, xValues) {{
    const currentData = [];
    const baselineSeries = {{}};
    BASELINE_KEYS.forEach(k => {{ baselineSeries[k] = []; }});
    xValues.forEach(xv => {{
      const scenario = `${{prefix}}-${{xKey}}${{xv}}`;
      const row = PERF.find(r => r.scenario === scenario);
      const cv = row ? (row.per_ms || row.avg_ms || null) : null;
      currentData.push(cv);
      BASELINE_KEYS.forEach(key => {{
        const bl = ALL_BASELINES[key];
        if (!bl || !bl.perf) {{ baselineSeries[key].push(null); return; }}
        const bb = bl.perf[scenario];
        baselineSeries[key].push(bb ? (bb.per || bb.avg || bb.wall_avg || null) : null);
      }});
    }});
    return {{ currentData, baselineSeries }};
  }}

  function renderChart(blockId, title, xLabels, currentData, baselineSeries) {{
    const datasets = [];
    if (currentData.some(v => v !== null)) {{
      datasets.push({{
        label: '当前运行', data: currentData,
        borderColor: '#667eea', backgroundColor: '#667eea20',
        borderWidth: 2.5, pointRadius: 4, tension: 0.2, spanGaps: false,
      }});
    }}
    BASELINE_KEYS.forEach((key, i) => {{
      const data = baselineSeries[key];
      if (!data || data.every(v => v === null)) return;
      datasets.push({{
        label: key, data: data,
        borderColor: BASELINE_COLORS[i % BASELINE_COLORS.length],
        borderWidth: 1.5, borderDash: [6, 3], pointRadius: 3,
        tension: 0.2, spanGaps: true,
      }});
    }});

    const canvas = document.createElement('canvas');
    canvas.id = 'chart-' + blockId;
    canvas.style.maxHeight = '300px';
    const wrap = document.createElement('div');
    wrap.className = 'chart-wrap';
    wrap.appendChild(canvas);

    new Chart(canvas, {{
      type: 'line',
      data: {{ labels: xLabels, datasets: datasets }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{ y: {{ title: {{ display: true, text: 'ms' }}, beginAtZero: true }} }}
      }}
    }});
    return wrap;
  }}

  SCENARIO_GROUPS.forEach(g => {{
    const {{ currentData, baselineSeries }} = collectLineData(g.prefix, g.xKey, g.xValues);
    const xLabels = g.xValues.map(v => g.xKey + '=' + v);
    const allRows = g.xValues.map(xv => {{
      const scenario = `${{g.prefix}}-${{g.xKey}}${{xv}}`;
      return PERF.find(r => r.scenario === scenario);
    }}).filter(Boolean);

    // Build summary table
    let tableHtml = '<table class="data-table"><thead><tr><th>场景</th><th>次数</th><th>并发</th><th>平均值</th><th>最小值</th><th>P50</th><th>P95</th><th>最大值</th><th>总耗时</th><th>单次均摊</th>';
    BASELINE_KEYS.forEach(k => {{ tableHtml += `<th>vs ${{k.split('(')[0].trim()}}</th>`; }});
    tableHtml += '</tr></thead><tbody>';
    allRows.forEach(r => {{
      const perMs = r.per_ms || r.avg_ms || 0;
      tableHtml += `<tr>
        <td>${{r.scenario}}</td><td>${{r.count||'-'}}</td><td>${{r.concurrency||'-'}}</td>
        <td>${{fmtMs(r.avg_ms)}}</td><td>${{fmtMs(r.min_ms)}}</td><td>${{fmtMs(r.p50_ms)}}</td>
        <td>${{fmtMs(r.p95_ms)}}</td><td>${{fmtMs(r.max_ms)}}</td>
        <td>${{fmtMs(r.wall_ms)}}</td><td>${{fmtMs(perMs)}}</td>`;
      BASELINE_KEYS.forEach(key => {{
        const bl = ALL_BASELINES[key];
        const bb = bl && bl.perf ? bl.perf[r.scenario] : null;
        const blPer = bb ? (bb.per || bb.avg || bb.wall_avg || 0) : 0;
        tableHtml += `<td>${{cmpBadge(perMs, blPer)}}</td>`;
      }});
      tableHtml += '</tr>';
    }});
    tableHtml += '</tbody></table>';

    const block = document.createElement('div');
    block.className = 'scenario-block';
    block.innerHTML = `<h3>${{g.title}}</h3>`;
    if (currentData.some(v => v !== null)) {{
      block.appendChild(renderChart(g.id, g.title, xLabels, currentData, baselineSeries));
    }}
    block.innerHTML += tableHtml;
    container.appendChild(block);
  }});
}})();
</script>
</body>
</html>"""


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
    for key in sorted(merged):
        samples = merged[key]["samples"]
        if not samples:
            continue
        first = samples[0]
        count = sum(s.get("count", 0) for s in samples)
        entry = {
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
    title: str = "CubeSandbox 性能基准测试报告",
) -> str:
    runs, merged_env, merged_perf = _merge_runs(data_files)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    baseline_keys = list(ALL_BASELINES.keys())

    html = _HTML.format(
        title=title,
        generated_at=generated_at,
        run_count=len(runs),
        scenario_count=len(merged_perf),
        all_baselines_json=json.dumps(ALL_BASELINES, ensure_ascii=False),
        baseline_keys_json=json.dumps(baseline_keys, ensure_ascii=False),
        baseline_colors_json=json.dumps(_BASELINE_COLORS, ensure_ascii=False),
        env_json=json.dumps(merged_env, ensure_ascii=False),
        perf_json=json.dumps(merged_perf, ensure_ascii=False),
    )

    out = Path(output_path).resolve()
    out.write_text(html, encoding="utf-8")
    print(f"\n📄 HTML report written to: {out}")
    return str(out)
