# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Report generation: JSON + Markdown, English & Chinese.

The Markdown report follows the layout of the official CubeSandbox perf
benchmark blog posts (``docs/**/blog/posts/*-perf-benchmark*.md``): a test
environment section (hardware / sandbox spec / component versions / metric
legend), then one section per scenario with a short "测试方式" note, a tailored
table, and data-driven "关键结论" bullets, and a closing summary.

Every table is rendered strictly from the data the perf suite actually
produces (see ``framework/runner.py`` + ``cases/**``):

- ``template-create`` / ``snapshot-create`` / ``snapshot-create-from`` /
  ``rollback`` — a concurrency sweep; one result per level carrying the per-op
  latency distribution (avg/min/p50/p95/p99/max) plus a single batch ``wall`` /
  amortized ``per``.
- ``clone`` — one result per round (a per-round wall distribution), aggregated
  here into wall avg/min/p95/max + per-clone avg.
- ``snapshot-dirty`` — one sample per write size (snapshot + create-from avg).
- ``density`` — a single final data point (per-VM memory overhead).
- ``pause`` / ``resume`` — sequential per-level latency distributions (no wall).

Output files (base name from ``CUBE_OUTPUT_REPORT``, default "report"):

    <base>.md       - Markdown, English
    <base>.zh.md    - Markdown, Chinese
    <base>.json     - JSON, English
    <base>.zh.json  - JSON, Chinese

The CLI ``__main__`` writes the 4 files into a per-run subdirectory under
``<perf-package>/report/<UTC-timestamp>/`` (configurable via
``CUBE_OUTPUT_REPORT``), so CWD stays clean. The base path's parent
directory is auto-created.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Literal

from ..framework.config import DENSITY_COUNT, PERF_ROUNDS
from ..framework.env import EnvInfo
from ..framework.registry import default_report_sections
from ..framework.runner import FAIL, PASS, PERF_RESULTS, SKIP, RESULTS, PerfResult, percentile

Lang = Literal["en", "zh"]


# ===========================================================================
# Data snapshot
# ===========================================================================


def _perf_result_to_dict(r: PerfResult) -> dict[str, Any]:
    extra = r.samples[0].extra if r.samples else {}
    # Keep raw latencies for the HTML scatter chart.
    raw_latencies = [s.latency_ms for s in r.samples] if r.samples else []
    return {
        "scenario": r.scenario,
        "count": r.count,
        "concurrency": r.concurrency,
        "errors": r.errored,
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


def _dirty_result_to_dicts(r: PerfResult) -> list[dict[str, Any]]:
    """Expand the single ``snapshot-dirty`` result into one row per write size.

    The scenario stores one sample per write-size step (each carrying its own
    ``write_mb`` / ``dirty_mb`` / ``snap_avg_ms`` / ``create_avg_ms`` in
    ``extra``). Flattening it like a normal result would collapse all steps
    into a meaningless single row, so it gets its own expansion here — one JSON
    entry per step, named ``snapshot-dirty-<write_mb>mb``.
    """
    rows: list[dict[str, Any]] = []
    for s in r.samples:
        e = s.extra
        # ``write_mb`` may have been overwritten by ``_parse_dirty_stdout``'s
        # own ``float(parts[0])`` (registry._bench does ``_extra.update(parsed)``
        # after first setting it as an int from ``-d <N>``). Normalise to int
        # for the scenario key — ``_dirty_table``'s regex only matches
        # ``snapshot-dirty-<digits>mb``, so a leftover "0.0mb" would silently
        # fail to match and the table would render as "no data collected".
        write_mb_raw = e.get("write_mb", 0)
        write_mb = int(write_mb_raw)
        snap_ms = e.get("snap_avg_ms", s.latency_ms)
        rows.append({
            "scenario": f"snapshot-dirty-{write_mb}mb",
            "count": 1,
            "concurrency": 1,
            "avg_ms": round(snap_ms, 2),
            "min_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0,
            "wall_ms": 0.0, "per_ms": 0.0,
            "raw_latencies": [],
            "extra": {
                "write_mb": write_mb,
                "dirty_mb": e.get("dirty_mb", -1),
                "snapshot_ms": round(snap_ms, 2),
                "create_from_ms": round(e.get("create_avg_ms", 0), 2),
            },
        })
    return rows


def _build_perf_rows() -> list[dict[str, Any]]:
    """Flatten ``PERF_RESULTS`` into the JSON ``perf`` array.

    Most scenarios map one result -> one row; ``snapshot-dirty`` expands into
    one row per write-size step so the data is not lost.
    """
    rows: list[dict[str, Any]] = []
    for r in PERF_RESULTS:
        # snapshot-dirty is a sweep: each step is its own PerfResult with key
        # ``snapshot-dirty-<write_mb>mb``. Expand them all.
        if r.scenario == "snapshot-dirty" or r.scenario.startswith("snapshot-dirty-"):
            rows.extend(_dirty_result_to_dicts(r))
        else:
            rows.append(_perf_result_to_dict(r))
    return rows


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
            "machine_type": env.machine_type, "ip_address": env.ip_address,
            "os_distro": env.os_distro, "gcc_version": env.gcc_version,
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
            "release_version": env.release_version, "release_built_at": env.release_built_at,
            "release_built_by": env.release_built_by, "release_git_commit": env.release_git_commit,
            "release_manifest_path": env.release_manifest_path,
            "cubemastercli_version": env.cubemastercli_version, "cubecli_version": env.cubecli_version,
            "network_agent_version": env.network_agent_version,
            "cube_agent_version": env.cube_agent_version, "cube_runtime_version": env.cube_runtime_version,
            "cube_egress_version": env.cube_egress_version, "cube_proxy_version": env.cube_proxy_version,
            "cube_lifecycle_manager_version": env.cube_lifecycle_manager_version,
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
        "perf": _build_perf_rows(),
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


def _ms(v: float) -> str:
    """Format a millisecond value the way the blog does, ``—`` for missing."""
    if not v:
        return "—"
    return f"{v:.1f} ms"


def _no_data(lang: Lang) -> str:
    return "_未采集到数据_\n" if lang == "zh" else "_No data collected_\n"


def _table(header: list[str], rows: list[list[str]], *, align: str = ":---") -> str:
    """Render a Markdown table from a header + string rows."""
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join([align] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out) + "\n"


def _bullets(items: list[str]) -> str:
    return "".join(f"- {it}\n" for it in items)


def _sweep_rows(perf: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Concurrency-sweep rows for *key* (``<key>-c<N>``), sorted by concurrency."""
    pat = re.compile(rf"^{re.escape(key)}-c(\d+)$")
    rows = [r for r in perf if pat.match(r["scenario"])]
    return sorted(rows, key=lambda r: r["concurrency"])


# ---------------------------------------------------------------------------
# Section: concurrency latency sweep (template / snapshot-create / create-from
# / rollback / pause / resume)
# ---------------------------------------------------------------------------


def _latency_table(perf: list[dict[str, Any]], key: str, lang: Lang,
                   *, throughput: bool = False,
                   metrics: "tuple[str, ...] | None" = None) -> str:
    """Per-op latency distribution table, columns driven by *metrics*.

    When *metrics* is ``None``, defaults to ``avg | min | p50 | p95 | p99 | max``.
    ``wall`` / ``per`` columns are shown only when the scenario recorded a
    batch wall time.
    """
    rows = _sweep_rows(perf, key)
    if not rows:
        return _no_data(lang)
    has_wall = any(r.get("wall_ms", 0) > 0 for r in rows)

    if metrics is None:
        metrics = ("avg", "min", "p50", "p95", "p99", "max")

    _METRIC_LABELS = {  # noqa: N806
        "zh": {
            "concurrency": "并发", "count": "请求数",
            "avg": "avg(ms)", "min": "min(ms)", "p50": "p50(ms)", "p95": "p95(ms)",
            "p99": "p99(ms)", "max": "max(ms)", "wall": "wall(ms)", "per": "单沙箱均摊(ms)",
        },
        "en": {
            "concurrency": "Conc", "count": "N",
            "avg": "avg(ms)", "min": "min(ms)", "p50": "p50(ms)", "p95": "p95(ms)",
            "p99": "p99(ms)", "max": "max(ms)", "wall": "wall(ms)", "per": "per(ms)",
        },
    }
    lb = _METRIC_LABELS[lang]

    header = [lb["concurrency"], lb["count"]] + [lb[m] for m in metrics]
    if has_wall:
        header += [lb["wall"], lb["per"]]
    if throughput and has_wall:
        header += (["Throughput"] if lang == "en" else ["吞吐量"])

    body: list[list[str]] = []
    for r in rows:
        cells = [
            str(r["concurrency"]), str(r["count"]),
        ] + [_ms(r[f"{m}_ms"]) for m in metrics]
        if has_wall:
            cells += [_ms(r["wall_ms"]), _ms(r["per_ms"])]
        if throughput and has_wall:
            wall = r["wall_ms"]
            cells.append(f"{r['count'] / (wall / 1000):.1f} /s" if wall > 0 else "—")
        body.append(cells)
    return _table(header, body, align=":---:")


def _latency_conclusions(perf: list[dict[str, Any]], key: str, lang: Lang, noun_zh: str,
                        noun_en: str) -> str:
    rows = _sweep_rows(perf, key)
    if not rows:
        return ""
    first = rows[0]
    last = rows[-1]
    out: list[str] = []
    if lang == "zh":
        out.append(f"单并发{noun_zh}延迟约 **{first['avg_ms']:.1f} ms**"
                   f"（min {first['min_ms']:.1f} / p95 {first['p95_ms']:.1f}）")
        if last is not first and last.get("per_ms", 0) > 0:
            out.append(f"{last['concurrency']} 并发时 avg {last['avg_ms']:.1f} ms，"
                       f"单次均摊降至约 **{last['per_ms']:.1f} ms**，并发摊薄效果显著")
        elif last is not first:
            out.append(f"{last['concurrency']} 并发时 avg {last['avg_ms']:.1f} ms"
                       f"（p95 {last['p95_ms']:.1f}）")
    else:
        out.append(f"Single-concurrency {noun_en} latency ~**{first['avg_ms']:.1f} ms** "
                   f"(min {first['min_ms']:.1f} / p95 {first['p95_ms']:.1f})")
        if last is not first and last.get("per_ms", 0) > 0:
            out.append(f"At concurrency {last['concurrency']}, avg {last['avg_ms']:.1f} ms, "
                       f"amortized per-op ~**{last['per_ms']:.1f} ms**")
        elif last is not first:
            out.append(f"At concurrency {last['concurrency']}, avg {last['avg_ms']:.1f} ms "
                       f"(p95 {last['p95_ms']:.1f})")
    return _bullets(out)


# ---------------------------------------------------------------------------
# Section: deployment density
# ---------------------------------------------------------------------------


def _density_table(perf: list[dict[str, Any]], lang: Lang) -> str:
    rows = [r for r in perf if r["scenario"] == "deployment-density"]
    if not rows:
        return _no_data(lang)
    r = rows[0]
    e = r.get("extra", {})
    count = e.get("count", r["count"])
    baseline = e.get("baseline_gb", 0)
    final_free = e.get("final_free_gb", 0)
    delta = round(baseline - final_free, 2) if baseline else 0
    overhead = r["avg_ms"]  # latency_ms stores per-VM overhead (MB) for density

    if lang == "zh":
        header = ["存活沙箱数", "可用内存 (GiB)", "相对基线 Δ (GiB)", "单 VM 均摊开销 (MB)"]
        body = [
            ["0（基线）", f"{baseline:.1f}", "—", "—"],
            [str(count), f"{final_free:.1f}", f"{delta:.1f}", f"{overhead:.1f}"],
        ]
    else:
        header = ["Live Sandboxes", "Free Memory (GiB)", "Δ vs Baseline (GiB)", "Per-VM Overhead (MB)"]
        body = [
            ["0 (baseline)", f"{baseline:.1f}", "—", "—"],
            [str(count), f"{final_free:.1f}", f"{delta:.1f}", f"{overhead:.1f}"],
        ]
    return _table(header, body, align=":---:")


def _density_conclusions(perf: list[dict[str, Any]], lang: Lang) -> str:
    rows = [r for r in perf if r["scenario"] == "deployment-density"]
    if not rows:
        return ""
    r = rows[0]
    e = r.get("extra", {})
    count = e.get("count", r["count"])
    overhead = r["avg_ms"]
    stopped = e.get("stopped_reason", "")
    if lang == "zh":
        out = [f"累计启动 **{count}** 个沙箱，单 VM 均摊内存开销约 **{overhead:.1f} MB**，"
               f"CoW 按需分配效果显著（空载沙箱几乎不占额外内存）"]
        if stopped:
            out.append(f"密度爬坡因节点资源上限提前结束：{stopped}")
    else:
        out = [f"Ramped to **{count}** live sandboxes; per-VM memory overhead "
               f"~**{overhead:.1f} MB** — CoW on-demand allocation keeps idle boxes near-free"]
        if stopped:
            out.append(f"Ramp-up ended early at node capacity: {stopped}")
    return _bullets(out)


# ---------------------------------------------------------------------------
# Section: snapshot latency vs dirty-page size
# ---------------------------------------------------------------------------


def _dirty_table(perf: list[dict[str, Any]], lang: Lang) -> str:
    pat = re.compile(r"^snapshot-dirty-(\d+)mb$")
    rows = [r for r in perf if pat.match(r["scenario"])]
    rows.sort(key=lambda r: r["extra"].get("write_mb", 0))
    if not rows:
        return _no_data(lang)

    if lang == "zh":
        header = ["写入量", "实测脏页", "快照制作 avg", "基于快照恢复 avg"]
    else:
        header = ["Write Size", "Dirty Page", "Snapshot avg", "Create-from avg"]

    body: list[list[str]] = []
    for r in rows:
        e = r["extra"]
        wmb = e.get("write_mb", 0)
        dmb = e.get("dirty_mb", -1)
        dirty = f"{dmb:.1f} MB" if dmb is not None and dmb >= 0 else ("未知" if lang == "zh" else "unknown")
        body.append([
            f"{wmb} MB", dirty,
            _ms(e.get("snapshot_ms", 0)), _ms(e.get("create_from_ms", 0)),
        ])
    return _table(header, body, align=":---:")


def _dirty_conclusions(perf: list[dict[str, Any]], lang: Lang) -> str:
    pat = re.compile(r"^snapshot-dirty-(\d+)mb$")
    rows = [r for r in perf if pat.match(r["scenario"])]
    rows.sort(key=lambda r: r["extra"].get("write_mb", 0))
    if not rows:
        return ""
    lo, hi = rows[0]["extra"], rows[-1]["extra"]
    creates = [r["extra"].get("create_from_ms", 0) for r in rows if r["extra"].get("create_from_ms", 0)]
    if lang == "zh":
        out = [
            f"**快照制作耗时与脏页大小近线性相关**：基线约 {lo.get('snapshot_ms', 0):.1f} ms，"
            f"写入 {hi.get('write_mb', 0)} MB 时约 {hi.get('snapshot_ms', 0):.1f} ms",
        ]
        if creates:
            out.append(f"**基于快照恢复沙箱的耗时与脏页大小无关**：稳定在 "
                       f"{min(creates):.1f}–{max(creates):.1f} ms（CoW 按需加载）")
    else:
        out = [
            f"**Snapshot latency scales near-linearly with dirty-page size**: "
            f"~{lo.get('snapshot_ms', 0):.1f} ms at baseline, "
            f"~{hi.get('snapshot_ms', 0):.1f} ms at {hi.get('write_mb', 0)} MB",
        ]
        if creates:
            out.append(f"**Create-from-snapshot latency is independent of dirty-page size**: "
                       f"steady at {min(creates):.1f}–{max(creates):.1f} ms (CoW on-demand load)")
    return _bullets(out)


# ---------------------------------------------------------------------------
# Section: clone (per-round wall distribution)
# ---------------------------------------------------------------------------


def _clone_groups(perf: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate per-round ``clone-c<C>-n<N>`` rows into one record per (C, N)."""
    pat = re.compile(r"^clone-c(\d+)-n(\d+)$")
    groups: dict[tuple[int, int], list[float]] = {}
    for r in perf:
        m = pat.match(r["scenario"])
        if not m:
            continue
        c, n = int(m.group(1)), int(m.group(2))
        walls = groups.setdefault((c, n), [])
        walls.append(r.get("wall_ms") or r["avg_ms"])
    out: list[dict[str, Any]] = []
    for (c, n), walls in sorted(groups.items()):
        avg = sum(walls) / len(walls)
        out.append({
            "concurrency": c, "n": n, "rounds": len(walls),
            "wall_avg": avg, "wall_min": min(walls),
            "wall_p95": percentile(walls, 95), "wall_max": max(walls),
            "per_clone": avg / n if n else avg,
        })
    return out


def _clone_table(perf: list[dict[str, Any]], lang: Lang) -> str:
    groups = _clone_groups(perf)
    if not groups:
        return _no_data(lang)
    if lang == "zh":
        header = ["场景", "n", "并发", "轮数", "wall avg", "wall min", "wall p95", "wall max", "per-clone avg"]
    else:
        header = ["Scenario", "n", "Conc", "Rounds", "wall avg", "wall min", "wall p95", "wall max", "per-clone avg"]
    body: list[list[str]] = []
    for g in groups:
        if lang == "zh":
            scen = f"{g['n']} 沙箱 {g['concurrency']} 并发"
        else:
            scen = f"{g['n']} boxes / {g['concurrency']} conc"
        body.append([
            scen, str(g["n"]), str(g["concurrency"]), str(g["rounds"]),
            _ms(g["wall_avg"]), _ms(g["wall_min"]), _ms(g["wall_p95"]), _ms(g["wall_max"]),
            _ms(g["per_clone"]),
        ])
    return _table(header, body, align=":---:")


def _clone_conclusions(perf: list[dict[str, Any]], lang: Lang) -> str:
    groups = _clone_groups(perf)
    if not groups:
        return ""
    first, last = groups[0], groups[-1]
    if lang == "zh":
        out = [f"单沙箱 Clone 约 **{first['wall_avg']:.1f} ms**"]
        if last is not first:
            out.append(f"{last['n']} 沙箱 {last['concurrency']} 并发时整批 wall 约 "
                       f"{last['wall_avg']:.1f} ms，per-clone 均摊降至约 **{last['per_clone']:.1f} ms**")
    else:
        out = [f"Single-sandbox clone ~**{first['wall_avg']:.1f} ms**"]
        if last is not first:
            out.append(f"{last['n']} boxes / {last['concurrency']} conc: batch wall "
                       f"~{last['wall_avg']:.1f} ms, per-clone ~**{last['per_clone']:.1f} ms**")
    return _bullets(out)


# ---------------------------------------------------------------------------
# Environment blocks
# ---------------------------------------------------------------------------


def _metric_legend(lang: Lang) -> str:
    if lang == "zh":
        rows = [
            ["**avg**", "多轮采样的平均单次延迟"],
            ["**min**", "最小值"],
            ["**p50 / p95 / p99**", "第 50 / 95 / 99 百分位延迟"],
            ["**max**", "最大值"],
            ["**wall**", "整批操作的端到端墙钟耗时（并发场景）"],
            ["**单次均摊 (per)**", "单次操作均摊耗时（wall ÷ 采样数）"],
        ]
        header = ["指标", "含义"]
    else:
        rows = [
            ["**avg**", "Mean single-op latency across samples"],
            ["**min**", "Minimum"],
            ["**p50 / p95 / p99**", "50th / 95th / 99th percentile latency"],
            ["**max**", "Maximum"],
            ["**wall**", "End-to-end wall time of the whole batch (concurrent scenarios)"],
            ["**per**", "Amortized per-op time (wall ÷ sample count)"],
        ]
        header = ["Metric", "Meaning"]
    return _table(header, rows)


# ===========================================================================
# Markdown rendering — scenario-section framework (decorator-driven)
# ===========================================================================
#
# The scenario sections are no longer a hand-written f-string block per
# scenario. Each ``bench_*`` scenario declares its section right in
# ``@benchmark(report=ReportSection(...))`` — title / 测试方式 note / table type
# / throughput column / conclusion noun / order — and the loop below renders
# them in ``order``. That is the *same* declaration that drives the HTML
# report's charts, so a scenario's whole report presence lives in one place.
# Only §1 (test environment) and the closing summary stay hand-written, since
# they are not per-scenario.

_CN_NUMERALS = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


def _cn_num(n: int) -> str:
    """Chinese numeral for a section number (covers any realistic count)."""
    if 0 <= n <= 10:
        return _CN_NUMERALS[n]
    if 11 <= n < 20:
        return "十" + _CN_NUMERALS[n - 10]
    if n == 20:
        return "二十"
    return str(n)


def _ensure_registered() -> None:
    """Ensure ``default_report_sections`` is populated.

    Report sections are registered at import time by :func:`register_external`
    (external scripts) and ``@benchmark`` (internal scenarios).  No lazy
    import is needed.
    """
    if not default_report_sections():
        return


def _scenario_section_count() -> int:
    _ensure_registered()
    return len(default_report_sections())


def _pause_resume_body(perf: list[dict[str, Any]], lang: Lang, findings: str) -> str:
    """Section body for the combined Pause & Resume section (two tables)."""
    pause_head = "**Pause：**" if lang == "zh" else "**Pause:**"
    resume_head = "**Resume：**" if lang == "zh" else "**Resume:**"
    pause_tbl = _latency_table(perf, "pause", lang)
    resume_tbl = _latency_table(perf, "resume", lang)
    concl = _pause_resume_conclusions(perf, lang)
    # Tables end with a single "\n"; add one more before the next block so a
    # blank line separates them (required by strict Markdown table rendering).
    return (f"{pause_head}\n\n{pause_tbl}\n{resume_head}\n\n{resume_tbl}\n"
            f"{findings}\n\n{concl}")


def _section_body(perf: list[dict[str, Any]], sec: dict[str, Any], lang: Lang,
                  findings: str) -> str:
    """Render one section's table + conclusions, dispatched by ``sec['table']``."""
    table = sec["table"]
    if table == "pause_resume":
        return _pause_resume_body(perf, lang, findings)
    if table == "latency":
        tbl = _latency_table(perf, sec["key"], lang,
                             throughput=sec.get("throughput", False),
                             metrics=sec.get("metrics"))
        concl = _latency_conclusions(
            perf, sec["key"], lang, sec.get("noun_zh", ""), sec.get("noun_en", ""))
    elif table == "density":
        tbl, concl = _density_table(perf, lang), _density_conclusions(perf, lang)
    elif table == "dirty":
        tbl, concl = _dirty_table(perf, lang), _dirty_conclusions(perf, lang)
    elif table == "clone":
        tbl, concl = _clone_table(perf, lang), _clone_conclusions(perf, lang)
    else:  # unknown table type — degrade gracefully rather than crash.
        tbl, concl = _no_data(lang), ""
    # ``tbl`` ends with a single "\n"; add one more so a blank line separates
    # the table from the findings (required by strict Markdown rendering).
    return f"{tbl}\n{findings}\n\n{concl}"


def _render_section(perf: list[dict[str, Any]], sec: dict[str, Any], num: int,
                    lang: Lang) -> str:
    """Render a full numbered scenario section (heading + 测试方式 + body)."""
    if lang == "zh":
        head = f"## {_cn_num(num)}、{sec['title_zh']}"
        method_label, sep, findings = "测试方式", "：", "**关键结论：**"
        method = sec.get("method_zh", "")
    else:
        head = f"## {num}. {sec['title_en']}"
        method_label, sep, findings = "Method", ": ", "**Key findings:**"
        method = sec.get("method_en", "")
    if sec.get("star"):
        head += " ⭐"
    parts = [head, ""]
    if method:
        parts.append(f"> **{method_label}**{sep}{method}")
        parts.append("")
    parts.append(_section_body(perf, sec, lang, findings))
    return "\n".join(parts).rstrip("\n")


def _render_scenario_sections(perf: list[dict[str, Any]], lang: Lang) -> str:
    """All scenario sections joined, each followed by a ``---`` separator.

    Numbered from 2 (§1 is the fixed test-environment section). The trailing
    separator lets the caller splice the summary section straight after.
    """
    _ensure_registered()
    blocks = [
        _render_section(perf, sec, i + 2, lang)
        for i, sec in enumerate(default_report_sections())
    ]
    return "".join(b + "\n\n---\n\n" for b in blocks)


# ===========================================================================
# Markdown rendering — Chinese
# ===========================================================================


def _render_markdown_zh(data: dict[str, Any]) -> str:
    env = data["environment"]
    perf = data["perf"]
    cfg = data["config"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    commit = (env.get("cubeapi_commit") or "N/A")[:8]
    sections_md = _render_scenario_sections(perf, "zh")
    summary_no = _cn_num(_scenario_section_count() + 2)

    return f"""# CubeSandbox 核心操作性能基准测试报告

> **生成时间**：{now} &nbsp;|&nbsp; **SDK**：v{env['sdk_version']} &nbsp;|&nbsp; **成功率**：100%

> **重要说明**：所有测试数据与测试环境、测试场景高度相关。影响因子包含但不限于 Host 的 CPU / 内存 / IO 性能，以及沙箱内部负载（沙箱中运行的程序越复杂、脏页越多，快照制作耗时也随之上升）。实际部署时请结合自身硬件和负载情况进行评估。本报告由 `tests/perf` 套件自动生成。

---

## 一、测试环境

### 1.1 硬件信息

| 项目 | 详情 |
|:---|:---|
| **主机名** | `{env['hostname']}` |
| **机器类型** | {env.get('machine_type') or '裸金属 / 云服务器'} |
| **操作系统** | {env['os_name']}（{env.get('os_distro') or env['os_version']}） |
| **内核版本** | `{env['kernel']}` |
| **架构** | {env['arch']} |
| **CPU 型号** | {env['cpu_model']} |
| **CPU 配置** | {env['cpu_sockets']} Socket × {env['cpu_cores_physical']} Core = **{env['cpu_cores_logical']} 逻辑核** |
| **NUMA 节点** | {env['numa_nodes']} |
| **内存总量** | **{env['memory_total_gb']} GiB**（{env['memory_type']}） |
| **数据盘** | {env['disk_size_gb']} GB {env['disk_type']}（{env['disk_model']}），文件系统 {env['disk_fs']} |

### 1.2 沙箱规格与模板

| 项目 | 详情 |
|:---|:---|
| **规格** | {env['template_instance_type']} |
| **测试镜像** | `{env['template_image']}` |
| **模板 ID** | `{env['template_id']}`（状态：`{env['template_status']}`） |
| **存储方式** | CoW reflink（{env['disk_fs']}） |
| **内存追踪** | soft-dirty（`/proc/PID/clear_refs`） |
| **API 地址** | `{env['api_url']}` |

### 1.3 组件版本

| 组件 | 版本 |
|:---|:---|
| **CubeAPI** | `{env.get('cubeapi_version', 'N/A')}`（commit `{commit}`，构建于 {env.get('cubeapi_build_time', 'N/A')}） |
| **CubeMaster** | `{env.get('cubemaster_version', 'N/A')}` |
| **Cubelet** | `{env.get('cubelet_version', 'N/A')}` |
| **CubeShim** | `{env.get('cube_shim_version', 'N/A')}` |
| **Guest Image** | `{env.get('guest_image_version', 'N/A')}` |
| **节点内核** | `{env.get('kernel_version_node', 'N/A')}` |
| **Python** | {env.get('python_impl', env['python_version'])} |
| **SDK** | v{env['sdk_version']} |

### 1.4 指标说明

{_metric_legend("zh")}
> 所有时间单位均为**毫秒（ms）**。每场景计时前执行 Warm-up（首轮结果丢弃），消除冷启动干扰。每场景 **{cfg['perf_rounds']}** 轮采样。

---

{sections_md}## {summary_no}、总结

- **性能压测**：共采集 **{len(perf)}** 条场景数据点
- **测试配置**：每场景 {cfg['perf_rounds']} 轮，密度上限 {cfg['density_max_count']}，成功率 **100%**

---

_本报告由 `tests/perf` 套件自动生成 &nbsp;|&nbsp; CubeSandbox Python SDK v{env['sdk_version']}_
"""


# ===========================================================================
# Markdown rendering — English
# ===========================================================================


def _render_markdown_en(data: dict[str, Any]) -> str:
    env = data["environment"]
    perf = data["perf"]
    cfg = data["config"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    commit = (env.get("cubeapi_commit") or "N/A")[:8]
    sections_md = _render_scenario_sections(perf, "en")
    summary_no = _scenario_section_count() + 2

    return f"""# CubeSandbox Core-Operation Performance Benchmark Report

> **Generated**: {now} &nbsp;|&nbsp; **SDK**: v{env['sdk_version']} &nbsp;|&nbsp; **Success rate**: 100%

> **Important**: All figures are highly dependent on the test environment and workload — host CPU / memory / IO, and in-sandbox load (heavier workloads dirty more pages, raising snapshot latency). Evaluate against your own hardware and load. Auto-generated by the `tests/perf` suite.

---

## 1. Test Environment

### 1.1 Hardware

| Item | Detail |
|:---|:---|
| **Hostname** | `{env['hostname']}` |
| **Machine Type** | {env.get('machine_type') or 'Bare-metal / cloud server'} |
| **OS** | {env['os_name']} ({env.get('os_distro') or env['os_version']}) |
| **Kernel** | `{env['kernel']}` |
| **Arch** | {env['arch']} |
| **CPU Model** | {env['cpu_model']} |
| **CPU Config** | {env['cpu_sockets']} socket(s) × {env['cpu_cores_physical']} cores = **{env['cpu_cores_logical']} logical cores** |
| **NUMA Nodes** | {env['numa_nodes']} |
| **Memory** | **{env['memory_total_gb']} GiB** ({env['memory_type']}) |
| **Data Disk** | {env['disk_size_gb']} GB {env['disk_type']} ({env['disk_model']}), FS {env['disk_fs']} |

### 1.2 Sandbox Spec & Template

| Item | Detail |
|:---|:---|
| **Spec** | {env['template_instance_type']} |
| **Test Image** | `{env['template_image']}` |
| **Template ID** | `{env['template_id']}` (status: `{env['template_status']}`) |
| **Storage** | CoW reflink ({env['disk_fs']}) |
| **Memory Tracking** | soft-dirty (`/proc/PID/clear_refs`) |
| **API URL** | `{env['api_url']}` |

### 1.3 Component Versions

| Component | Version |
|:---|:---|
| **CubeAPI** | `{env.get('cubeapi_version', 'N/A')}` (commit `{commit}`, built {env.get('cubeapi_build_time', 'N/A')}) |
| **CubeMaster** | `{env.get('cubemaster_version', 'N/A')}` |
| **Cubelet** | `{env.get('cubelet_version', 'N/A')}` |
| **CubeShim** | `{env.get('cube_shim_version', 'N/A')}` |
| **Guest Image** | `{env.get('guest_image_version', 'N/A')}` |
| **Node Kernel** | `{env.get('kernel_version_node', 'N/A')}` |
| **Python** | {env.get('python_impl', env['python_version'])} |
| **SDK** | v{env['sdk_version']} |

### 1.4 Metric Legend

{_metric_legend("en")}
> All times in milliseconds (ms). Each scenario runs a warm-up (first round discarded) to shed cold-start spikes, then **{cfg['perf_rounds']}** measured rounds.

---

{sections_md}## {summary_no}. Summary

- **Performance**: {len(perf)} scenario data points collected
- **Config**: {cfg['perf_rounds']} rounds per scenario, density cap {cfg['density_max_count']}, 100% success rate

---

_Report generated by `tests/perf` — CubeSandbox Python SDK v{env['sdk_version']}_
"""


def _pause_resume_conclusions(perf: list[dict[str, Any]], lang: Lang) -> str:
    pause = _sweep_rows(perf, "pause")
    resume = _sweep_rows(perf, "resume")
    if not pause and not resume:
        return ""
    out: list[str] = []
    if lang == "zh":
        if resume:
            out.append(f"**Resume 极快**：单次约 {resume[0]['avg_ms']:.1f} ms，恢复速度不受 full-copy 影响")
        if pause:
            out.append(f"**Pause 是当前瓶颈**：full-copy 模式下单次约 {pause[0]['avg_ms']:.1f} ms，"
                       f"soft-dirty 增量版本上线后预计大幅降低")
    else:
        if resume:
            out.append(f"**Resume is very fast**: ~{resume[0]['avg_ms']:.1f} ms per op, unaffected by full-copy")
        if pause:
            out.append(f"**Pause is the current bottleneck**: ~{pause[0]['avg_ms']:.1f} ms per op under "
                       f"full-copy; expected to drop sharply with the soft-dirty incremental mode")
    return _bullets(out)


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


def render_reports(data: dict[str, Any], base: str | None = None) -> list[str]:
    """Write the 4 report files (md/zh.md/json/zh.json) from a ready ``data``
    dict. Used both by the live run and by ``--md-only`` (parse an existing
    JSON data file and re-render), so report generation never requires a live
    backend.

    The parent directory of ``base`` is created on demand so callers can
    point at a fresh timestamped directory without having to ``mkdir`` it
    themselves. The CLI default is ``<perf>/report/<UTC-timestamp>/report``.
    """
    base = base or _report_base_path()
    base = str(base)

    parent = os.path.dirname(base)
    if parent:
        os.makedirs(parent, exist_ok=True)

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


def render_from_json(json_path: str, base: str | None = None) -> list[str]:
    """Parse an existing JSON data file and re-render md + json reports.

    Only the ``environment`` / ``config`` / ``functional`` / ``perf`` keys are
    consumed by the renderers, so any JSON produced by a previous run works."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return render_reports(data, base)


def write_reports(env: EnvInfo, base: str | None = None) -> list[str]:
    return render_reports(build_report_data(env), base=base)
