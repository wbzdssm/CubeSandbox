#!/usr/bin/env python3
"""Clean up snap-* snapshot templates from the CubeMaster backend.

Usage:
    python3 cleanup_snapshots.py --help
    CUBE_API_URL=http://9.135.79.34:3000 python3 cleanup_snapshots.py --dry-run
    CUBE_API_URL=http://9.135.79.34:3000 python3 cleanup_snapshots.py

Can also be imported and used programmatically:
    from .cleanup_snapshots import list_snaps, delete_snaps
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import requests


def _api_url() -> str:
    return os.environ.get("CUBE_API_URL", "http://127.0.0.1:3000").rstrip("/")


def list_all_templates(api_url: str | None = None) -> list[dict]:
    """GET /templates — return the raw template list."""
    url = (api_url or _api_url()) + "/templates"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def list_snaps(api_url: str | None = None) -> list[dict]:
    """Filter templates to only those with templateID starting with 'snap-'."""
    return [t for t in list_all_templates(api_url) if t.get("templateID", "").startswith("snap-")]


def delete_snaps(
    snapshot_ids: list[str],
    *,
    api_url: str | None = None,
    verbose: bool = True,
) -> tuple[int, int]:
    """Delete snapshots by templateID. Returns (deleted_count, failed_count)."""
    base = (api_url or _api_url()) + "/templates"
    ok = 0
    fail = 0
    for tid in snapshot_ids:
        try:
            resp = requests.delete(f"{base}/{tid}", timeout=30)
            if resp.status_code == 200:
                ok += 1
                if verbose:
                    print(f"  DELETED {tid}")
            else:
                fail += 1
                if verbose:
                    print(f"  FAIL {tid}  HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            fail += 1
            if verbose:
                print(f"  ERR  {tid}  {exc}")
    return ok, fail


def _main():
    parser = argparse.ArgumentParser(
        description="Clean up snap-* snapshot templates from CubeMaster.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list snapshots only, do not delete",
    )
    parser.add_argument(
        "--older-than",
        type=int,
        default=0,
        metavar="MINUTES",
        help="only delete snapshots older than N minutes (0 = all)",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="CubeAPI URL (default: $CUBE_API_URL or http://127.0.0.1:3000)",
    )
    args = parser.parse_args()

    api_url = args.api_url or _api_url()
    print(f"API URL: {api_url}")

    try:
        templates = list_all_templates(api_url)
    except Exception as exc:
        sys.exit(f"Failed to list templates: {exc}")

    snaps = list_snaps(api_url)
    non_snaps = [t for t in templates if not t.get("templateID", "").startswith("snap-")]

    print(f"Total templates : {len(templates)}")
    print(f"snap-* templates: {len(snaps)}")
    print(f"non-snap        : {len(non_snaps)}")

    if not snaps:
        print("No snap-* templates to clean up.")
        return

    # Older-than filter
    if args.older_than > 0:
        cutoff_ts = time.time() - args.older_than * 60
        filtered: list[dict] = []
        skipped = 0
        for s in snaps:
            created = s.get("createdAt", "")
            if created:
                try:
                    # Parse ISO 8601 like "2026-07-21T06:03:42Z"
                    created = created.replace("Z", "+00:00")
                    dt = time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")
                    ts = time.mktime(dt) - time.timezone  # rough UTC
                    if ts < cutoff_ts:
                        filtered.append(s)
                    else:
                        skipped += 1
                        continue
                except ValueError:
                    filtered.append(s)  # can't parse → include
            else:
                filtered.append(s)
        print(f"  older than {args.older_than} min: {len(filtered)}  skipped: {skipped}")
        snaps = filtered
        if not snaps:
            print("No snapshots match the age filter.")
            return

    print("\nSnapshot templates:")
    for s in snaps:
        print(f"  {s['templateID']:<36} {s.get('status',''):<12} {s.get('createdAt','')}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would delete {len(snaps)} snapshots. Remove --dry-run to execute.")
        return

    if not sys.stdin.isatty():
        print(f"\n⚠  About to delete {len(snaps)} snapshots. Pipe detected, proceeding...")
    else:
        try:
            answer = input(f"\n⚠  Delete {len(snaps)} snapshots? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return

    print(f"\nDeleting {len(snaps)} snapshots ...")
    ids = [s["templateID"] for s in snaps]
    ok, fail = delete_snaps(ids, api_url=api_url)
    print(f"\nDone: {ok} deleted, {fail} failed.")


if __name__ == "__main__":
    _main()
