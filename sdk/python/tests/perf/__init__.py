# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Standalone performance benchmark suite for the Python SDK.

This package was split out of `tests/e2e/` so that performance benchmarking
can be run and maintained independently from the functional integration
tests. It reuses the shared config/env/runner/report infrastructure from
the `e2e` package (both are siblings under `tests/`).

Run it via the CLI entry point (from the ``tests/`` directory)::

    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf

Modules:
    perf - performance benchmark scenarios (create/snapshot/clone/pause-resume/density)
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
# Ensure the sibling `e2e` package (shared config/env/runner/report) is importable
# even when `perf` is imported from outside the `tests/` directory.
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
