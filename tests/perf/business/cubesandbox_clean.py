# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""业务模块 — 默认脚本导入、注册与压测后资源清理。

与 framework（执行引擎）、reporting（报告输出）、plugins（前端 HTML
生成）解耦。操作全部通过 SDK 调用底层 API（非裸 HTTP）。

模块职责：
    默认脚本注册
    快照列表 / 删除（仅 snap-*，不动模板）
    压测后统一清理（快照 + 孤儿沙箱 + 残留资源）
"""

from __future__ import annotations

import os
import time

# ── 快照 CRUD（仅 snap-*，不动模板）───────────────────────────────────

def list_snapshots():
    """返回当前所有 ``snap-*`` 快照，调用 ``Template.list()`` 后按前缀过滤。"""
    import sys
    from cubesandbox import Template
    try:
        tmpls = Template.list()
    except Exception as exc:
        print(f"[snapshot] Template.list() failed: {exc}", file=sys.stderr)
        return []
    return [
        {
            "template_id": t.template_id,
            "status": getattr(t, "status", ""),
            "created_at": getattr(t, "created_at", "") or getattr(t, "createdAt", ""),
        }
        for t in tmpls
        if (t.template_id or "").startswith("snap-")
    ]


def delete_snapshots(ids: list[str]) -> tuple[int, int]:
    """批量删除快照（Template.delete），返回 (ok, fail)。"""
    import sys
    from cubesandbox import Template
    ok = fail = 0
    for tid in ids:
        try:
            Template.delete(tid)
            ok += 1
        except Exception as exc:
            fail += 1
            print(f"[snapshot] {tid}: {exc}", file=sys.stderr)
    return ok, fail


# ── 默认脚本注册 ──────────────────────────────────────────────────────

_DEFAULT_SCRIPTS = [
    "../examples/snapshot-rollback-clone/bench_clone_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_create_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_snapshot_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_rollback_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_pause_resume_concurrency.py",
    "../examples/snapshot-rollback-clone/bench_snapshot_dirty.py",
]


def register_default_scripts():
    """自动注册内置默认脚本（若 CUBE_EXTERNAL_SCRIPTS 未设置）。"""
    from .framework import registry

    if not os.environ.get("CUBE_EXTERNAL_SCRIPTS"):
        os.environ["CUBE_EXTERNAL_SCRIPTS"] = ",".join(_DEFAULT_SCRIPTS)
    registry.discover_external_scripts()


# ── 压测后清理 ────────────────────────────────────────────────────────

def cleanup_after_benchmark():
    """所有场景跑完后统一清理。

    仅在 CUBE_PERF_AUTO_CLEANUP=1 时激活。
    """
    import sys

    if os.environ.get("CUBE_PERF_AUTO_CLEANUP") != "1":
        return

    wait = float(os.environ.get("CUBE_PERF_AUTO_CLEANUP_WAIT", "3"))
    if wait > 0:
        time.sleep(wait)

    snaps = list_snapshots()
    if snaps:
        ids = [s["template_id"] for s in snaps]
        print(f"\n[cleanup] {len(ids)} snapshot(s) ...", file=sys.stderr)
        ok, fail = delete_snapshots(ids)
        print(f"[cleanup] {ok} deleted, {fail} failed", file=sys.stderr)
