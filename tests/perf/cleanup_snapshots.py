# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot cleanup helpers — list & delete snapshots via the Python SDK.

Uses :class:`cubesandbox.Template` for discovery (``Template.list()``) and
deletion (``Template.delete()``) so the perf harness reuses the same
connection pool, auth mechanism, and error handling as the benchmarks
themselves.  No raw HTTP calls, no separate URL/header plumbing.
"""

from __future__ import annotations

import os
import time
from typing import Any


def list_snaps() -> list[dict[str, Any]]:
    """Return all snapshot templates via ``Template.list()``.

    Filters the full template list to entries whose ``template_id`` starts
    with ``snap-``.  Returns a list of lightweight dicts so callers never
    depend on the SDK response model shape.
    """
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


def delete_snaps(ids: list[str]) -> tuple[int, int]:
    """Delete a batch of snapshot templates. Returns (ok_count, fail_count).

    Uses ``Template.delete()`` (which targets ``DELETE /templates/{id}``).
    Snapshots are stored as templates, so this is the same endpoint
    ``cubemastercli snapshot delete`` resolves to.
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
            print(f"[cleanup] Template.delete({tid}) failed: {exc}", file=sys.stderr)
    return ok, fail


def auto_cleanup_if_enabled() -> None:
    """After-benchmark cleanup hook — invoked from ``__main__.py``.

    Activated when ``CUBE_PERF_AUTO_CLEANUP=1`` (default: off).  Waits a
    few seconds for async operations to finish, then deletes all snap-*
    templates via ``Template.delete()``.
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
    print(f"\n[cleanup] auto-cleaning {len(ids)} snapshot(s) ...", file=sys.stderr)
    ok, fail = delete_snaps(ids)
    print(f"[cleanup] done: {ok} deleted, {fail} failed", file=sys.stderr)
