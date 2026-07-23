# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Full-chain integration & performance benchmark test suite for the Python SDK.

This package is the modular successor to the former single-file
``integration_test_full.py`` script. Run it via the backward-compatible
entry point::

    CUBE_API_URL=... CUBE_API_KEY=... python3 tests/integration_test_full.py

or directly as a module (from the ``tests/`` directory)::

    CUBE_API_URL=... CUBE_API_KEY=... python3 -m e2e

Modules:
    config     - CLI/env configuration resolution & tunables
    env        - host/CPU/memory/disk/template environment collection
    runner     - assertion helpers, pass/fail/skip counters, perf measuring
    functional - functional (non-perf) API test cases
    perf       - performance benchmark scenarios
    report     - Markdown + JSON report generation (English & Chinese)
"""

from __future__ import annotations

import os
import sys

# tests/e2e/__init__.py -> tests/e2e -> tests -> sdk/python
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_TESTS_DIR = os.path.abspath(os.path.join(_PKG_DIR, ".."))
_SDK_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _SDK_ROOT not in sys.path:
    sys.path.insert(0, _SDK_ROOT)
# Ensure the sibling `perf` package (standalone benchmark suite) is
# importable even when `e2e` is imported from outside the `tests/` directory.
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
