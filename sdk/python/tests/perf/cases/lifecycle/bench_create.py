# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle scenario: template-based sandbox creation (single & concurrent)."""

from __future__ import annotations

from cubesandbox import Config, Sandbox

from ...framework.registry import ReportGroup, benchmark, parallel_sweep
from ...framework.runner import sandbox_pool


@benchmark("template-create", aliases=["create"],
           report=ReportGroup("基于模板创建沙箱（冷启动）"))
@parallel_sweep("template-create", header=" [Perf] Template-Based Sandbox Creation")
def bench_template_create(cfg: Config, concurrency: int, n: int):
    """Benchmark: Template-based sandbox creation (single & concurrent).

    Creation *is* the measured op; the created sandboxes are just cleanup debt,
    so a ``sandbox_pool`` collects them and the sweep kills them per level.
    """
    with sandbox_pool() as pool:
        yield lambda: pool.add(Sandbox.create(cfg.template_id, timeout=120, config=cfg))
