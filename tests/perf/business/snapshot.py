# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""快照业务 — 创建、列表、删除与自动清理。

全模块通过 :class:`cubesandbox.Template` 和 :class:`cubesandbox.Sandbox`
调用底层 API，复用 SDK 的连接池、鉴权和错误处理语义。 业务层只做数据筛选
（如按 ``snap-`` 前缀过滤），不拼 HTTP 调用。

.. warning::
    本模块 **仅操作快照**（``snap-*`` 前缀的 ID），**不会删除 /
    触及普通模板**。删除前已按 ``snap-*`` 前缀过滤。
"""

from __future__ import annotations

import os
import time
from typing import Any


# ── 列表 ────────────────────────────────────────────────────────────────


def list_snaps() -> list[dict[str, Any]]:
    """返回当前所有快照模板（``snap-*``）。

    调用 ``Template.list()`` → 筛选 ``template_id`` 前缀为 ``snap-`` 的
    条目，返回轻量 dict 列表供调用方消费。
    """
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


# ── 删除 ────────────────────────────────────────────────────────────────


def delete_snaps(ids: list[str]) -> tuple[int, int]:
    """批量删除快照。返回 ``(ok_count, fail_count)``。

    底层走 ``Template.delete()``（``DELETE /templates/{id}``）。
    CubeAPI 在该端点自动识别快照 ID。
    """
    import sys

    from cubesandbox import Template

    ok = 0
    fail = 0
    for tid in ids:
        try:
            Template.delete(tid)
            ok += 1
        except Exception as exc:
            fail += 1
            print(f"[snapshot] Template.delete({tid}) failed: {exc}", file=sys.stderr)
    return ok, fail


# ── 自动清理钩子 ────────────────────────────────────────────────────────


def auto_cleanup_if_enabled() -> None:
    """压测后自动清理残留快照。

    在 ``CUBE_PERF_AUTO_CLEANUP=1`` 时激活（默认关闭）。
    复用上面的 ``list_snaps`` + ``delete_snaps``。
    """
    import sys

    if os.environ.get("CUBE_PERF_AUTO_CLEANUP") != "1":
        return

    wait = float(os.environ.get("CUBE_PERF_AUTO_CLEANUP_WAIT", "3"))
    if wait > 0:
        time.sleep(wait)

    snaps = list_snaps()
    if not snaps:
        return

    ids = [s["template_id"] for s in snaps]
    print(f"\n[snapshot] auto-cleaning {len(ids)} snapshot(s) ...", file=sys.stderr)
    ok, fail = delete_snaps(ids)
    print(f"[snapshot] done: {ok} deleted, {fail} failed", file=sys.stderr)
