# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Framework barrel — one import to write a new benchmark.

    from framework import perf_test, op

    @perf_test("my-scenario", title="My Scenario", levels=(1, 5, 10))
    def bench(cfg, concurrency, n):
        yield op.sandbox(cfg, lambda sb: sb.exec("whoami"))
"""

from .registry import (
    auto,
    benchmark,
    parallel_sweep,
    perf_test,
    register_external,
    sandbox_benchmark,
    ReportGroup,
)
from .runner import (
    sandbox as _raw_sandbox,
    sandbox_op as _raw_sandbox_op,
    snapshot_op as _raw_snapshot_op,
)


class _Ops:
    """Minimal namespace so ``op.sandbox`` reads naturally.

    ``op.sandbox(cfg, fn)``  →  yieldable, pool‑based sandbox benchmark op.
    ``op.snapshot(cfg, fn)``  →  yieldable, pool‑based snapshot benchmark op.
    """

    sandbox = staticmethod(_raw_sandbox_op)
    snapshot = staticmethod(_raw_snapshot_op)

    @staticmethod
    def context(cfg, template_id=None, **create_opts):
        """Context-manager sandbox for single‑shot benchmarks.

        Usage::

            with op.context(cfg) as sb:
                print(sb.exec("whoami"))
        """
        return _raw_sandbox(cfg, template_id, **create_opts)


op = _Ops()
