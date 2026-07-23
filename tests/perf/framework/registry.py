# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Benchmark registry, scenario selection, and the run driver.

This is the framework core that the scenario modules under ``cases/`` plug
into. The ``@benchmark`` decorator collects every scenario's metadata (CLI
aliases, opt-in/opt-out skip gates, HTML report chart groups) in one place and
auto-populates three derived tables — so a new scenario never has to touch a
registry, an alias table, a hand-written skip block, or the report module.

The decoration order (== the order in which ``cases/__init__.py`` imports the
scenario modules) is the canonical run order. Importing ``perf.cases`` is what
fills these tables; ``run_all`` then just iterates the populated registry.
"""

from __future__ import annotations

import functools
import inspect
import os
import platform
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from cubesandbox import Config

from .config import CONCURRENCY_LEVELS, PERF_ROUNDS, PERF_SETTLE, PERF_WARMUP
from .runner import (
    PERF_RESULTS,
    green,
    measure_parallel,
    print_parallel_stats,
    red,
    sandbox_op,
    snapshot_op,
    skip,
    yellow,
)

# ---------------------------------------------------------------------------
# Scenario registry (populated by the @benchmark decorator)
# ---------------------------------------------------------------------------
#
# Adding a benchmark is a one-liner: write the function and tag it with
# ``@benchmark("my-scenario")``. Everything a scenario needs lives on that one
# decorator — CLI aliases, the opt-in/opt-out skip gate, and the HTML report
# chart group — so a new benchmark never has to touch a registry, an alias
# table, a hand-written skip block, or the report module. The decoration order
# (== the order scenario modules are imported in ``cases/__init__.py``) is the
# canonical run order.
BENCHMARK_REGISTRY: "dict[str, Callable[[Config], None]]" = {}
BENCHMARK_ALIASES: "dict[str, list[str]]" = {}
# HTML report chart groups contributed via ``report=...``, in decoration
# order. Consumed by ``report_html`` (see ``default_report_scenarios``).
REPORT_SCENARIOS: "list[dict]" = []
# Markdown report sections contributed via ``report=ReportSection(...)``.
# Consumed by ``reporting.report`` (see ``default_report_sections``); the
# Markdown report iterates these declarations instead of hard-coding one
# f-string block per scenario. Sorted by ``order`` at render time.
REPORT_SECTIONS: "list[dict]" = []
# Scenario keys explicitly named on the CLI (``--scenarios``/``--only``).
# Populated by ``select_benchmarks``; an explicitly-selected scenario bypasses
# its default opt-in/opt-out gate — naming a scenario *is* the intent to run
# it, so ``--only ivshmem`` no longer also needs ``CUBE_RUN_IVSHMEM=1``.
_FORCED_KEYS: "set[str]" = set()


@dataclass(frozen=True)
class ReportChart:
    """One chart + summary-table group in the HTML perf report.

    A benchmark may declare zero, one, or several charts (e.g. the
    pause/resume benchmark feeds both a 暂停 and a 恢复 chart). *prefix*
    defaults to the benchmark key and is the scenario-name prefix the report
    matches against (``<prefix>-<x_key><N>``).
    """
    title: str
    prefix: "str | None" = None
    x_key: str = "c"
    x_label: str = "并发数"
    fallback: "tuple[int, ...]" = (1, 2, 4)


# Backward-compatible alias — ``ReportGroup`` was the original name for the
# HTML chart group before ``ReportSection`` was introduced for the Markdown
# report. Kept so old ``report=ReportGroup(...)`` declarations keep working.
ReportGroup = ReportChart


@dataclass(frozen=True)
class ReportSection:
    """One numbered section of the Markdown perf report (and its HTML charts).

    This is the single declaration that drives *both* reports for a scenario:
    the Markdown renderer (``reporting.report``) iterates these sections in
    ``order`` and renders each one's title / "测试方式" note / table / "关键结论"
    from the fields here (no more one hand-written f-string block per
    scenario), and the ``charts`` a section carries feed the HTML report's
    chart groups (via ``ReportChart``), so a scenario's whole report presence
    lives in the same ``@benchmark(report=...)`` one-liner.

    Fields:

    - *table*: which table/conclusion renderer the Markdown report uses —
      one of ``"latency"`` / ``"density"`` / ``"dirty"`` / ``"clone"`` /
      ``"pause_resume"``. ``"latency"`` matches the concurrency-sweep rows
      ``<benchmark-key>-c<N>``; the others have bespoke renderers.
    - *title_zh* / *title_en*: the section heading in each language.
    - *method_zh* / *method_en*: the "测试方式" / "Method" note (Markdown, may
      contain inline code / ``{id}`` etc. — plain strings, no f-string escapes).
    - *order*: sort key deciding the section's position (and its rendered
      number) in the Markdown report. The environment section is always 1, so
      scenario sections conventionally start at 2.
    - *throughput*: ``"latency"`` tables only — add an amortized throughput
      column.
    - *noun_zh* / *noun_en*: ``"latency"`` conclusions only — the operation
      noun woven into the "单并发 …延迟" / "Single-concurrency … latency" bullets.
    - *star*: append a ``⭐`` to the heading (highlights a flagship scenario).
    - *charts*: zero or more ``ReportChart`` for the HTML report. Empty for
      scenarios whose data shape is not a concurrency sweep (density / dirty /
      clone), so they appear in Markdown but contribute no HTML chart.
    """
    table: str
    title_zh: str
    title_en: str
    method_zh: str = ""
    method_en: str = ""
    order: float = 100.0
    throughput: bool = False
    noun_zh: str = ""
    noun_en: str = ""
    star: bool = False
    charts: "tuple[ReportChart, ...]" = ()


def benchmark(
    key: str,
    *,
    aliases: "list[str] | None" = None,
    opt_in_env: "str | None" = None,
    opt_out_env: "str | None" = None,
    skip_reason: "str | None" = None,
    available: bool = True,
    report: "ReportSection | ReportChart | list | None" = None,
):
    """Register the decorated function as a benchmark under *key*.

    Every knob keeps a scenario's metadata in one place:

    - *aliases*: friendly group names (several benchmarks may share one, e.g.
      every ``volume-*`` scenario under ``volume``).
    - *opt_in_env*: env var that must equal ``"1"`` for the scenario to run;
      otherwise it is skipped (default-off scenarios like volume / ivshmem).
      Explicitly naming the scenario on the CLI (``--only``/``--scenarios``)
      bypasses this gate — see ``_FORCED_KEYS`` / ``select_benchmarks``.
    - *opt_out_env*: env var that, when equal to ``"1"``, skips the scenario
      (default-on scenarios like density / snapshot-dirty). Also bypassed when
      the scenario is explicitly named on the CLI.
    - *skip_reason*: extra human hint appended to the opt-in skip message.
    - *available*: evaluated at import time; ``False`` skips unconditionally
      (e.g. the optional ``Volume`` type failed to import).
    - *report*: the scenario's report presence. Pass a ``ReportSection`` to
      declare a Markdown section (which may itself carry HTML ``charts``), a
      bare ``ReportChart``/``ReportGroup`` (legacy) for an HTML-only chart, or
      a list mixing the two.
    """
    def deco(fn: "Callable[[Config], None]") -> "Callable[[Config], None]":
        if key in BENCHMARK_REGISTRY:
            raise ValueError(f"duplicate benchmark key: {key}")

        # Wrap only when a gate is declared, so plain scenarios stay zero-cost.
        if not available or opt_in_env or opt_out_env:
            @functools.wraps(fn)
            def registered(cfg: Config) -> None:
                if not available:
                    skip(key, "requires an optional dependency missing from this build")
                    return
                # Explicit CLI selection is a hard "run it" signal: it overrides
                # the default opt-in/opt-out env gates (but never the missing
                # optional-dependency guard above).
                forced = key in _FORCED_KEYS
                if not forced and opt_in_env and os.environ.get(opt_in_env) != "1":
                    reason = f"set {opt_in_env}=1 (or name it via --only {key})"
                    if skip_reason:
                        reason += f" ({skip_reason})"
                    skip(key, reason)
                    return
                if not forced and opt_out_env and os.environ.get(opt_out_env) == "1":
                    skip(key, f"{opt_out_env}=1")
                    return
                fn(cfg)
        else:
            registered = fn

        BENCHMARK_REGISTRY[key] = registered
        for alias in aliases or []:
            BENCHMARK_ALIASES.setdefault(alias, []).append(key)
        _register_report(key, report)
        return registered
    return deco


def _register_report(
    key: str, report: "ReportSection | ReportChart | list | None"
) -> None:
    """Populate ``REPORT_SECTIONS`` / ``REPORT_SCENARIOS`` from a ``report=``.

    A ``ReportSection`` contributes one Markdown section plus its ``charts``;
    a bare ``ReportChart``/``ReportGroup`` contributes an HTML-only chart. The
    section's data key defaults to the benchmark *key*, and each chart's
    ``prefix`` defaults to it too.
    """
    items = report if isinstance(report, (list, tuple)) else ([report] if report is not None else [])
    for item in items:
        if isinstance(item, ReportSection):
            REPORT_SECTIONS.append({
                "key": key,
                "table": item.table,
                "title_zh": item.title_zh,
                "title_en": item.title_en,
                "method_zh": item.method_zh,
                "method_en": item.method_en,
                "order": item.order,
                "throughput": item.throughput,
                "noun_zh": item.noun_zh,
                "noun_en": item.noun_en,
                "star": item.star,
            })
            charts = item.charts
        elif isinstance(item, ReportChart):
            charts = (item,)
        else:
            charts = ()
        for grp in charts:
            prefix = grp.prefix or key
            REPORT_SCENARIOS.append({
                "id": prefix.replace("-", "_"),
                "title": grp.title,
                "prefix": prefix,
                "xKey": grp.x_key,
                "fallback": list(grp.fallback),
                "xLabel": grp.x_label,
            })


def parallel_sweep(
    label: str,
    *,
    header: "str | None" = None,
    levels: "list[int] | None" = None,
    metrics: "tuple[str, ...] | None" = None,
    rounds: "int | None" = None,
    warmup: "int | None" = None,
    settle: "float | None" = None,
):
    """Declaratively turn a per-level generator into a concurrency-sweep benchmark.

    Instead of hand-writing the ``for concurrency in CONCURRENCY_LEVELS`` loop
    (with its ``n = PERF_ROUNDS * concurrency`` sizing, ``measure_parallel``
    call, result append, and stats print-out) in every scenario, decorate a
    *generator* ``(cfg, concurrency, n)`` that:

      * sets up the level's resources,
      * ``yield``s the single-shot operation to be timed ``n`` times, then
      * tears the resources down after the yield (put it in a ``finally`` /
        lean on ``sandbox_pool()`` so cleanup runs even mid-sweep).

    The framework owns the loop, timing, collection, and the shared stats line;
    the scenario body only declares "what to set up, what to measure, what to
    clean up". Stack it *under* ``@benchmark`` so the registry still sees the
    required ``fn(cfg)``::

        @benchmark("template-create", aliases=["create"])
        @parallel_sweep("template-create", header=" [Perf] ...")
        def bench(cfg, concurrency, n):
            with sandbox_pool() as pool:
                yield lambda: pool.add(Sandbox.create(cfg.template_id, config=cfg))

    *header* prints once above the sweep; *levels* overrides the default
    ``CONCURRENCY_LEVELS`` for scenarios that need a bespoke set; *metrics*
    picks which latency fields the shared stats line shows (see
    ``print_parallel_stats``).

    Timing knobs (each ``None`` falls back to its global default, so a scenario
    only names the ones it wants to bend):

    - *rounds*: measured ops **per worker** (total ``n = rounds * concurrency``);
      defaults to ``PERF_ROUNDS`` (env ``CUBE_PERF_ROUNDS``).
    - *warmup*: unmeasured ops run once per level before timing, to shed
      cold-start spikes; defaults to ``PERF_WARMUP`` (env ``CUBE_PERF_WARMUP``).
    - *settle*: seconds slept **between** concurrency levels to let the node
      quiesce; defaults to ``PERF_SETTLE`` (env ``CUBE_PERF_SETTLE``).
    """
    def deco(gen: "Callable[..., Iterator[Callable[[], Any]]]") -> "Callable[[Config], None]":
        make_level = contextmanager(gen)
        # Let a stacked ``@metrics(...)`` supply the default field list; an
        # explicit ``metrics=`` on this decorator always wins.
        effective_metrics = metrics if metrics is not None else getattr(gen, "_perf_metrics", None)

        @functools.wraps(gen)
        def run(cfg: Config) -> None:
            if header:
                print(f"\n{'='*60}")
                print(header)
                print(f"{'='*60}")
            rounds_n = PERF_ROUNDS if rounds is None else rounds
            warmup_n = PERF_WARMUP if warmup is None else warmup
            settle_s = PERF_SETTLE if settle is None else settle
            for i, concurrency in enumerate(
                _resolve_levels(label, levels or CONCURRENCY_LEVELS, concurrency_key)
            ):
                # Let the node quiesce between levels (skip before the first).
                if i and settle_s:
                    time.sleep(settle_s)
                n = rounds_n * concurrency
                with make_level(cfg, concurrency, n) as op:
                    # Shed cold-start spikes: run a few unmeasured ops first.
                    warmup_errors = 0
                    for _ in range(warmup_n):
                        try:
                            op()
                        except Exception:
                            warmup_errors += 1
                    if warmup_errors:
                        print(yellow(
                            f"  warmup: {warmup_errors}/{warmup_n} ops failed "
                            f"(concurrency={concurrency})"))
                    result = measure_parallel(
                        f"{label}-c{concurrency}", op, n=n, concurrency=concurrency)
                    PERF_RESULTS.append(result)
                    print_parallel_stats(result, effective_metrics)

        return run
    return deco


def sandbox_action(
    *,
    pool: "Callable[..., Any] | None" = None,
    fixture: str = "sandbox",
    template_id: "str | None" = None,
    **create_opts: Any,
):
    """Business layer — turn a *plain action* into a ``parallel_sweep`` generator.

    This is the "业务" decorator of the four-layer split (see the module's
    ``sandbox_benchmark`` docstring / ``DESIGN.zh.md`` §4.6). It removes the
    ``yield`` boilerplate: decorate a function that **is** the per-op action and
    get back the ``gen(cfg, concurrency, n)`` generator ``parallel_sweep``
    expects.

    *fixture* picks what each measured op is handed (built by the matching
    ``*_op`` helper in ``runner``):

    - ``"sandbox"`` (default): a throwaway sandbox — action signature ``(sb)``,
      or ``(sb, pool)`` when *pool* is given (feed the product to ``pool.add``)::

          @sandbox_action(pool=snapshot_pool)
          def snapshot_create(sb, snaps):
              snaps.add(sb.create_snapshot().snapshot_id)

    - ``"snapshot"``: a throwaway sandbox *plus* one of its snapshots — action
      signature ``(sb, snap_id)``, or ``(sb, snap_id, pool)`` with *pool*. Suits
      ops that need a live box and a snapshot to act on (e.g. rollback)::

          @sandbox_action(fixture="snapshot")
          def rollback(sb, snap_id):
              sb.rollback(snap_id)

    *template_id* / ``**create_opts`` pass straight to the fixture /
    ``Sandbox.create`` (e.g. ``timeout=300``, ``volume_mounts=[...]``).
    """
    op_builder = snapshot_op if fixture == "snapshot" else sandbox_op

    def deco(action: "Callable[..., Any]") -> "Callable[..., Iterator[Callable[[], Any]]]":
        @functools.wraps(action)
        def gen(cfg: Config, concurrency: int, n: int):
            if pool is None:
                yield op_builder(cfg, action, template_id, **create_opts)
            elif fixture == "snapshot":
                with _open_pool(pool, cfg) as p:
                    yield op_builder(
                        cfg, lambda sb, sid: action(sb, sid, p), template_id, **create_opts)
            else:
                with _open_pool(pool, cfg) as p:
                    yield op_builder(
                        cfg, lambda sb: action(sb, p), template_id, **create_opts)

        return gen
    return deco


def metrics(*names: str):
    """Metrics layer — declare which latency fields the stats line shows.

    The "指标" decorator of the four-layer split: it just tags the generator
    with a ``_perf_metrics`` attribute that ``parallel_sweep`` reads as its
    default field list (an explicit ``metrics=`` on ``parallel_sweep`` wins).
    Valid names: ``avg`` / ``min`` / ``p50`` / ``p95`` / ``p99`` / ``max`` (see
    ``print_parallel_stats``). Stack it between ``@parallel_sweep`` and
    ``@sandbox_action``::

        @parallel_sweep("snapshot-create")
        @metrics("avg", "p50", "p95", "max")
        @sandbox_action(pool=snapshot_pool)
        def snapshot_create(sb, snaps): ...
    """
    def deco(gen: "Callable[..., Any]") -> "Callable[..., Any]":
        gen._perf_metrics = tuple(names)  # type: ignore[attr-defined]
        return gen
    return deco


def sandbox_benchmark(
    key: str,
    *,
    title: "str | None" = None,
    header: "str | None" = None,
    aliases: "list[str] | None" = None,
    pool: "Callable[..., Any] | None" = None,
    fixture: str = "sandbox",
    metrics: "tuple[str, ...] | None" = None,
    template_id: "str | None" = None,
    levels: "list[int] | None" = None,
    rounds: "int | None" = None,
    warmup: "int | None" = None,
    settle: "float | None" = None,
    report: "ReportSection | ReportChart | None" = None,
    **create_opts: Any,
):
    """Fully declarative sandbox scenario — decorate the *action*, get the sweep.

    This is the one-line **sugar** that stacks the four single-concern layers of
    the split (see ``DESIGN.zh.md`` §4.6) for the most common shape: *"each
    measured op spins up a throwaway fixture, does one thing to it, tears it
    down"*. There is no generator and no ``yield`` — the decorated function
    **is** the per-op action, so a scenario collapses to its one measured line::

        @sandbox_benchmark("snapshot-create", title="创建快照（并发）",
                           header=" [Perf] Snapshot Creation", aliases=["snapshot"],
                           pool=snapshot_pool, metrics=("avg", "p50", "p95", "max"),
                           levels=[1, 5, 10], rounds=20, warmup=2, settle=1.0)
        def snapshot_create(sb, snaps):
            snaps.add(sb.create_snapshot().snapshot_id)

    Internally it is exactly::

        benchmark(key, aliases=aliases, report=...)(
            parallel_sweep(key, header=..., levels=..., metrics=...,
                           rounds=..., warmup=..., settle=...)(
                sandbox_action(pool=pool, fixture=fixture,
                               template_id=template_id, **create_opts)(action)))

    Reach for the four decorators directly (``@benchmark`` / ``@parallel_sweep``
    / ``@metrics`` / ``@sandbox_action``) when you want to split concerns, reuse
    a middle layer, or swap one layer's implementation.

    - *title*: HTML report chart title (omit for no report group).
    - *fixture*: what each measured op is handed — ``"sandbox"`` (default,
      action ``(sb)`` / ``(sb, pool)``) or ``"snapshot"`` (a box *and* one of its
      snapshots, action ``(sb, snap_id)`` / ``(sb, snap_id, pool)``). See
      ``sandbox_action``.
    - *pool*: a pool context-manager factory (``sandbox_pool`` / ``snapshot_pool``);
      when set, the action takes a trailing ``pool`` arg and typically feeds its
      product to ``pool.add(...)``.
    - *metrics*: latency fields the stats line shows (see ``print_parallel_stats``).
    - *levels*: concurrency ladder for this scenario; defaults to the global
      ``CONCURRENCY_LEVELS`` (env ``CUBE_PERF_CONCURRENCY``, default 1/5/10).
    - *rounds* / *warmup* / *settle*: per-scenario timing overrides; each
      ``None`` falls back to its global default (``PERF_ROUNDS`` /
      ``PERF_WARMUP`` / ``PERF_SETTLE`` — see ``parallel_sweep``).
    - *template_id* / ``**create_opts``: passed straight to the fixture /
      ``Sandbox.create`` (e.g. a bespoke template, ``timeout=300``,
      ``volume_mounts=[...]``).
    """
    report_group = report or (ReportGroup(title) if title else None)

    def deco(action: "Callable[..., Any]") -> "Callable[[Config], None]":
        gen = sandbox_action(
            pool=pool, fixture=fixture, template_id=template_id, **create_opts)(action)
        swept = parallel_sweep(
            key, header=header, levels=levels, metrics=metrics,
            rounds=rounds, warmup=warmup, settle=settle)(gen)
        return benchmark(key, aliases=aliases, report=report_group)(swept)

    return deco


# ---------------------------------------------------------------------------
# Quick‑start helper: declare a scenario with the fewest imports possible.
# ---------------------------------------------------------------------------
#
#    from framework.registry import perf_test, sandbox_op
#
#    @perf_test("my-scenario", title="My Scenario", levels=(1, 5, 10))
#    def bench(cfg, concurrency, n):
#        yield sandbox_op(cfg, lambda sb: sb.exec("whoami"))
#
# Without *levels* the body runs once, no sweep:
#
#    @perf_test("once-off")
#    def bench(cfg):
#        with sandbox(cfg) as sb:
#            print(sb.exec("whoami"))
# ---------------------------------------------------------------------------


def perf_test(
    key: str,
    title: str = "",
    *,
    aliases: "list[str] | None" = None,
    levels: "list[int] | None" = None,
    concurrency_key: str = "CONCURRENCY_LEVELS",
    warmup: "int | None" = None,
    rounds: "int | None" = None,
    metrics: "tuple[str, ...] | None" = None,
    header: "str | None" = None,
):
    """Minimal‑boilerplate entry point for a new benchmark.

    Put a ``bench_<name>.py`` under ``cases/<category>/`` and decorate a
    standalone function.  Three lines of imports cover 90 % of scenarios.

    - If *levels* is given the decorated function receives three positional
      args ``(cfg, concurrency, n)`` and is expected to **yield** a callable
      (e.g. ``yield sandbox_op(cfg, ...)`` or ``yield snapshot_op(cfg, ...)``).
    - If *levels* is omitted the function gets ``(cfg)`` and runs once — use
      this for density / snapshot‑dirty / anything that does not need
      concurrency sweeps.
    """
    report = ReportGroup(title) if title else None
    header = header or f" [Perf] {title or key.capitalize()}"

    def deco(fn: "Callable[..., Any]") -> "Callable[[Config], None]":
        if levels is not None:
            swept = parallel_sweep(
                key, header=header, levels=levels,
                concurrency_key=concurrency_key,
                warmup=warmup, rounds=rounds, metrics=metrics)(fn)
            return benchmark(key, aliases=aliases, report=report)(swept)
        else:
            # Single‑shot benchmark without concurrency sweep
            @benchmark(key, aliases=aliases, report=report)
            def _fn(cfg: Config) -> None:
                fn(cfg)
            return _fn

    return deco


# ---------------------------------------------------------------------------
# Auto‑register a bare ``def run():`` (no decorator needed)
# ---------------------------------------------------------------------------


def auto(
    fn: "Callable[[], Any]",
    *,
    key: str,
    title: str = "",
    levels: "tuple[int, ...] | None" = None,
    warmup: int = 1,
    metrics: "tuple[str, ...] | None" = None,
    header: "str | None" = None,
) -> None:
    """Wrap a plain ``run()`` function so the framework does all the timing.

    Called automatically by ``perf.cases`` discovery when a ``bench_*.py``
    module exposes a callable named ``run`` and does *not* use ``@benchmark``.
    You never need to call this yourself — just write::

        # bench_my.py
        '''My benchmark.'''       # docstring line 1 → report title
        LEVELS = (1, 5, 10)      # optional concurrency ladder

        def run():
            ...

    Under the hood this registers a ``@benchmark`` + ``@parallel_sweep`` entry
    so the scenario appears in ``--list-scenarios`` and participates in
    reports just like any other benchmark.
    """
    from .runner import PERF_RESULTS, measure_parallel, print_parallel_stats  # noqa: PLC0415
    from .config import CONCURRENCY_LEVELS  # noqa: PLC0415

    _levels = _resolve_levels(key, levels or CONCURRENCY_LEVELS)
    report = ReportGroup(title) if title else None
    header = header or f" [Perf] {title or key.capitalize()}"
    _metrics = metrics or ("avg", "min", "p95", "max")

    @benchmark(key, aliases=None, report=report)
    @parallel_sweep(key, header=header, levels=_levels, warmup=warmup, metrics=_metrics)
    def _auto_bench(
        cfg: Config, concurrency: int, n: int,
        _fn: "Callable[[], Any]" = fn,
    ) -> None:
        # warmup
        for _ in range(warmup):
            try:
                _fn()
            except Exception:
                pass
        # measure
        result = measure_parallel(
            f"{key}-c{concurrency}", _fn, n=n, concurrency=concurrency,
        )
        PERF_RESULTS.append(result)
        print_parallel_stats(result, _metrics)


# ---------------------------------------------------------------------------
# External-script registration (CUBE_EXTERNAL_SCRIPTS in .env)
# ---------------------------------------------------------------------------


def discover_external_scripts() -> None:
    """Auto-discover and register external scripts.

    Paths listed in ``CUBE_EXTERNAL_SCRIPTS`` (comma-separated in .env).
    """
    import os as _os
    import re
    from pathlib import Path as _Path

    candidates: list[_Path] = []

    raw = _os.environ.get("CUBE_EXTERNAL_SCRIPTS", "").strip()
    if not raw:
        print("[perf] CUBE_EXTERNAL_SCRIPTS is not set — no external scripts to register")
        print("[perf]   cp tests/perf/.env.example tests/perf/.env")
        print("[perf]   then edit tests/perf/.env and uncomment CUBE_EXTERNAL_SCRIPTS")
        return

    for p in raw.split(","):
        p = p.strip()
        if p:
            candidates.append(_Path(p).expanduser().resolve())

    registered_keys = set(BENCHMARK_REGISTRY)

    for p in candidates:
        if not p.is_file():
            continue
        key = p.stem.replace("bench_", "").replace("_", "-")
        if key in registered_keys:
            continue

        levels = None
        title = ""
        try:
            source = p.read_text(encoding="utf-8")
            m = re.search(r"^LEVELS\s*=\s*[\[(]([^)\]]+)[)\]]", source, re.MULTILINE)
            if m:
                levels = tuple(
                    int(x.strip()) for x in m.group(1).split(",") if x.strip()
                )
            for line in source.split("\n"):
                stripped = line.strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    title = stripped.strip('"\'').strip()
                    break
                if stripped.startswith("#"):
                    title = stripped.lstrip("#").strip()
                    break
        except Exception:
            pass

        # Detect scripts that don't accept -c / --concurrency (e.g.
        # bench_snapshot_dirty.py which uses -d DIRTY_MB instead).
        has_concurrency = bool(
            re.search(r'add_argument\("?-c"', source)
            or re.search(r'add_argument\("?--concurrency"', source)
        )

        register_external(key, str(p), title=title, levels=levels,
                          no_concurrency=not has_concurrency)
        registered_keys.add(key)

    if candidates:
        print(f"[perf] registered {len(candidates)} external script(s)")
    else:
        print("[perf] no external scripts found — nothing registered")


def register_external(
    key: str,
    path: str,
    *,
    title: str = "",
    levels: "tuple[int, ...] | None" = None,
    rounds: int = 5,
    metrics: "tuple[str, ...] | None" = None,
    timeout: int = 300,
    no_concurrency: bool = False,  # True → run once without -c / -n
) -> None:
    """Register a decoupled external ``.py`` script as a perf scenario.

    **Convention for script authors**
        The script MUST accept ``-c <N>`` (concurrency) and ``-n <N>``
        (op count).  ``--rounds <N>`` and ``--no-header`` are passed
        additionally — add them as optional flags.  That's the entire
        contract — the script owns its business logic; the framework only
        handles execution scheduling and statistics collection.

        Example::

            # bench_clone.py
            '''Clone concurrency benchmark.'''

            import argparse
            ap = argparse.ArgumentParser()
            ap.add_argument("-c", type=int, default=1)
            ap.add_argument("-n", type=int, default=5)
            ap.add_argument("--rounds", type=int, default=3)
            ap.add_argument("--no-header", action="store_true")
            args = ap.parse_args()

            from cubesandbox import Sandbox
            sb = Sandbox.create("tpl-xxx")
            sb.clone(n=args.n, concurrency=args.c)
            sb.kill()

    The framework sweeps *levels*, calling the script once per level::

        python bench_xxx.py -c <level> -n <rounds> --rounds <rounds> --no-header

    Each invocation's wall‑clock time is recorded as a single data point.
    """
    import subprocess
    import sys as _sys
    import time as _time

    from .runner import PerfSample, PerfResult, PERF_RESULTS, print_parallel_stats, red, yellow

    _script_path = path
    _levels = _resolve_levels(key, levels or CONCURRENCY_LEVELS)
    _rounds = rounds or PERF_ROUNDS

    # Build report metadata once — both Markdown (ReportSection) and HTML
    # (ReportGroup) read from the same decorator-declared metadata.
    _section_title = title or key.capitalize()
    _section = ReportSection(
        table="dirty" if no_concurrency else "latency",
        title_zh=_section_title,
        title_en=_section_title,
        order=100.0 + len(REPORT_SECTIONS),
    )
    _chart = ReportGroup(_section_title)
    header = f" [Perf] {_section_title}"

    _metrics = metrics or ("avg", "min", "p95", "max")

    @benchmark(key, aliases=None, report=[_section, _chart])
    def _bench(cfg: Config) -> None:
        print(f"\n{'=' * 60}")
        print(f"{header:^60}")
        print(f"{'=' * 60}")

        if no_concurrency:
            # Single run — the script manages its own parameters
            # (e.g. bench_snapshot_dirty.py with -d DIRTY_MB).
            cmd = [_sys.executable, _script_path, "--no-header"]
            t0 = _time.time()
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                wall = (_time.time() - t0) * 1000
                result = PerfResult(
                    scenario=key,
                    samples=[PerfSample(label="", latency_ms=wall)],
                )
                result.samples[0].extra["error"] = "TIMEOUT"
                PERF_RESULTS.append(result)
                print(f"  TIMEOUT after {wall:.0f}ms")
                return

            wall = (_time.time() - t0) * 1000
            if proc.returncode != 0:
                err = (proc.stderr or "").strip()[:1000]
                result = PerfResult(
                    scenario=key,
                    samples=[PerfSample(label="", latency_ms=wall)],
                )
                result.samples[0].extra["error"] = f"rc={proc.returncode}: {err}"
                PERF_RESULTS.append(result)
                print(f"  wall={wall:.0f}ms {yellow(f'ERR(rc={proc.returncode})')}")
                if err:
                    for line in err.split("\n"):
                        print(f"    {red(line)}")
            else:
                result = PerfResult(
                    scenario=key,
                    samples=[PerfSample(label="", latency_ms=wall)],
                )
                PERF_RESULTS.append(result)
                print(f"  wall={wall:.0f}ms")
            _post_concurrency_cleanup(key, 1)
            print(f"{'=' * 60}\n")
            return

        # Concurrency-sweep path
        for c in _levels:
            cmd = [
                _sys.executable, _script_path,
                "-c", str(c), "-n", str(_rounds),
                "--rounds", str(_rounds), "--no-header",
            ]
            t0 = _time.time()
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                wall = (_time.time() - t0) * 1000
                result = PerfResult(
                    scenario=key,
                    samples=[PerfSample(label="", latency_ms=wall)],
                )
                result.samples[0].extra["error"] = "TIMEOUT"
                PERF_RESULTS.append(result)
                print(f"  concurrency={c:>2}: TIMEOUT after {wall:.0f}ms")
                continue

            wall = (_time.time() - t0) * 1000
            per_ms = wall / _rounds if _rounds else 0
            scenario_key = f"{key}-c{c}"
            if proc.returncode != 0:
                err = (proc.stderr or "").strip()[:1000]
                result = PerfResult(
                    scenario=scenario_key,
                    samples=[PerfSample(label="", latency_ms=wall, extra={"wall_ms": wall, "per_ms": per_ms})],
                )
                result.samples[0].extra["error"] = f"rc={proc.returncode}: {(proc.stderr or '').strip()[:1000]}"
                PERF_RESULTS.append(result)
                print(
                    f"  concurrency={c:>2}: wall={wall:.0f}ms "
                    f"{yellow(f'ERR(rc={proc.returncode})')}"
                )
                if err:
                    for line in err.split("\n"):
                        print(f"    {red(line)}")
            else:
                # Parse script stdout for detailed metrics.
                # Format: "avg=Xms min=Xms p95=Xms max=Xms wall=Xms per=Xms"
                stdout = (proc.stdout or "").strip()
                parsed = _parse_bench_stdout(stdout) if stdout else {}
                extra = {"wall_ms": wall, "per_ms": per_ms}
                extra.update({k: v for k, v in parsed.items() if k not in ("wall_ms", "per_ms")})

                sample = PerfSample(label="", latency_ms=parsed.get("avg_ms", wall), extra=extra)
                result = PerfResult(scenario=scenario_key, samples=[sample])
                PERF_RESULTS.append(result)

                if parsed:
                    print(f"  concurrency={c:>2}: avg={parsed['avg_ms']:.1f}ms "
                          f"p95={parsed['p95_ms']:.1f}ms max={parsed['max_ms']:.1f}ms")
                else:
                    print(f"  concurrency={c:>2}: wall={wall:.0f}ms")
            _post_concurrency_cleanup(scenario_key, c)
        print(f"{'=' * 60}\n")


_STDOUT_PARSE_RE = re.compile(
    r"avg=(?P<avg>[0-9.]+)ms\s+min=(?P<min>[0-9.]+)ms\s+p95=(?P<p95>[0-9.]+)ms\s+max=(?P<max>[0-9.]+)ms"
)


def _parse_bench_stdout(stdout: str) -> dict[str, float]:
    """Parse bench script stdout. Returns ``{}`` when the output format
    is not recognised (fallback: wall-clock measurement)."""
    m = _STDOUT_PARSE_RE.search(stdout)
    if not m:
        return {}
    return {
        "avg_ms": float(m.group("avg")),
        "min_ms": float(m.group("min")),
        "p95_ms": float(m.group("p95")),
        "max_ms": float(m.group("max")),
    }


def _post_concurrency_cleanup(name: str, concurrency: int) -> None:
    """每档并发跑完后清理残留快照（懒加载，避免循环 import）。

    由 ``_bench()`` 在每轮 ``for c in _levels`` 末尾调用。此函数是
    ``ops.cleanup.post_concurrency_cleanup`` 的薄包装，import 只在首次
    调用时发生，不会拖慢冷启动。
    """
    if not _is_auto_cleanup_enabled():
        return
    from ..ops.cleanup import post_concurrency_cleanup
    post_concurrency_cleanup(f"{name}/c={concurrency}")


def _is_auto_cleanup_enabled() -> bool:
    return os.environ.get("CUBE_PERF_AUTO_CLEANUP", "1") != "0"


def _resolve_levels(
    label: str,
    default_levels: "tuple[int, ...]",
    concurrency_key: str = "CONCURRENCY_LEVELS",
) -> "tuple[int, ...]":
    """Per‑scenario concurrency override via ``CUBE_<LABEL>_CONCURRENCY``.

    e.g. ``CUBE_CLONE_CONCURRENCY=1,5,10`` overrides the global default for the
    clone scenario while other scenarios keep their global ladders.
    """
    import os as _os

    env = _os.environ.get(f"CUBE_{label.upper().replace('-', '_')}_CONCURRENCY")
    if env:
        try:
            return tuple(int(x.strip()) for x in env.split(",") if x.strip())
        except Exception:
            pass
    return default_levels


def _open_pool(factory: "Callable[..., Any]", cfg: Config) -> Any:
    """Open a pool context manager, passing *cfg* only if the factory takes it.

    Bridges the two pool shapes: ``sandbox_pool()`` (no args) and
    ``snapshot_pool(cfg)`` (needs the config to delete snapshots).
    """
    if inspect.signature(factory).parameters:
        return factory(cfg)
    return factory()


# ---------------------------------------------------------------------------
# Scenario selection
# ---------------------------------------------------------------------------
#
# The registry / aliases / report groups are all populated by the
# ``@benchmark`` decorator as ``cases/`` modules import, so this section only
# derives the run list and the report-scenario view — there is nothing to
# hand-maintain here.


def default_report_scenarios() -> "list[dict]":
    """Default HTML-report chart/table groups, derived from ``@benchmark(report=...)``.

    ``report_html`` consumes this so a charted scenario is declared in the
    same one-liner that registers the benchmark (see ``ReportChart``).
    """
    return [dict(g) for g in REPORT_SCENARIOS]


def default_report_sections() -> "list[dict]":
    """Default Markdown-report sections, derived from ``@benchmark(report=...)``.

    ``reporting.report`` consumes this so a scenario's Markdown section (title,
    "测试方式" note, table type, throughput/conclusion knobs) is declared in the
    same one-liner that registers the benchmark (see ``ReportSection``).
    Returned sorted by ``order`` so the caller can render / number them
    directly; a stable secondary sort keeps declaration order for ties.
    """
    return sorted((dict(s) for s in REPORT_SECTIONS), key=lambda s: s["order"])


def available_scenarios() -> "list[str]":
    """Return the canonical scenario keys plus aliases, for CLI help / listing."""
    return list(BENCHMARK_REGISTRY.keys()) + list(BENCHMARK_ALIASES.keys())


def select_benchmarks(selected: "list[str] | None") -> "list[Callable[[Config], None]]":
    """Resolve scenario keys/aliases into an ordered, de-duplicated bench list.

    *selected* is a list of scenario keys or aliases (case-insensitive; a
    leading ``no-``/``skip-`` prefix excludes that scenario). When it is
    ``None`` / empty, the full suite is returned. Unknown tokens raise
    ``ValueError`` listing the valid choices.

    Side effect: rebuilds ``_FORCED_KEYS`` with the scenarios explicitly named
    here (excluding the ``all`` wildcard), so a named default-off/-on scenario
    bypasses its opt-in/opt-out env gate at run time.
    """
    _FORCED_KEYS.clear()
    if not selected:
        return list(BENCHMARK_REGISTRY.values())

    def _expand(key: str) -> "list[str]":
        if key == "all":  # derived alias for the whole suite
            return list(BENCHMARK_REGISTRY)
        if key in BENCHMARK_ALIASES:
            return list(BENCHMARK_ALIASES[key])
        if key in BENCHMARK_REGISTRY:
            return [key]
        return []

    include: "set[str]" = set()
    exclude: "set[str]" = set()
    unknown: "list[str]" = []
    for raw in selected:
        token = raw.strip().lower()
        if not token:
            continue
        negate = False
        for prefix in ("no-", "skip-", "!", "^"):
            if token.startswith(prefix):
                negate, token = True, token[len(prefix):]
                break
        keys = _expand(token)
        if not keys:
            unknown.append(raw)
            continue
        if negate:
            exclude.update(keys)
        else:
            include.update(keys)
            # ``all`` is a bulk wildcard, not an explicit pick — it must not
            # force default-off scenarios (volume / ivshmem) on.
            if token != "all":
                _FORCED_KEYS.update(keys)

    if unknown:
        valid = ", ".join(sorted(set(BENCHMARK_REGISTRY) | set(BENCHMARK_ALIASES) | {"all"}))
        raise ValueError(
            f"unknown scenario(s): {', '.join(unknown)}\n  valid choices: {valid}"
        )

    # If only exclusions were given, start from the full set.
    chosen = (include or set(BENCHMARK_REGISTRY)) - exclude
    # Preserve the canonical registry order.
    return [fn for key, fn in BENCHMARK_REGISTRY.items() if key in chosen]


def collect_component_versions(cfg: Config) -> dict[str, str]:
    """Collect component version info for the HTML report environment section.

    Queries CubeAPI health endpoint and local system for component versions.
    """
    versions: dict[str, str] = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }

    # Try to get CubeAPI version from health endpoint
    try:
        import httpx

        headers = {}
        api_key = os.environ.get("CUBE_API_KEY") or os.environ.get("E2B_API_KEY", "")
        if api_key:
            headers["X-API-Key"] = api_key
        resp = httpx.get(f"{cfg.api_url}/health", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                for key in ("version", "commit", "build_time", "go_version"):
                    if key in data:
                        versions[f"cubeapi_{key}"] = str(data[key])
    except Exception:
        pass

    # Try to get SDK version
    try:
        import cubesandbox

        versions["sdk_version"] = cubesandbox.__version__
    except Exception:
        pass

    return versions


def run_all(cfg: Config, selected: "list[str] | None" = None) -> None:
    """Run performance benchmarks in order.

    *selected* is an optional list of scenario keys/aliases (see
    ``BENCHMARK_REGISTRY`` / ``BENCHMARK_ALIASES``). When None/empty, the full
    suite runs. Otherwise only the resolved subset runs.

    Requires the ``perf.cases`` package to have been imported already so the
    registry is populated (the CLI entry point does this).
    """
    benches = select_benchmarks(selected)

    # Print component versions
    versions = collect_component_versions(cfg)
    print("\n--- Component Versions ---")
    for k, v in sorted(versions.items()):
        print(f"  {k}: {v}")
    print()

    if selected:
        keys = [k for k, fn in BENCHMARK_REGISTRY.items() if fn in benches]
        print(f"--- Selected scenarios ({len(benches)}): {', '.join(keys)} ---\n")

    for bench_fn in benches:
        bench_fn(cfg)
