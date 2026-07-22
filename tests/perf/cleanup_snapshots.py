# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Snapshot cleanup helpers - list & delete snapshots via CubeAPI."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_API_URL = "http://127.0.0.1"


def _api_url() -> str:
    return os.environ.get("CUBE_API_URL") or DEFAULT_API_URL


def _api_headers() -> dict[str, str]:
    key = os.environ.get("CUBE_API_KEY") or os.environ.get("E2B_API_KEY") or ""
    return {"X-API-Key": key} if key else {}


def list_snaps() -> list[dict[str, Any]]:
    """Return all snapshot templates currently registered.

    Calls ``GET /templates`` (CubeAPI flat list), then filters to entries
    whose templateID starts with ``snap-``.
    """
    url = f"{_api_url()}/templates"
    try:
        resp = httpx.get(url, headers=_api_headers(), timeout=15)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        import sys
        print(f"[cleanup] list failed: {exc}", file=sys.stderr)
        return []

    raw = resp.json()
    docs: list[dict[str, Any]]
    if isinstance(raw, list):
        docs = raw
    elif isinstance(raw, dict):
        docs = raw.get("data") or raw.get("templates") or []
    else:
        docs = []

    return [
        d for d in docs
        if (d.get("templateID") or d.get("template_id") or "").startswith("snap-")
    ]


def delete_snaps(ids: list[str], timeout: float = 20.0) -> tuple[int, int]:
    """Delete a batch of snapshot templates. Returns (ok_count, fail_count)."""
    import sys

    ok = 0
    fail = 0
    for tid in ids:
        url = f"{_api_url()}/templates/{tid}"
        try:
            resp = httpx.delete(url, headers=_api_headers(), timeout=timeout)
            if resp.status_code in (200, 204):
                ok += 1
            else:
                fail += 1
                print(f"[cleanup] DELETE {tid} -> HTTP {resp.status_code}", file=sys.stderr)
        except httpx.HTTPError as exc:
            fail += 1
            print(f"[cleanup] DELETE {tid} failed: {exc}", file=sys.stderr)
    return ok, fail


def auto_cleanup_if_enabled() -> None:
    """After-benchmark cleanup hook - invoked from ``__main__.py``.

    Activated when ``CUBE_PERF_AUTO_CLEANUP=1`` (default: off).  Waits a
    few seconds for async operations to finish, then deletes all snap-*
    templates.
    """
    if os.environ.get("CUBE_PERF_AUTO_CLEANUP") != "1":
        return

    wait = float(os.environ.get("CUBE_PERF_AUTO_CLEANUP_WAIT", "3"))
    if wait > 0:
        time.sleep(wait)

    snaps = list_snaps()
    if not snaps:
        return

    ids = [
        str(s.get("templateID") or s.get("template_id", ""))
        for s in snaps
    ]
    ids = [i for i in ids if i]
    if not ids:
        return

    import sys
    print(f"\n[cleanup] auto-cleaning {len(ids)} snapshot(s) ...", file=sys.stderr)
    ok, fail = delete_snaps(ids)
    print(f"[cleanup] done: {ok} deleted, {fail} failed", file=sys.stderr)
