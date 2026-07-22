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



Raw ``run()`` auto-registration (zero decorator)
------------------------------------------------
A module whose top-level name contains a callable ``run`` is automatically
turned into a single‑concurrency benchmark::

    # cases/mything/bench_fib.py
    '''Compute Fibonacci in a sandbox.'''          # first line → report title

    LEVELS = (1, 5, 10)                            # optional concurrency ladder

    def run():
        from cubesandbox import Sandbox
        sb = Sandbox.create("tpl-xxx")
        try:
            return sb.exec("fib(20)")
        finally:
            sb.kill()

That's it — no imports from ``framework``, no decorators, no generators.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

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
    _auto_register_raw_benchmarks()
    _register_external_scripts()


def _auto_register_raw_benchmarks() -> None:
    """Auto‑register raw ``run()`` functions as benchmarks.

    Scans every imported ``bench_*`` module.  If a module exposes a callable
    named ``run`` and has *not* already registered a benchmark via the
    ``@benchmark`` decorator, we wrap it automatically so the user gets the
    full concurrency‑sweep + stats pipeline for free.  A module‑level
    ``LEVELS`` tuple / list controls the concurrency ladder (falls back to the
    framework default).  The module docstring's first line becomes the report
    section title.
    """
    from framework.registry import (  # late import to avoid circularity at init
        BENCHMARK_REGISTRY,
        auto,
        ReportGroup,
    )

    registered_keys = {s.key for s in BENCHMARK_REGISTRY}
    for modname in _discover_benchmark_modules():
        mod = sys.modules.get(modname)
        if mod is None:
            continue
        fn = getattr(mod, "run", None)
        if not callable(fn):
            continue
        # Derive key: bench_my_test.py  →  my-test
        leaf = modname.rsplit(".", 1)[-1]
        key = leaf.replace("bench_", "", 1).replace("_", "-")
        if key in registered_keys:
            continue

        title = ""
        if mod.__doc__:
            title = mod.__doc__.strip().split("\n")[0].strip()
        levels = getattr(mod, "LEVELS", None)
        if levels is not None and not isinstance(levels, (tuple, list)):
            levels = tuple(levels) if hasattr(levels, "__iter__") else None

        auto(fn, key=key, title=title, levels=levels)
        registered_keys.add(key)


def _register_external_scripts() -> None:
    """Register standalone scripts listed in ``CUBE_EXTERNAL_SCRIPTS``.

    Also auto‑discovers all ``bench_*.py`` files under
    ``examples/snapshot-rollback-clone/`` (relative to the SDK root) so the
    original hand‑written benchmarks participate in the suite automatically.

    Conventions for script authors
    ------------------------------
    - ``def run():``       → entry point; called once per measured iteration.
    - ``LEVELS = (1,…)``   → (optional) concurrency ladder; falls back to the
      framework default.
    - First line of the module docstring / first comment line → report title.

    The framework wraps ``run()`` with subprocess invocation (zero import
    coupling), sweeps concurrency levels, measures wall‑clock, and merges the
    results into the same report as the internal scenarios.
    """
    import os
    import re
    from pathlib import Path

    from framework.registry import BENCHMARK_REGISTRY, register_external  # noqa: PLC0415

    # --- Collect script paths ---
    candidates: list[Path] = []

    # 1) CUBE_EXTERNAL_SCRIPTS env var (comma separated)
    raw = os.environ.get("CUBE_EXTERNAL_SCRIPTS", "").strip()
    if raw:
        for p in raw.split(","):
            p = p.strip()
            if p:
                candidates.append(Path(p).expanduser().resolve())

    # 2) Default: examples/snapshot-rollback-clone/bench_*.py
    _sdk_root = Path(__file__).resolve().parents[5]  # sdk/python/
    _examples_dir = _sdk_root / "examples" / "snapshot-rollback-clone"
    if _examples_dir.is_dir():
        for pf in sorted(_examples_dir.glob("bench_*.py")):
            if pf.is_file() and pf not in candidates:
                candidates.append(pf)

    # --- Register each ---
    registered_keys = {s.key for s in BENCHMARK_REGISTRY}

    for p in candidates:
        if not p.is_file():
            print(f"  [WARN] external script not found: {p}")
            continue

        # Derive key from filename: bench_clone_concurrency.py → clone-concurrency
        key = p.stem.replace("bench_", "", 1).replace("_", "-")
        if key in registered_keys:
            print(f"  [WARN] external script '{key}' conflicts with "
                  f"an existing benchmark — skipped")
            continue

        levels = None
        title = ""
        try:
            source = p.read_text(encoding="utf-8")
            # Parse LEVELS = (1, 5, 10)
            m = re.search(r"^LEVELS\s*=\s*[\[(]([^)\]]+)[)\]]", source, re.MULTILINE)
            if m:
                levels = tuple(
                    int(x.strip()) for x in m.group(1).split(",") if x.strip()
                )
            # First docstring / comment line → title
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

        register_external(key, str(p), title=title, levels=levels)
        registered_keys.add(key)


_import_benchmarks()
