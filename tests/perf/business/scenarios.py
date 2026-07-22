# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""场景管理 — 默认脚本导入、注册与压测后清理。

每一类场景（创建沙箱、快照、回滚、克隆……）的运行逻辑由外部脚本定义；
本模块只负责「注册到框架」和「压测完成后统一清理残留」这两个横切面。

与本层其他模块的关系：
    snapshot.py   — 快照列表 / 删除（不含模板操作）
    scenarios.py  — 脚本导入 + 清理编排（本文件）
"""

from __future__ import annotations

import os
import sys
import time


# ── 默认脚本注册 ──────────────────────────────────────────────────────

_DEFAULT_SCRIPTS = [
    "../examples/snapshot-rollback-clone/bench_clone_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_create_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_snapshot_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_rollback_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_pause_resume_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_snapshot_dirty.py",
]


def register_default_scripts() -> None:
    """将内置默认场景注册到 registry（若 CUBE_EXTERNAL_SCRIPTS 未设）。

    在 ``__init__.py`` 导入后立即调用此函数，确保场景列表就绪。
    """
    from ..framework import registry

    if os.environ.get("CUBE_EXTERNAL_SCRIPTS"):
        registry.discover_external_scripts()
    else:
        os.environ["CUBE_EXTERNAL_SCRIPTS"] = ",".join(_DEFAULT_SCRIPTS)
        registry.discover_external_scripts()


# ── 压测后清理 ────────────────────────────────────────────────────────


def cleanup_after_benchmark() -> None:
    """所有场景跑完后统一清理残留资源。

    当前负责：
        - 删除所有 snap-* 快照模板（通过 snapshot.py 调用）
    未来扩展：
        - 清理孤儿沙箱（cubecli unsafe destroyall）

    仅当 ``CUBE_PERF_AUTO_CLEANUP=1`` 时激活。
    """
    if os.environ.get("CUBE_PERF_AUTO_CLEANUP") != "1":
        return

    from .snapshot import auto_cleanup_if_enabled

    auto_cleanup_if_enabled()
