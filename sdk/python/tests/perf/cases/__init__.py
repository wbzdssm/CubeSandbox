# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Benchmark scenario auto-discovery (pytest-style ``bench_*`` collection).

The perf suite keeps its own ordered registry: the ``@benchmark`` decorator
registers each scenario at import time, and the *decoration order is the
canonical run order*. So importing ``perf.cases`` must import every scenario
module to populate ``framework.registry``.

Discovery mirrors pytest's ``test_*`` convention: any module named
``bench_*.py`` anywhere under this package (in any subpackage) is a scenario
and is imported automatically — dropping a new ``bench_<name>.py`` file in is
all it takes to register a benchmark; no hand-maintained import list here.

Modules are imported in **sorted dotted-path order** (i.e. alphabetical by
subpackage then filename), and that order is the run / report order:
    clone, ivshmem, lifecycle/(create, density, pause_resume),
    snapshot/(create, create_from, dirty, rollback), volume

Helper modules that are *not* benchmarks (e.g. ``ivshmem/probe.py``) simply
skip the ``bench_`` prefix and are never auto-imported.
"""

from __future__ import annotations

import importlib
import pkgutil

__all__: list[str] = []


def _discover_benchmark_modules() -> "list[str]":
    """Return the sorted dotted names of every ``bench_*`` module under this package."""
    names: list[str] = []
    for info in pkgutil.walk_packages(__path__, prefix=__name__ + "."):
        leaf = info.name.rsplit(".", 1)[-1]
        if not info.ispkg and leaf.startswith("bench_"):
            names.append(info.name)
    return sorted(names)


def _import_benchmarks() -> None:
    """Import every discovered ``bench_*`` module in sorted order.

    Sorted dotted-path order makes decoration order == run order (see the
    module docstring). Wrapped in a function so no loop variable leaks into
    the package namespace.
    """
    for modname in _discover_benchmark_modules():
        importlib.import_module(modname)


_import_benchmarks()
