# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Standalone performance benchmark suite for the Python SDK.

Self-contained package — all dependencies (config/env/runner/report) are
included within the `perf/` directory.  No external `e2e/` import needed.

Run it via the CLI entry point (from the ``tests/`` directory)::

    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf

Modules:
    benchmarks  - 11 benchmark scenarios + run_all()
    baseline    - official CubeSandbox perf baseline data (4 machines)
    report_html - self-contained HTML report with Chart.js line charts
    report      - Markdown + JSON report generation
    config      - configuration resolution & runtime tunables
    env         - environment info collection (hardware + component versions)
    runner      - PerfResult, PerfSample, measure helpers
"""

from __future__ import annotations

import os
import sys

# tests/perf/__init__.py -> tests/perf -> tests -> sdk/python
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_TESTS_DIR = os.path.abspath(os.path.join(_PKG_DIR, ".."))
_SDK_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _SDK_ROOT not in sys.path:
    sys.path.insert(0, _SDK_ROOT)


def _load_dotenv() -> None:
    """Populate os.environ from a ``.env`` file (zero-dependency).

    This runs before any perf submodule reads its env-driven tunables, so a
    ``.env`` file lets you configure the whole suite (API creds, concurrency,
    report customization) without exporting variables by hand. Rules:

    - Real environment variables always win; ``.env`` only fills in the gaps,
      so ``CUBE_API_KEY=... python -m perf`` still overrides the file.
    - The first ``.env`` found across these locations is used (nearest first):
      current working dir, ``tests/perf/``, ``tests/``, ``sdk/python/``.
    - Lines may be ``KEY=VALUE`` or ``export KEY=VALUE``; blank lines and
      ``#`` comments are ignored; surrounding single/double quotes are stripped.
    - ``CUBE_DOTENV`` may point at an explicit file to load instead.
    """
    explicit = os.environ.get("CUBE_DOTENV")
    candidates = (
        [explicit] if explicit else [
            os.path.join(os.getcwd(), ".env"),
            os.path.join(_PKG_DIR, ".env"),
            os.path.join(_TESTS_DIR, ".env"),
            os.path.join(_SDK_ROOT, ".env"),
        ]
    )
    path = next((p for p in candidates if p and os.path.isfile(p)), None)
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # Real env vars take precedence over the .env file.
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()
