# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Test runner primitives: counters, assertions, and perf measurement helpers.

Holds the process-wide mutable state (pass/fail/skip counters, collected
results) that `functional.py`, `report.py`, and the sibling `tests/perf/`
benchmark package all read/write.
"""

from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

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
    def latencies(self) -> list[float]:
        return [s.latency_ms for s in self.samples]

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
    fn()
    elapsed_ms = (time.perf_counter() - start) * 1000
    return PerfSample(label="", latency_ms=elapsed_ms)


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
