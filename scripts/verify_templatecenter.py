#!/usr/bin/env python3
# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Dual-link verification harness for the CubeTemplateCenter split.

WHY THIS EXISTS
---------------
The template flow now has several shapes that MUST be externally
indistinguishable:

  master-local    CubeMaster does everything in-process (pre-split baseline)
  master-remote   CubeMaster validates/persists/distributes,
                  CubeTemplateCenter builds the ext4
  master-proxy    CubeMaster reverse-proxies /cube/template* to TC
  tc              Straight to CubeTemplateCenter (next-iteration preview)

A shell script cannot express what is actually needed: run the same API
sequence against different entry points, snapshot the database after each
step, then DIFF the shapes field by field. This harness therefore:

  1. Records a trace per shape: every HTTP call plus the DB state right after.
  2. Normalizes volatile values (ids, timestamps, tokens, sizes) so two runs
     are structurally comparable.
  3. Diffs traces and reports the FIRST divergence, which is usually the bug.

ZERO DEPENDENCIES: standard library only. DB access goes through
`docker exec <container> mysql`, same as scripts/test-templatecenter.sh.

USAGE
-----
  # verify one shape end to end
  ./scripts/verify_templatecenter.py run --shape master-local
  ./scripts/verify_templatecenter.py run --shape master-remote
  ./scripts/verify_templatecenter.py run --shape tc

  # record a baseline, then compare another shape against it
  ./scripts/verify_templatecenter.py run --shape master-local  --save baseline.json
  ./scripts/verify_templatecenter.py run --shape master-remote --compare baseline.json

  # diff two recorded traces
  ./scripts/verify_templatecenter.py diff baseline.json remote.json

  # read-only probing against a live environment (no writes, no builds)
  ./scripts/verify_templatecenter.py run --shape master-local --read-only

EXIT CODES
-----------
  0  all checks passed (and the diff was clean when comparing)
  1  a check failed, or a comparison found a divergence
  2  environment/configuration problem (service down, switch not set)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vfy_core as core  # noqa: E402
from vfy_cases import ORDER, Cases  # noqa: E402
from vfy_core import C, Config, SHAPES, fail, info, ok, section, warn  # noqa: E402
from vfy_verifier import Verifier  # noqa: E402

# Divergences that are EXPECTED between shapes: they describe the same
# outcome reached by a different internal route. Everything else must match.
EXPECTED_DIVERGENCE = {
    # remote mode passes through BUILT, local never does
    "context.saw_built_state",
    "phase_track",
    # entry point differs by definition
    "entry_url",
    "shape",
    "started_at",
    # alias/tag are per-run values
    "context.alias",
}


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested structures to dotted paths so a diff can name the exact
    field that diverged."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def step_index(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for s in trace.get("steps", [])}


def diff_traces(a: dict[str, Any], b: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Compare two traces. Returns (hard divergences, informational notes)."""
    hard: list[str] = []
    notes: list[str] = []

    label_a = a.get("shape", "A")
    label_b = b.get("shape", "B")

    ia, ib = step_index(a), step_index(b)

    only_a = sorted(set(ia) - set(ib))
    only_b = sorted(set(ib) - set(ia))
    for n in only_a:
        notes.append(f"step only in {label_a}: {n}")
    for n in only_b:
        notes.append(f"step only in {label_b}: {n}")

    # 1) Outcome per shared step must agree. A step passing in one shape and
    #    failing in the other is the strongest possible signal.
    for name in sorted(set(ia) & set(ib)):
        sa, sb = ia[name], ib[name]
        if sa.get("skipped") or sb.get("skipped"):
            if sa.get("skipped") != sb.get("skipped"):
                notes.append(f"step '{name}': skipped in only one shape "
                             f"({label_a}={sa.get('skipped')}, {label_b}={sb.get('skipped')})")
            continue
        if sa.get("passed") != sb.get("passed"):
            hard.append(f"step '{name}': passed differs "
                        f"({label_a}={sa.get('passed')}, {label_b}={sb.get('passed')})"
                        f"\n    {label_a}: {sa.get('detail', '')}"
                        f"\n    {label_b}: {sb.get('detail', '')}")
            continue

        # 2) Same business return code for the same call.
        ra, rb = sa.get("response") or {}, sb.get("response") or {}
        if ra and rb:
            if ra.get("ret_code") != rb.get("ret_code"):
                hard.append(f"step '{name}': ret_code differs "
                            f"({label_a}={ra.get('ret_name')}, {label_b}={rb.get('ret_name')})")
            if ra.get("body_keys") != rb.get("body_keys"):
                sa_keys = set(ra.get("body_keys") or [])
                sb_keys = set(rb.get("body_keys") or [])
                hard.append(f"step '{name}': response fields differ; "
                            f"only in {label_a}={sorted(sa_keys - sb_keys)}, "
                            f"only in {label_b}={sorted(sb_keys - sa_keys)}")
            elif ra.get("normalized_body") != rb.get("normalized_body"):
                fa = flatten(ra.get("normalized_body"))
                fb = flatten(rb.get("normalized_body"))
                for key in sorted(set(fa) | set(fb)):
                    if fa.get(key) != fb.get(key):
                        hard.append(f"step '{name}': body field '{key}' differs "
                                    f"({label_a}={fa.get(key)!r}, {label_b}={fb.get(key)!r})")

        # 3) Same DB fan-out.
        da, db_ = sa.get("db"), sb.get("db")
        if da and db_ and da != db_:
            fa, fb = flatten(da), flatten(db_)
            for key in sorted(set(fa) | set(fb)):
                if fa.get(key) != fb.get(key):
                    hard.append(f"step '{name}': db field '{key}' differs "
                                f"({label_a}={fa.get(key)!r}, {label_b}={fb.get(key)!r})")

    # 4) Final DB snapshot: the single most important structural comparison.
    ca = (a.get("context") or {}).get("final_snapshot")
    cb = (b.get("context") or {}).get("final_snapshot")
    if ca and cb and ca != cb:
        fa, fb = flatten(ca), flatten(cb)
        for key in sorted(set(fa) | set(fb)):
            if fa.get(key) != fb.get(key):
                hard.append(f"final DB snapshot: '{key}' differs "
                            f"({label_a}={fa.get(key)!r}, {label_b}={fb.get(key)!r})")

    # 5) Trajectories are expected to differ; surface them for the reader.
    ta = " -> ".join(a.get("phase_track") or [])
    tb = " -> ".join(b.get("phase_track") or [])
    if ta or tb:
        notes.append(f"trajectory {label_a}: {ta or '(none)'}")
        notes.append(f"trajectory {label_b}: {tb or '(none)'}")

    for key in ("saw_built_state", "artifact_reused_on_same_spec"):
        va = (a.get("context") or {}).get(key)
        vb = (b.get("context") or {}).get(key)
        if va is not None or vb is not None:
            notes.append(f"context.{key}: {label_a}={va}, {label_b}={vb}")

    hard = [h for h in hard if not any(h.startswith(f"final DB snapshot: '{e}'")
                                       or f"'{e}'" in h for e in EXPECTED_DIVERGENCE)]
    return hard, notes


def run_shape(cfg: Config, shape: str, read_only: bool, keep: bool) -> tuple[int, dict[str, Any]]:
    v = Verifier(cfg, shape, read_only)
    if not v.preflight():
        fail("preflight failed; not running the API suite")
        return 2, v.trace.to_dict()

    cases = Cases(v)
    try:
        for name, fn in ORDER:
            fn(cases)
    except KeyboardInterrupt:
        warn("interrupted")
    finally:
        if not keep:
            try:
                cases.cleanup()
            except Exception as e:  # noqa: BLE001
                warn(f"cleanup error: {e}")

    section(f"Summary - shape={shape}")
    t = v.trace
    total = len(t.steps)
    print(f"  total   : {total}")
    print(f"  {C.G}passed{C.X}  : {t.passed_count}")
    print(f"  {C.R}failed{C.X}  : {len(t.failed)}")
    print(f"  {C.Y}skipped{C.X} : {t.skipped_count}")
    if t.phase_track:
        print(f"  trajectory: {' -> '.join(t.phase_track)}")
    if t.failed:
        print(f"\n  {C.R}failed steps:{C.X}")
        for s in t.failed:
            print(f"    - {s.name}: {s.detail}")
    return (1 if t.failed else 0), t.to_dict()


def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config()
    if args.image:
        cfg.image = args.image
    if args.build_timeout:
        cfg.build_timeout = args.build_timeout

    code, trace = run_shape(cfg, args.shape, args.read_only, args.keep)

    if args.save:
        with open(args.save, "w") as f:
            json.dump(trace, f, indent=2, sort_keys=True, default=str)
        info(f"trace saved to {args.save}")

    if args.compare:
        if not os.path.exists(args.compare):
            fail(f"baseline not found: {args.compare}")
            return 2
        with open(args.compare) as f:
            baseline = json.load(f)
        section(f"Diff: {baseline.get('shape')} (baseline) vs {trace.get('shape')} (current)")
        hard, notes = diff_traces(baseline, trace)
        for n in notes:
            print(f"  {C.DIM}note{C.X}  {n}")
        if hard:
            print()
            for h in hard:
                fail(h)
            fail(f"{len(hard)} divergence(s) between shapes")
            return 1
        ok("no unexpected divergence between the two shapes")

    return code


def cmd_diff(args: argparse.Namespace) -> int:
    for p in (args.a, args.b):
        if not os.path.exists(p):
            fail(f"file not found: {p}")
            return 2
    with open(args.a) as f:
        a = json.load(f)
    with open(args.b) as f:
        b = json.load(f)
    section(f"Diff: {a.get('shape')} vs {b.get('shape')}")
    hard, notes = diff_traces(a, b)
    for n in notes:
        print(f"  {C.DIM}note{C.X}  {n}")
    if hard:
        print()
        for h in hard:
            fail(h)
        fail(f"{len(hard)} divergence(s)")
        return 1
    ok("traces are structurally equivalent")
    return 0


def cmd_shapes(_: argparse.Namespace) -> int:
    section("Available shapes")
    for name, meta in SHAPES.items():
        print(f"  {C.B}{name}{C.X}")
        print(f"    {meta['desc']}")
        print(f"    {C.DIM}requires: {meta['requires']}{C.X}")
    print()
    info("recommended sequence (all three must agree):")
    print("  1) ./scripts/verify_templatecenter.py run --shape master-local  --save /tmp/base.json")
    print("  2) ./scripts/verify_templatecenter.py run --shape master-remote --compare /tmp/base.json")
    print("  3) ./scripts/verify_templatecenter.py run --shape tc            --compare /tmp/base.json")
    return 0


def main() -> int:
    global_parser = argparse.ArgumentParser(
        description="Dual-link verification for the CubeTemplateCenter split",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    global_parser.add_argument("-v", "--verbose", action="store_true")
    sub = global_parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the API suite against one shape")
    p_run.add_argument("--shape", required=True, choices=sorted(SHAPES))
    p_run.add_argument("--save", metavar="FILE", help="write the trace to FILE")
    p_run.add_argument("--compare", metavar="FILE", help="diff against a recorded trace")
    p_run.add_argument("--read-only", action="store_true",
                       help="skip every mutating case (safe on a live environment)")
    p_run.add_argument("--keep", action="store_true",
                       help="do not delete templates created during the run")
    p_run.add_argument("--image", help="override the source image")
    p_run.add_argument("--build-timeout", type=int, help="seconds to wait for a build")
    p_run.set_defaults(func=cmd_run)

    p_diff = sub.add_parser("diff", help="diff two recorded traces")
    p_diff.add_argument("a")
    p_diff.add_argument("b")
    p_diff.set_defaults(func=cmd_diff)

    p_shapes = sub.add_parser("shapes", help="list shapes and the recommended sequence")
    p_shapes.set_defaults(func=cmd_shapes)

    args = global_parser.parse_args()
    core._VERBOSE = args.verbose
    started = time.time()
    try:
        code = args.func(args)
    except KeyboardInterrupt:
        warn("interrupted")
        return 130
    print(f"\n{C.DIM}elapsed {time.time() - started:.1f}s{C.X}")
    return code


if __name__ == "__main__":
    sys.exit(main())
