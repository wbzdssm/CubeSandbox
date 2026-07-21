# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Lifecycle scenario: template-based sandbox creation (single & concurrent)."""

from __future__ import annotations

from cubesandbox import Config, Sandbox

from ...framework.config import CREATE_CONCURRENCY_LEVELS
from ...framework.registry import ReportChart, ReportSection, benchmark, parallel_sweep
from ...framework.runner import sandbox_pool


@benchmark(
    "template-create",
    aliases=["create"],
    report=ReportSection(
        table="latency",
        order=2,
        title_zh="基于模板创建沙箱（冷启动）",
        title_en="Template-Based Sandbox Creation (Cold Start)",
        method_zh="调用 `POST /sandboxes`（指定 `template_id`）到沙箱进入 `running` 状态的端到端耗时。这是最常见的使用场景。",
        method_en="end-to-end time from `POST /sandboxes` (with `template_id`) to the sandbox reaching `running`.",
        throughput=True,
        noun_zh="创建",
        noun_en="creation",
        charts=(ReportChart("基于模板创建沙箱（冷启动）"),),
    ),
)
@parallel_sweep(
    "template-create",
    header=" [Perf] Template-Based Sandbox Creation",
    levels=CREATE_CONCURRENCY_LEVELS,
)
def bench_template_create(cfg: Config, concurrency: int, n: int):
    """Benchmark: Template-based sandbox creation (single & concurrent).

    Creation *is* the measured op; the created sandboxes are just cleanup debt,
    so a ``sandbox_pool`` collects them and the sweep kills them per level.
    """
    with sandbox_pool() as pool:
        yield lambda: pool.add(Sandbox.create(cfg.template_id, timeout=120, config=cfg))
