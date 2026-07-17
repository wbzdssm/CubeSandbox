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
