#!/usr/bin/env python3
# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Dual-link verification for the CubeTemplateCenter split.

WHAT THIS IS
------------
The template flow can now be reached through several routes that MUST be
externally indistinguishable:

  master-local    /cube/* on CubeMaster, build in-process (pre-split baseline)
  master-remote   /cube/* on CubeMaster, ext4 built by CubeTemplateCenter
  master-proxy    /cube/* on CubeMaster, reverse-proxied to CubeTemplateCenter
  tc              /cube/* straight to CubeTemplateCenter
  sdk-local       Python SDK -> CubeAPI -> CubeMaster, build in-process
  sdk-remote      Python SDK -> CubeAPI -> CubeMaster -> CubeTemplateCenter

RECORD, OUTPUT, THEN COMPARE - IN THAT ORDER
--------------------------------------------
This tool does not assert. `run` walks the whole lifecycle (create, every state
transition, all read paths, rebuild, dedup, delete, post-delete) and prints the
RAW data of each step: the request as sent, the response verbatim, and
`SELECT *` of every DB row involved. A run therefore has no pass/fail - it
either produced usable records or it reports why it could not.

The verdict comes from `compare`, which diffs two recorded runs field by field.

  same dialect  (master-* vs master-*, sdk-* vs sdk-*)
                responses, DB rows and extracted facts are all compared.
  cross dialect (master-* vs sdk-*)
                /cube/* speaks a ret envelope while CubeAPI speaks HTTP status
                codes, so bodies are not comparable; the semantic outcome, the
                DB fan-out and the counts are.

USAGE
-----
  # record the baseline, printing raw data as it goes
  ./scripts/verify_templatecenter.py run --link master-local  --out /tmp/local.json

  # record the remote link, then diff it against the baseline
  ./scripts/verify_templatecenter.py run --link master-remote --out /tmp/remote.json
  ./scripts/verify_templatecenter.py compare /tmp/local.json /tmp/remote.json

  # or record and compare in one go
  ./scripts/verify_templatecenter.py run --link master-remote --compare /tmp/local.json

  # the SDK link (what a user actually runs), against both build modes
  ./scripts/verify_templatecenter.py run --link sdk-local  --out /tmp/sdk-local.json
  ./scripts/verify_templatecenter.py run --link sdk-remote --out /tmp/sdk-remote.json
  ./scripts/verify_templatecenter.py compare /tmp/sdk-local.json /tmp/sdk-remote.json

  # SDK vs raw HTTP: cross-dialect, so DB fan-out and outcomes are compared
  ./scripts/verify_templatecenter.py compare /tmp/local.json /tmp/sdk-local.json

  # re-print the raw records of a recorded run
  ./scripts/verify_templatecenter.py show /tmp/remote.json --full

EXIT CODES
----------
  0  recorded cleanly / no unexpected divergence
  1  a divergence was found when comparing
  2  the run could not produce comparable records (service down, switch unset)
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
from vfy_cases import ORDER, Scenario  # noqa: E402
from vfy_core import (  # noqa: E402
    LINKS,
    C,
    Config,
    Record,
    fail,
    flatten,
    info,
    normalize,
    ok,
    render_record,
    section,
    warn,
)
from vfy_verifier import Recorder  # noqa: E402

# Facts that legitimately differ between links: they describe the internal
# route taken, not the outcome. Everything else must match.
EXPECTED_FACT_DIVERGENCE = {
    "timeline",          # remote passes through BUILT, local does not
    "saw_built_state",
    "transitions",
    "poll_number",
    "attempts",
    "checks",            # /health composition depends on the TC switch
    "unsupported",
    "exception",
    "identifier",        # echoed back for readability, not an outcome
    "boundary",
    "why",
}

# Boundary-phase bookkeeping. Whether a costly row was submitted depends on
# --full-boundaries, which is an operator choice rather than a property of the
# link, so comparing two runs made with different policies must not drown in it.
POLICY_FACTS = {"skipped_by_policy", "costly", "full_boundaries", "submitted",
                "rows", "accepted"}
EXPECTED_FACT_DIVERGENCE |= POLICY_FACTS

# Recorded per state transition, so the count differs by design. Compared as a
# trajectory note instead of record by record.
NON_ALIGNED_OPS = {"create.poll"}

# Environment observations, not behavior.
ENV_OPS_PREFIX = "env."


# --------------------------------------------------------------------------
# Outcome classification
#
# /cube/* answers HTTP 200 and puts the outcome in ret.ret_code; CubeAPI uses
# real status codes and the SDK raises. Reducing both to the same small
# vocabulary is what makes a cross-dialect comparison possible at all.
# --------------------------------------------------------------------------
RET_TO_OUTCOME = {
    200: "ok",
    130400: "invalid",
    130404: "not-found",
    130409: "conflict",
    130593: "server-error",
    130594: "server-error",
}

STATUS_TO_OUTCOME = {404: "not-found", 400: "invalid", 422: "invalid", 409: "conflict"}


def outcome_of(resp: dict[str, Any] | None) -> str:
    if not resp:
        return "no-call"
    err = str(resp.get("error") or "")
    if err:
        status = resp.get("http_status") or 0
        # The SDK validates some inputs locally and raises before sending, which
        # is an "invalid" outcome reached without a round trip.
        if err.startswith(("ValueError", "TypeError")):
            return "invalid"
        if status in STATUS_TO_OUTCOME:
            return STATUS_TO_OUTCOME[status]
        if not status:
            return "transport-error"
        return "server-error" if status >= 500 else f"http-{status}"

    ret = resp.get("ret_code")
    if ret is not None:
        return RET_TO_OUTCOME.get(int(ret), f"ret-{ret}")

    status = int(resp.get("http_status") or 0)
    if 200 <= status < 300:
        return "ok"
    if status in STATUS_TO_OUTCOME:
        return STATUS_TO_OUTCOME[status]
    if status >= 500:
        return "server-error"
    return f"http-{status}" if status else "transport-error"


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------
def _records(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return list(trace.get("records") or [])


def _align(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> tuple[
        list[tuple[dict, dict]], list[dict], list[dict]]:
    """Pair records by (op, n-th occurrence of that op).

    Alignment is by op rather than by position because links legitimately emit
    different numbers of polls and skips; op identity is stable by contract.
    """
    def buckets(recs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for r in recs:
            op = r.get("op", "")
            if op in NON_ALIGNED_OPS or op.startswith(ENV_OPS_PREFIX):
                continue
            out.setdefault(op, []).append(r)
        return out

    ba, bb = buckets(a), buckets(b)
    pairs: list[tuple[dict, dict]] = []
    only_a: list[dict] = []
    only_b: list[dict] = []
    for op in sorted(set(ba) | set(bb)):
        la, lb = ba.get(op, []), bb.get(op, [])
        for i in range(max(len(la), len(lb))):
            if i < len(la) and i < len(lb):
                pairs.append((la[i], lb[i]))
            elif i < len(la):
                only_a.append(la[i])
            else:
                only_b.append(lb[i])
    return pairs, only_a, only_b


def _diff_mapping(kind: str, op: str, va: Any, vb: Any,
                  la: str, lb: str, skip: set[str] | None = None) -> list[str]:
    out: list[str] = []
    fa, fb = flatten(normalize(va)), flatten(normalize(vb))
    for key in sorted(set(fa) | set(fb)):
        head = key.split(".")[0].split("[")[0]
        if skip and (head in skip or key in skip):
            continue
        if fa.get(key) != fb.get(key):
            out.append(f"{op}: {kind} '{key}' differs "
                       f"({la}={fa.get(key)!r}, {lb}={fb.get(key)!r})")
    return out


def _diff_db(op: str, da: dict | None, db_: dict | None, la: str, lb: str) -> list[str]:
    """The DB is dialect-independent: both links write the same tables, so this
    comparison is valid even across dialects and is the strongest signal here."""
    out: list[str] = []
    if not da and not db_:
        return out
    da, db_ = da or {}, db_ or {}
    for table in sorted(set(da) | set(db_)):
        rows_a = da.get(table) or []
        rows_b = db_.get(table) or []
        if not isinstance(rows_a, list) or not isinstance(rows_b, list):
            continue
        if len(rows_a) != len(rows_b):
            out.append(f"{op}: db {table} row count differs "
                       f"({la}={len(rows_a)}, {lb}={len(rows_b)})")
            continue
        for i, (ra, rb) in enumerate(zip(rows_a, rows_b)):
            cols_a, cols_b = set(ra), set(rb)
            if cols_a != cols_b:
                out.append(f"{op}: db {table}[{i}] columns differ; "
                           f"only in {la}={sorted(cols_a - cols_b)}, "
                           f"only in {lb}={sorted(cols_b - cols_a)}")
                continue
            na, nb = normalize(ra), normalize(rb)
            for col in sorted(cols_a):
                if na.get(col) != nb.get(col):
                    out.append(f"{op}: db {table}[{i}].{col} differs "
                               f"({la}={na.get(col)!r}, {lb}={nb.get(col)!r})")
    return out


def compare(a: dict[str, Any], b: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Diff two recorded runs. Returns (divergences, notes)."""
    la = a.get("link", "A")
    lb = b.get("link", "B")
    same_dialect = a.get("via") == b.get("via")

    hard: list[str] = []
    notes: list[str] = []

    notes.append(f"{la}: via={a.get('via')} entry={a.get('entry_url')} at {a.get('started_at')}")
    notes.append(f"{lb}: via={b.get('via')} entry={b.get('entry_url')} at {b.get('started_at')}")
    if not same_dialect:
        notes.append("cross-dialect comparison: response bodies are not comparable "
                     "(/cube/* ret envelope vs CubeAPI HTTP status); comparing "
                     "semantic outcomes, DB fan-out and facts only")

    for label, trace in ((la, a), (lb, b)):
        for p in trace.get("problems") or []:
            notes.append(f"{label} reported a problem: {p}")

    pairs, only_a, only_b = _align(_records(a), _records(b))
    for rec in only_a:
        notes.append(f"op only recorded in {la}: {rec.get('op')} ({rec.get('label')})")
    for rec in only_b:
        notes.append(f"op only recorded in {lb}: {rec.get('op')} ({rec.get('label')})")

    for ra, rb in pairs:
        op = ra.get("op", "")
        fa = ra.get("facts") or {}
        fb = rb.get("facts") or {}

        if fa.get("unsupported") or fb.get("unsupported"):
            if bool(fa.get("unsupported")) != bool(fb.get("unsupported")):
                notes.append(f"{op}: unsupported by one link only "
                             f"({la}={bool(fa.get('unsupported'))}, "
                             f"{lb}={bool(fb.get('unsupported'))})")
            continue

        # A row skipped by --full-boundaries policy has no response at all, so
        # comparing outcomes would report "no-call vs ok" for what is really just
        # a different operator choice. Skipped on either side means the pair
        # carries no information.
        if fa.get("skipped_by_policy") or fb.get("skipped_by_policy"):
            if bool(fa.get("skipped_by_policy")) != bool(fb.get("skipped_by_policy")):
                notes.append(f"{op}: exercised in one run only "
                             f"({la} skipped={bool(fa.get('skipped_by_policy'))}, "
                             f"{lb} skipped={bool(fb.get('skipped_by_policy'))}); "
                             "pass --full-boundaries to both runs to compare it")
            continue

        # 1) Semantic outcome. A call that succeeds on one link and fails on the
        #    other is the strongest possible signal, in any dialect.
        oa, ob = outcome_of(ra.get("response")), outcome_of(rb.get("response"))
        if oa != ob:
            hard.append(f"{op}: outcome differs ({la}={oa}, {lb}={ob})"
                        f"\n    {la}: {_short(ra)}"
                        f"\n    {lb}: {_short(rb)}")
            continue

        # 2) Response body, same dialect only.
        if same_dialect:
            ja, jb = (ra.get("response") or {}).get("json"), (rb.get("response") or {}).get("json")
            if isinstance(ja, dict) and isinstance(jb, dict):
                ka, kb = set(ja), set(jb)
                if ka != kb:
                    hard.append(f"{op}: response fields differ; "
                                f"only in {la}={sorted(ka - kb)}, "
                                f"only in {lb}={sorted(kb - ka)}")
                else:
                    hard.extend(_diff_mapping("body", op, ja, jb, la, lb))

        # 3) DB fan-out: valid in every dialect.
        hard.extend(_diff_db(op, ra.get("db"), rb.get("db"), la, lb))

        # 4) Extracted facts.
        hard.extend(_diff_mapping("fact", op, fa, fb, la, lb,
                                  skip=EXPECTED_FACT_DIVERGENCE))

    # 5) Trajectories differ by design; surface them so a human can read them.
    ta = (a.get("context") or {}).get("timeline") or []
    tb = (b.get("context") or {}).get("timeline") or []
    notes.append(f"trajectory {la}: {' -> '.join(ta) or '(none)'}")
    notes.append(f"trajectory {lb}: {' -> '.join(tb) or '(none)'}")

    for key in ("terminal_status", "saw_built_state", "artifact_reused_on_same_spec"):
        va = (a.get("context") or {}).get(key)
        vb = (b.get("context") or {}).get(key)
        if va is None and vb is None:
            continue
        if key == "terminal_status" and va != vb:
            hard.append(f"context: terminal_status differs ({la}={va}, {lb}={vb})")
        elif key == "artifact_reused_on_same_spec" and va != vb:
            hard.append(f"context: artifact_reused_on_same_spec differs ({la}={va}, {lb}={vb})")
        else:
            notes.append(f"context.{key}: {la}={va}, {lb}={vb}")

    return hard, notes


def _short(rec: dict[str, Any]) -> str:
    resp = rec.get("response") or {}
    bits = [f"HTTP {resp.get('http_status')}"]
    if resp.get("ret_name"):
        bits.append(f"ret={resp.get('ret_name')}")
    if resp.get("ret_msg"):
        bits.append(f"msg={str(resp.get('ret_msg'))[:120]!r}")
    if resp.get("error"):
        bits.append(f"error={str(resp.get('error'))[:160]}")
    return " ".join(bits)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config()
    if args.image:
        cfg.image = args.image
    if args.build_timeout:
        cfg.build_timeout = args.build_timeout
    if args.full_boundaries:
        cfg.full_boundaries = True

    rec = Recorder(cfg, args.link, print_raw=not args.quiet_raw,
                   print_limit=0 if args.full else args.print_limit)
    usable = rec.capture_environment()

    scenario = Scenario(rec)
    if usable:
        try:
            for _, fn in ORDER:
                fn(scenario)
        except KeyboardInterrupt:
            rec.problem("interrupted by the operator")
        finally:
            if not args.keep:
                try:
                    scenario.cleanup()
                except Exception as e:  # noqa: BLE001
                    warn(f"cleanup error: {e}")
    else:
        rec.problem("environment is not usable; the lifecycle was not recorded")

    rec.summarize()
    trace = rec.trace.to_dict()

    out_path = args.out
    if out_path:
        with open(out_path, "w") as f:
            json.dump(trace, f, indent=2, sort_keys=True, default=str)
        info(f"raw records written to {out_path} ({len(trace['records'])} records)")

    exit_code = 0 if usable and not rec.trace.problems else 2

    if args.compare:
        if not os.path.exists(args.compare):
            fail(f"baseline not found: {args.compare}")
            return 2
        with open(args.compare) as f:
            baseline = json.load(f)
        exit_code = max(exit_code, _report(baseline, trace))

    return exit_code


def cmd_compare(args: argparse.Namespace) -> int:
    for p in (args.a, args.b):
        if not os.path.exists(p):
            fail(f"file not found: {p}")
            return 2
    with open(args.a) as f:
        a = json.load(f)
    with open(args.b) as f:
        b = json.load(f)
    return _report(a, b)


def _report(a: dict[str, Any], b: dict[str, Any]) -> int:
    section(f"Compare: {a.get('link')} vs {b.get('link')}")
    hard, notes = compare(a, b)
    for n in notes:
        print(f"  {C.DIM}note{C.X}  {n}")
    if hard:
        print()
        for h in hard:
            fail(h)
        fail(f"{len(hard)} divergence(s) between the two links")
        return 1
    print()
    ok("no unexpected divergence between the two links")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    if not os.path.exists(args.file):
        fail(f"file not found: {args.file}")
        return 2
    with open(args.file) as f:
        trace = json.load(f)
    section(f"Raw records: {trace.get('link')} (via {trace.get('via')})")
    limit = 0 if args.full else args.print_limit
    for raw in trace.get("records") or []:
        if args.op and not str(raw.get("op", "")).startswith(args.op):
            continue
        print(render_record(Record(**raw), limit))
    for p in trace.get("problems") or []:
        warn(f"problem: {p}")
    return 0


def cmd_links(_: argparse.Namespace) -> int:
    section("Links")
    for name, meta in LINKS.items():
        print(f"  {C.B}{name}{C.X}  ({meta['via']})")
        print(f"    {meta['desc']}")
        print(f"    {C.DIM}requires: {meta['requires']}{C.X}")
    print()
    info("recommended sequence")
    print("  # 1. HTTP baseline vs remote build")
    print("  run --link master-local  --out /tmp/local.json")
    print("  run --link master-remote --compare /tmp/local.json")
    print("  # 2. proxy + direct TC (needs CUBE_TC_SERVE_TEMPLATE_API=true)")
    print("  run --link master-proxy  --compare /tmp/local.json")
    print("  run --link tc            --compare /tmp/local.json")
    print("  # 3. the SDK path a user actually runs, in both build modes")
    print("  run --link sdk-local     --out /tmp/sdk-local.json")
    print("  run --link sdk-remote    --compare /tmp/sdk-local.json")
    print("  # 4. SDK vs raw HTTP (cross-dialect: DB fan-out + outcomes)")
    print("  compare /tmp/local.json /tmp/sdk-local.json")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record, output and compare the template lifecycle across links",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="walk the lifecycle through one link and record raw data")
    p_run.add_argument("--link", required=True, choices=sorted(LINKS))
    p_run.add_argument("--out", metavar="FILE", help="write the raw records to FILE")
    p_run.add_argument("--compare", metavar="FILE", help="compare against a recorded run")
    p_run.add_argument("--keep", action="store_true",
                       help="do not delete templates created during the run")
    p_run.add_argument("--quiet-raw", action="store_true",
                       help="do not print raw records while running")
    p_run.add_argument("--full", action="store_true",
                       help="print raw values untruncated")
    p_run.add_argument("--print-limit", type=int, default=1200,
                       help="characters per raw value when printing (default 1200)")
    p_run.add_argument("--image", help="override the source image")
    p_run.add_argument("--build-timeout", type=int, help="seconds to wait for a build")
    p_run.add_argument("--full-boundaries", action="store_true",
                       help="also submit boundary rows whose acceptance starts a "
                            "distinct full build (slow: every exposed_ports / "
                            "writable_layer_size variant is its own fingerprint)")
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="compare two recorded runs")
    p_cmp.add_argument("a")
    p_cmp.add_argument("b")
    p_cmp.set_defaults(func=cmd_compare)

    # Kept because the earlier revision of this tool used `diff`.
    p_diff = sub.add_parser("diff", help="alias of compare")
    p_diff.add_argument("a")
    p_diff.add_argument("b")
    p_diff.set_defaults(func=cmd_compare)

    p_show = sub.add_parser("show", help="re-print the raw records of a recorded run")
    p_show.add_argument("file")
    p_show.add_argument("--op", help="only records whose op starts with this prefix")
    p_show.add_argument("--full", action="store_true", help="print values untruncated")
    p_show.add_argument("--print-limit", type=int, default=1200)
    p_show.set_defaults(func=cmd_show)

    p_links = sub.add_parser("links", help="list links and the recommended sequence")
    p_links.set_defaults(func=cmd_links)

    args = parser.parse_args()
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
