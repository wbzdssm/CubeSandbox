# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Test runner primitives: counters, assertions, and perf measurement helpers.

Holds the process-wide mutable state (pass/fail/skip counters, collected
results) that `functional.py`, `report.py`, and the sibling `tests/perf/`
benchmark package all read/write.
"""

from __future__ import annotations

import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from cubesandbox import Config, Sandbox

try:
    from cubesandbox import Volume
except ImportError:
    Volume = None  # type: ignore[assignment]

# ===========================================================================
# Data classes
# ===========================================================================


@dataclass
class PerfSample:
    """A single performance measurement."""

    label: str
    latency_ms: float
    concurrency: int = 1
    extra: dict = field(default_factory=dict)


@dataclass
class PerfResult:
    """Aggregated performance result for a scenario."""

    scenario: str
    samples: list[PerfSample] = field(default_factory=list)

    @property
    def errored(self) -> int:
        """Number of samples that failed with an exception."""
        return sum(1 for s in self.samples if s.extra.get("error"))

    @property
    def latencies(self) -> list[float]:
        """Valid latencies only — errored samples are excluded from stats."""
        return [s.latency_ms for s in self.samples if not s.extra.get("error")]

    @property
    def avg(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0

    @property
    def min(self) -> float:
        return min(self.latencies) if self.latencies else 0

    @property
    def p50(self) -> float:
        return self._percentile(50)

    @property
    def p95(self) -> float:
        return self._percentile(95)

    @property
    def p99(self) -> float:
        return self._percentile(99)

    @property
    def max(self) -> float:
        return max(self.latencies) if self.latencies else 0

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def concurrency(self) -> int:
        return self.samples[0].concurrency if self.samples else 1

    def _percentile(self, p: float) -> float:
        return percentile(self.latencies, p)


def percentile(values: list[float], p: float) -> float:
    """Compute the *p*-th percentile of *values* (0-100)."""
    if not values:
        return 0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_vals):
        return sorted_vals[f] + c * (sorted_vals[f + 1] - sorted_vals[f])
    return sorted_vals[f]


# ===========================================================================
# Global state
# ===========================================================================

PASS = 0
FAIL = 0
SKIP = 0

PERF_RESULTS: list[PerfResult] = []
RESULTS: list[dict[str, Any]] = []
_CURRENT_SECTION: str = ""


def reset() -> None:
    """Reset all module-level counters/results (useful for repeated runs/tests)."""
    global PASS, FAIL, SKIP, _CURRENT_SECTION
    PASS = 0
    FAIL = 0
    SKIP = 0
    _CURRENT_SECTION = ""
    PERF_RESULTS.clear()
    RESULTS.clear()


# ===========================================================================
# Console colors
# ===========================================================================


def green(s: str) -> str:
    return f"\033[32m{s}\033[0m"


def red(s: str) -> str:
    return f"\033[31m{s}\033[0m"


def yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m"


# ===========================================================================
# Section / assertion helpers
# ===========================================================================


def section(num: int, title: str) -> None:
    """Print a section header and remember it for report grouping."""
    global _CURRENT_SECTION
    _CURRENT_SECTION = f"[{num}] {title}"
    print(f"\n{'='*60}")
    print(f" {_CURRENT_SECTION}")
    print(f"{'='*60}")


def ok(label: str) -> None:
    global PASS
    PASS += 1
    RESULTS.append({"section": _CURRENT_SECTION, "label": label, "status": "pass", "detail": ""})
    print(f"  {green('PASS')}: {label}")


def fail(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    RESULTS.append({"section": _CURRENT_SECTION, "label": label, "status": "fail", "detail": detail})
    msg = f"  {red('FAIL')}: {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def skip(label: str, reason: str = "") -> None:
    global SKIP
    SKIP += 1
    RESULTS.append({"section": _CURRENT_SECTION, "label": label, "status": "skip", "detail": reason})
    msg = f"  {yellow('SKIP')}: {label}"
    if reason:
        msg += f"  [{reason}]"
    print(msg)


def assert_true(label: str, value: bool, detail: str = "") -> None:
    if value:
        ok(label)
    else:
        fail(label, detail or f"got {value!r}")


def assert_eq(label: str, expected: Any, actual: Any) -> None:
    if expected == actual:
        ok(label)
    else:
        fail(label, f"expected={expected!r} actual={actual!r}")


def assert_contains(label: str, haystack: Any, needle: Any) -> None:
    if needle in str(haystack):
        ok(label)
    else:
        fail(label, f"missing {needle!r} in {str(haystack)[:200]}")


def is_getinfo_server_bug(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "rfc 3339" in msg or "deserial" in msg


# ===========================================================================
# Perf measurement helpers
# ===========================================================================


def measure(label: str, fn: Callable[[], Any], concurrency: int = 1, extra: dict | None = None) -> PerfSample:
    """Time a callable and return a PerfSample."""
    start = time.perf_counter()
    fn()
    elapsed_ms = (time.perf_counter() - start) * 1000
    return PerfSample(label=label, latency_ms=elapsed_ms, concurrency=concurrency, extra=extra or {})


def measure_one(fn: Callable[[], Any]) -> PerfSample:
    start = time.perf_counter()
    try:
        fn()
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        sample = PerfSample(label="", latency_ms=elapsed_ms)
        sample.extra["error"] = _shorten(str(exc))
        return sample
    elapsed_ms = (time.perf_counter() - start) * 1000
    return PerfSample(label="", latency_ms=elapsed_ms)


def _shorten(msg: str, limit: int = 200) -> str:
    return msg[:limit] + ("…" if len(msg) > limit else "")


def measure_parallel(label: str, fn: Callable[[], Any], n: int, concurrency: int) -> PerfResult:
    """Run *fn* *n* times with *concurrency* workers, return aggregated result."""
    result = PerfResult(scenario=label)
    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(lambda: measure_one(fn)) for _ in range(n)]
        for fut in as_completed(futures):
            result.samples.append(fut.result())
    wall_ms = (time.perf_counter() - wall_start) * 1000
    for s in result.samples:
        s.concurrency = concurrency
        s.extra["wall_ms"] = wall_ms
        s.extra["per_ms"] = wall_ms / n
    return result


# The latency fields ``print_parallel_stats`` can render, keyed by the short
# name a scenario names in ``metrics=(...)``. Order here is the print order.
_STAT_GETTERS: "dict[str, Callable[[PerfResult], float]]" = {
    "avg": lambda r: r.avg,
    "min": lambda r: r.min,
    "p50": lambda r: r.p50,
    "p95": lambda r: r.p95,
    "p99": lambda r: r.p99,
    "max": lambda r: r.max,
}

# Shown when a scenario does not name its own ``metrics`` — the historic set.
DEFAULT_STAT_METRICS: "tuple[str, ...]" = ("avg", "min", "p95", "max")


def print_parallel_stats(result: PerfResult, metrics: "tuple[str, ...] | None" = None) -> None:
    """Print the one-line concurrency summary shared by every parallel sweep.

    *metrics* picks which latency fields to show (any of ``avg`` / ``min`` /
    ``p50`` / ``p95`` / ``p99`` / ``max``), in the given order; unknown names
    are ignored. ``None`` falls back to :data:`DEFAULT_STAT_METRICS`. The
    trailing ``wall`` / ``per`` throughput columns are always shown.
    """
    extra = result.samples[0].extra if result.samples else {}
    stats = " ".join(
        f"{name}={_STAT_GETTERS[name](result):.1f}ms"
        for name in (metrics or DEFAULT_STAT_METRICS)
        if name in _STAT_GETTERS
    )
    suffix = ""
    if result.errored:
        suffix = yellow(f"  errors={result.errored}/{result.count}")
    print(f"  concurrency={result.concurrency:>2}: {stats}  "
          f"wall={extra.get('wall_ms', 0):.0f}ms "
          f"per={extra.get('per_ms', 0):.1f}ms{suffix}")


# ===========================================================================
# Scenario fixtures (context managers)
# ===========================================================================
#
# These take the pytest-style "setup / teardown around the body" pattern and
# make it fit a benchmark loop: they own the sandbox/snapshot *lifecycle* so a
# scenario body only writes the operation it actually measures. They replace
# the ``try/finally: sb.kill()`` (and snapshot-delete) boilerplate that was
# copy-pasted across the "sandbox is a precondition, not the measured op"
# scenarios (rollback / pause-resume / clone / ivshmem).
#
# Cleanup is always best-effort (swallows exceptions) so a teardown failure
# never masks the real error or aborts the loop, matching the previous inline
# ``except Exception: pass`` behaviour. Note these create a *fresh* resource
# per ``with`` block, which is exactly what the per-round loops need — unlike a
# pytest fixture that would be shared across the whole test.


@contextmanager
def sandbox(cfg: Config, template_id: "str | None" = None, *,
            timeout: int = 120, **create_opts: Any) -> "Iterator[Sandbox]":
    """Create a sandbox for the duration of the block, always killing it on exit.

    *template_id* defaults to ``cfg.template_id``; extra keyword args are passed
    straight through to ``Sandbox.create`` (e.g. ``volume_mounts=[...]``).

    Usage::

        with sandbox(cfg) as sb:
            ...  # measure operations on sb; kill() is automatic
    """
    sb = Sandbox.create(template_id or cfg.template_id, timeout=timeout, config=cfg, **create_opts)
    try:
        yield sb
    finally:
        try:
            sb.kill()
        except Exception:
            pass


def sandbox_op(cfg: Config, action: "Callable[[Sandbox], Any]",
               template_id: "str | None" = None, **create_opts: Any) -> "Callable[[], Any]":
    """Build a timed op that runs *action* against a throwaway sandbox.

    Folds the ``with sandbox(cfg) as sb: action(sb)`` pattern into a single
    callable, so a ``parallel_sweep`` scenario whose measured op is "spin up a
    fresh box, do one thing to it, tear it down" collapses to one ``yield``
    instead of a nested ``def op(): with sandbox(...)``::

        with snapshot_pool(cfg) as snaps:
            yield sandbox_op(cfg, lambda sb: snaps.add(sb.create_snapshot().snapshot_id))

    *action*'s return value is propagated (so the op can feed a pool); extra
    kwargs pass through to ``sandbox()`` / ``Sandbox.create``.
    """
    def op() -> Any:
        with sandbox(cfg, template_id, **create_opts) as sb:
            return action(sb)
    return op


@contextmanager
def snapshot(cfg: Config, template_id: "str | None" = None, *,
             timeout: int = 120, **create_opts: Any) -> "Iterator[tuple[Sandbox, str]]":
    """Create sandbox -> snapshot it, yield ``(sandbox, snapshot_id)``, clean up both.

    On exit the snapshot is deleted first, then the sandbox is killed (both
    best-effort). Suited to scenarios that need a live sandbox *and* one of its
    snapshots (e.g. rollback).

    Usage::

        with snapshot(cfg) as (sb, snap_id):
            sb.rollback(snap_id)  # delete_snapshot + kill are automatic
    """
    with sandbox(cfg, template_id, timeout=timeout, **create_opts) as sb:
        snap = sb.create_snapshot()
        snap_id = snap.snapshot_id
        try:
            yield sb, snap_id
        finally:
            try:
                Sandbox.delete_snapshot(snap_id, config=cfg)
            except Exception:
                pass


def snapshot_op(cfg: Config, action: "Callable[[Sandbox, str], Any]",
                template_id: "str | None" = None, **create_opts: Any) -> "Callable[[], Any]":
    """Build a timed op that runs *action* against a fresh sandbox + its snapshot.

    The ``snapshot()`` sibling of :func:`sandbox_op`: it folds the
    ``with snapshot(cfg) as (sb, snap_id): action(sb, snap_id)`` pattern into one
    callable, so a ``parallel_sweep`` scenario whose measured op needs both a
    live box *and* one of its snapshots (e.g. rollback) collapses to one
    ``yield`` instead of a nested ``def op(): with snapshot(...)``::

        yield snapshot_op(cfg, lambda sb, snap_id: sb.rollback(snap_id))

    *action*'s return value is propagated; extra kwargs pass through to
    ``snapshot()`` / ``sandbox()`` / ``Sandbox.create``.
    """
    def op() -> Any:
        with snapshot(cfg, template_id, **create_opts) as (sb, snap_id):
            return action(sb, snap_id)
    return op


class _Pool:
    """A thread-safe collector of resources, cleaned up en masse on scope exit.

    Type-agnostic on purpose: ``add()`` just accumulates whatever it is handed —
    a ``Sandbox``, a snapshot-id ``str``, a ``Volume`` — under a lock, so one
    collector backs all three resource lines instead of each hand-copying an
    identical ``list + lock + add/len/iter`` triad. The paired ``_pool(...)``
    engine supplies the per-item teardown; ``__len__`` / iteration let a
    scenario read the live count (e.g. density's per-sandbox overhead).
    """

    def __init__(self) -> None:
        self._items: "list[Any]" = []
        self._lock = threading.Lock()

    def add(self, item: Any) -> Any:
        with self._lock:
            self._items.append(item)
        return item

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> "Iterator[Any]":
        return iter(list(self._items))


@contextmanager
def _pool(teardown: "Callable[[Any], Any]") -> "Iterator[_Pool]":
    """Yield a :class:`_Pool`, applying *teardown* to every item on scope exit.

    The shared engine behind ``sandbox_pool`` / ``snapshot_pool`` /
    ``volume_pool``: each names only its one-line *teardown* closure (the sole
    axis on which the three pools differ) and hands it here. Teardown runs
    best-effort per item (exceptions swallowed) so one failure never aborts
    cleanup of the rest, matching the old inline ``except Exception: pass``.
    """
    pool = _Pool()
    try:
        yield pool
    finally:
        for item in pool:
            try:
                teardown(item)
            except Exception:
                pass


def sandbox_pool() -> "AbstractContextManager[_Pool]":
    """Collect any number of sandboxes, killing them all (best-effort) on exit.

    Unlike ``sandbox()`` (one box per ``with``), this owns a *pool*: it suits
    the scenarios where creation itself is the measured op and the sandboxes
    are just cleanup debt (template-create, density, volume-mount-sandbox).
    Takes no ``cfg`` — a sandbox tears itself down via ``kill()``.

    Usage::

        with sandbox_pool() as pool:
            pool.add(Sandbox.create(...))  # thread-safe; all killed on exit
    """
    return _pool(lambda sb: sb.kill())


def snapshot_pool(cfg: Config) -> "AbstractContextManager[_Pool]":
    """Collect snapshot ids, deleting them all (best-effort) on scope exit.

    The snapshot-id counterpart of ``sandbox_pool()``: when snapshot *creation*
    is the measured op, the resulting snapshots are just cleanup debt. Needs
    *cfg* because ``delete_snapshot`` is a config-scoped call.

    Usage::

        with snapshot_pool(cfg) as snaps:
            snaps.add(sb.create_snapshot().snapshot_id)  # all deleted on exit
    """
    return _pool(lambda snap_id: Sandbox.delete_snapshot(snap_id, config=cfg))


def volume_pool(cfg: Config) -> "AbstractContextManager[_Pool]":
    """Collect volumes, destroying them all (best-effort) on scope exit.

    The volume counterpart of ``sandbox_pool()`` / ``snapshot_pool()``. It
    stores whole ``Volume`` objects — teardown reads ``.volume_id`` off each —
    so a scenario keeps the handle it needs (e.g. to build a ``mount(...)``)
    while the pool owns cleanup. Re-destroying a volume the scenario already
    destroyed is a harmless no-op (teardown swallows exceptions). Needs *cfg*
    because ``Volume.destroy`` is a config-scoped call.

    When a scenario also owns sandboxes that *mount* these volumes, nest a
    ``sandbox_pool()`` **inside** this block so the sandboxes are killed (inner
    exit) before their volumes are destroyed (outer exit)::

        with volume_pool(cfg) as vols:
            with sandbox_pool() as boxes:
                ...  # boxes killed first, then vols destroyed
    """
    return _pool(lambda vol: Volume.destroy(vol.volume_id, config=cfg))
