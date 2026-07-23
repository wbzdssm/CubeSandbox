# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0

"""Clean up snap-* snapshot templates left behind by perf runs."""

from __future__ import annotations

from cubesandbox import Config, Template


def list_snaps(config: Config | None = None) -> list[dict]:
    """Return snap-* templates, newest first."""
    templates = Template.list(config=config)
    snaps = [t for t in templates if _is_snap(t)]
    snaps.sort(key=lambda t: t.get("createdAt", ""), reverse=True)
    return snaps


def delete_snaps(template_ids: list[str], config: Config | None = None) -> tuple[int, int]:
    """Delete snapshot templates. Returns (ok, fail)."""
    ok = 0
    fail = 0
    for tid in template_ids:
        try:
            Template.delete(tid, config=config)
            ok += 1
        except Exception:
            fail += 1
    return ok, fail


def _is_snap(t: dict) -> bool:
    tid = t.get("templateID", "")
    return isinstance(tid, str) and tid.startswith("snap-")
