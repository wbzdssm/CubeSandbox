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
    "../examples/ivshmem/ivshmem_benchmark.py",
]


def register_default_scripts():
    """Always register all built-in example scripts.

    ``CUBE_EXTERNAL_SCRIPTS`` is force-set to the full default list (globs) so
    that a previous filtered run (e.g. ``--scenarios snapshot-dirty``) never
    leaves only a single script in ``.env``. The env var is intentionally NOT
    in ``_TUNABLE_ENV_KEYS`` to avoid being persisted.
    """
    from ..framework import registry

    os.environ["CUBE_EXTERNAL_SCRIPTS"] = ",".join(_DEFAULT_SCRIPTS)
    registry.discover_external_scripts()


# ── 快照 CRUD（仅 snap-*，不动模板）───────────────────────────────────

def list_snapshots():
    """返回当前所有 ``snap-*`` 快照，过滤掉正在使用中的。

    有活跃 replica（如 12cc1289f9...@9.135.79.34）的快照不可删除，
    直接从列表中排除，避免 delete 时触发 130409 错误。
    """
    import sys
    from cubesandbox import Template
    try:
        tmpls = Template.list()
    except Exception as exc:
        print(f"[cleanup] Template.list() failed: {exc}", file=sys.stderr)
        return []
    result = []
    for t in tmpls:
        tid = t.template_id or ""
        if not tid.startswith("snap-"):
            continue
        # Skip snapshots that have active replicas (in-use sandboxes).
        # Deleting them would fail with 130409 — just skip silently.
        if getattr(t, "replicas", []):
            continue
        result.append({
            "template_id": tid,
            "status": getattr(t, "status", "") or "",
            "created_at": getattr(t, "created_at", "") or getattr(t, "createdAt", ""),
        })
    return result


def delete_snapshots(ids: list[str]) -> tuple[int, int]:
    """批量删除快照，遇到 130409 直接跳过，返回 (ok, fail)。

    130409 涵盖两种不可删除的情况：
    - "active runtime ref" — 快照被沙箱使用
    - "active snapshot operation" — 另一个快照操作进行中

    这两种重试也没用，直接跳过。
    """
    import sys
    from cubesandbox import Template

    ok = fail = 0
    for tid in ids:
        try:
            Template.delete(tid)
            ok += 1
        except Exception as exc:
            msg = str(exc)
            if "130409" in msg:
                print(f"[cleanup] {tid}: in use, skipped", file=sys.stderr)
            else:
                print(f"[cleanup] {tid}: {exc}", file=sys.stderr)
            fail += 1
    return ok, fail


# ── 清理钩子 ──────────────────────────────────────────────────────────

def _auto_cleanup_enabled() -> bool:
    return os.environ.get("CUBE_PERF_AUTO_CLEANUP", "1") != "0"


def _cleanup_wait_seconds() -> float:
    return float(os.environ.get("CUBE_PERF_AUTO_CLEANUP_WAIT", "0"))


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


def cleanup_all_sandboxes(label: str = "") -> tuple[int, int]:
    """Kill all sandboxes via the SDK. Returns (ok, fail)."""
    import sys
    from cubesandbox import Sandbox

    try:
        sandboxes = Sandbox.list()
    except Exception as exc:
        print(f"[cleanup{label}] Sandbox.list() failed: {exc}", file=sys.stderr)
        return 0, 0

    if not sandboxes:
        return 0, 0

    prefix = f"[cleanup{label}]"
    print(f"{prefix} {len(sandboxes)} sandbox(es) ...", file=sys.stderr)
    ok = fail = 0
    for s in sandboxes:
        sid = s.get("sandboxID", "")
        if not sid:
            continue
        try:
            Sandbox.connect(sid).kill()
            ok += 1
        except Exception as exc:
            fail += 1
            print(f"[cleanup{label}] kill {sid}: {exc}", file=sys.stderr)
    print(f"{prefix} {ok} killed, {fail} failed", file=sys.stderr)
    return ok, fail


def post_concurrency_cleanup(label: str = "") -> None:
    """每档并发跑完后立即清理——由 ``registry.py`` 在每个 ``for c in _levels`` 末尾调用。

    *label* 形如 ``"clone/c=10"``，便于日志追踪。
    """
    cleanup_all_sandboxes(label)
    cleanup_all_snapshots(label)
