# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""平台运维操作 — 默认脚本注册、快照 CRUD、压测中/后资源清理。

通过 SDK 调用底层 API（不拼裸 HTTP），操作仅限快照（``snap-*`` 前缀 ID），
不动普通模板。
"""

from __future__ import annotations

import os
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


def register_default_scripts():
    """自动注册内置默认脚本（若 CUBE_EXTERNAL_SCRIPTS 未设置）。"""
    from ..framework import registry

    if not os.environ.get("CUBE_EXTERNAL_SCRIPTS"):
        os.environ["CUBE_EXTERNAL_SCRIPTS"] = ",".join(_DEFAULT_SCRIPTS)
    registry.discover_external_scripts()


# ── 快照 CRUD（仅 snap-*，不动模板）───────────────────────────────────

def list_snapshots():
    """返回当前所有 ``snap-*`` 快照，调用 ``Template.list()`` 后按前缀过滤。"""
    import sys
    from cubesandbox import Template
    try:
        tmpls = Template.list()
    except Exception as exc:
        print(f"[cleanup] Template.list() failed: {exc}", file=sys.stderr)
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
    """批量删除快照，遇到 "already in progress" 自动重试，返回 (ok, fail)。"""
    import sys
    from cubesandbox import Template

    retries = int(os.environ.get("CUBE_PERF_CLEANUP_RETRIES", "3"))
    ok = fail = 0
    for tid in ids:
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                Template.delete(tid)
                ok += 1
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                msg = str(exc)
                if "130409" in msg or "already in progress" in msg.lower():
                    if attempt < retries:
                        backoff = attempt * 2
                        print(f"[cleanup] {tid}: in progress, retry {attempt}/{retries} "
                              f"in {backoff}s...", file=sys.stderr)
                        time.sleep(backoff)
                        continue
                fail += 1
                print(f"[cleanup] {tid}: {exc}", file=sys.stderr)
                break
        if last_err:
            fail += 1
    return ok, fail


# ── 清理钩子 ──────────────────────────────────────────────────────────

def _auto_cleanup_enabled() -> bool:
    return os.environ.get("CUBE_PERF_AUTO_CLEANUP", "1") != "0"


def _cleanup_wait_seconds() -> float:
    return float(os.environ.get("CUBE_PERF_AUTO_CLEANUP_WAIT", "5"))


def cleanup_all_snapshots(label: str = "") -> None:
    """删除当前所有 snap-* 快照（可重入，每并发档跑完后调一次）。

    默认激活；设 ``CUBE_PERF_AUTO_CLEANUP=0`` 可关闭。*label* 用于日志区分。
    """
    import sys

    if not _auto_cleanup_enabled():
        return

    wait = _cleanup_wait_seconds()
    if wait > 0:
        time.sleep(wait)

    snaps = list_snapshots()
    if not snaps:
        return

    ids = [s["template_id"] for s in snaps]
    tag = f" [{label}]" if label else ""
    print(f"\n[cleanup{tag}] {len(ids)} snapshot(s) ...", file=sys.stderr)
    ok, fail = delete_snapshots(ids)
    print(f"[cleanup{tag}] {ok} deleted, {fail} failed", file=sys.stderr)


# ── 钩子注册（供 registry.py 调用）────────────────────────────────────

def post_concurrency_cleanup(label: str = "") -> None:
    """每档并发跑完后立即清理——由 ``registry.py`` 在每个 ``for c in _levels`` 末尾调用。

    *label* 形如 ``"clone/c=10"``，便于日志追踪。
    """
    cleanup_all_snapshots(label)
