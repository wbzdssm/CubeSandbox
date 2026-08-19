#!/usr/bin/env python3
# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Self-test for the comparison engine.

A comparison tool that silently always reports "equivalent" is worse than no
tool at all. Since a real run can never fail (it only records), the ONLY thing
standing between a regression and a green result is this engine - so it is
exercised on synthetic runs first. Needs no services:

  python3 scripts/vfy_selftest.py
"""

from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_templatecenter import compare, outcome_of  # noqa: E402
from vfy_core import flatten, normalize  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name}  {detail}")
        FAILURES.append(name)


def envelope(ret_code: int = 200, **body):
    return {
        "http_status": 200,
        "elapsed_ms": 12,
        "ret_code": ret_code,
        "ret_name": "Success" if ret_code == 200 else "Err",
        "ret_msg": "",
        "error": None,
        "json": {"ret": {"ret_code": ret_code}, **body},
        "raw": "",
        "content_type": "application/json",
        "content_length": "",
    }


def rest(http_status: int = 200, error: str | None = None, **body):
    return {
        "http_status": http_status,
        "elapsed_ms": 12,
        "ret_code": None,
        "ret_name": None,
        "ret_msg": "",
        "error": error,
        "json": dict(body) if error is None else None,
        "raw": "",
        "content_type": "application/json",
        "content_length": "",
    }


def rec(seq, op, label="", response=None, db=None, facts=None, via="http"):
    return {
        "seq": seq, "op": op, "label": label, "via": via, "at_ms": seq * 100,
        "request": {"method": "GET", "url": "http://x/y", "path": "/y", "body": None},
        "response": response, "db": db, "facts": facts or {}, "note": "",
    }


JOB_ROW = {
    "job_id": "job-aaa", "template_id": "tpl-aaa", "status": "READY", "phase": "READY",
    "artifact_id": "rfs-aaa", "progress": "100", "error_message": "",
    "created_at": "2026-08-19 10:00:00",
}
ART_ROW = {
    "artifact_id": "rfs-aaa", "status": "READY", "ext4_size_bytes": "123456",
    "ext4_sha256": "deadbeef", "download_token": "tok-aaa", "cube_egress_ca_baked": "1",
}


def run(link: str, via: str = "http", **over):
    t = {
        "link": link,
        "via": via,
        "entry_url": f"http://{link}:8089",
        "started_at": "2026-08-19T10:00:00",
        "problems": [],
        "context": {
            "name": f"{link}-name",
            "job_id": "job-aaa",
            "template_id": "tpl-aaa",
            "artifact_id": "rfs-aaa",
            "terminal_status": "READY",
            "timeline": ["PENDING/", "RUNNING/PULLING", "READY/READY"],
            "saw_built_state": False,
            "artifact_reused_on_same_spec": True,
        },
        "records": [
            rec(1, "env.health", "health: CubeMaster", rest(200), via=via),
            rec(2, "probe.unknown_template", "unknown id",
                envelope(130404) if via == "http" else rest(404, error="TemplateNotFoundError: x"),
                via=via),
            rec(3, "create.submit", "submit",
                envelope(200, job={"status": "PENDING"}) if via == "http"
                else rest(200, build_id="job-aaa", status="PENDING"),
                facts={"job_id": "job-aaa", "template_id": "tpl-aaa", "status": "PENDING"},
                via=via),
            rec(4, "create.poll", "transition -> RUNNING/PULLING", None,
                db={"t_cube_template_image_job": [JOB_ROW]},
                facts={"transition": "RUNNING/PULLING", "poll_number": 2}, via=via),
            rec(5, "create.final_db", "settled fan-out", None,
                db={"t_cube_template_image_job": [JOB_ROW],
                    "t_cube_rootfs_artifact": [ART_ROW],
                    "t_cube_template_replica": [{"node_id": "n1", "status": "READY"}]},
                facts={"artifact_id": "rfs-aaa", "replica_rows": 1,
                       "replica_status": {"READY": 1}}, via=via),
            rec(6, "read.by_name", "by name",
                envelope(200, template_id="tpl-aaa") if via == "http"
                else rest(200, template_id="tpl-aaa"),
                facts={"resolved_template_id": "tpl-aaa",
                       "name_resolves_to_same_id": True}, via=via),
            rec(7, "read.download_head", "HEAD download", rest(200), via=via),
            rec(8, "delete.submit", "delete",
                envelope(200) if via == "http" else rest(200), via=via),
            rec(9, "delete.final_db", "post-delete rows", None,
                db={"t_cube_template_image_job": [],
                    "t_cube_template_replica": []},
                facts={"job_rows": 0, "replica_rows": 0}, via=via),
        ],
    }
    t.update(over)
    return t


base = run("master-local")

# 1) two runs that differ only in per-run values must be clean
hard, notes = compare(base, run("master-remote"))
check("identical links produce no divergence", not hard, f"got: {hard}")

# 2) the BUILT trajectory is the legitimate remote-mode difference and must be
#    tolerated, but still surfaced for the reader
b = run("master-remote")
b["context"]["timeline"] = ["PENDING/", "RUNNING/PULLING", "BUILT/READY", "READY/READY"]
b["context"]["saw_built_state"] = True
hard, notes = compare(base, b)
check("BUILT trajectory difference is tolerated", not hard, f"got: {hard}")
check("trajectory is surfaced as a note", any("trajectory" in n for n in notes), str(notes))

# 3) differing poll counts must not be treated as a divergence
b = run("master-remote")
b["records"].append(rec(10, "create.poll", "transition -> BUILT/READY", None,
                        facts={"transition": "BUILT/READY"}))
hard, _ = compare(base, b)
check("extra state transition is not a divergence", not hard, f"got: {hard}")

# 4) an outcome flip MUST be caught
b = run("master-remote")
b["records"][2]["response"] = envelope(130593)
hard, _ = compare(base, b)
check("outcome flip is caught", any("outcome differs" in h for h in hard), f"got: {hard}")

# 5) a missing response field MUST be caught (API contract drift)
b = run("master-remote")
b["records"][2]["response"]["json"] = {"ret": {"ret_code": 200}}
hard, _ = compare(base, b)
check("missing response field is caught",
      any("response fields differ" in h for h in hard), f"got: {hard}")

# 6) a missing DB row MUST be caught -- this is the case that would have exposed
#    "remote mode never wrote t_cube_rootfs_artifact"
b = run("master-remote")
b["records"][4]["db"]["t_cube_rootfs_artifact"] = []
hard, _ = compare(base, b)
check("missing artifact row is caught",
      any("t_cube_rootfs_artifact row count differs" in h for h in hard), f"got: {hard}")

# 7) a column that stops being written MUST be caught
b = run("master-remote")
art = copy.deepcopy(ART_ROW)
art["download_token"] = ""
b["records"][4]["db"]["t_cube_rootfs_artifact"] = [art]
hard, _ = compare(base, b)
check("emptied column is caught",
      any("download_token differs" in h for h in hard), f"got: {hard}")

# 8) a column disappearing from the row entirely MUST be caught
b = run("master-remote")
art = copy.deepcopy(ART_ROW)
art.pop("cube_egress_ca_baked")
b["records"][4]["db"]["t_cube_rootfs_artifact"] = [art]
hard, _ = compare(base, b)
check("dropped column is caught", any("columns differ" in h for h in hard), f"got: {hard}")

# 9) replica status regression MUST be caught
b = run("master-remote")
b["records"][4]["facts"]["replica_status"] = {"FAILED": 1}
hard, _ = compare(base, b)
check("replica status difference is caught",
      any("replica_status" in h for h in hard), f"got: {hard}")

# 10) rows left behind after a delete MUST be caught
b = run("master-remote")
b["records"][8]["db"]["t_cube_template_replica"] = [{"node_id": "n1", "status": "READY"}]
b["records"][8]["facts"]["replica_rows"] = 1
hard, _ = compare(base, b)
check("leftover rows after delete are caught",
      any("t_cube_template_replica row count differs" in h for h in hard), f"got: {hard}")

# 11) a terminal status regression MUST be caught
b = run("master-remote")
b["context"]["terminal_status"] = "FAILED"
hard, _ = compare(base, b)
check("terminal_status difference is caught",
      any("terminal_status differs" in h for h in hard), f"got: {hard}")

# 12) losing artifact reuse MUST be caught
b = run("master-remote")
b["context"]["artifact_reused_on_same_spec"] = False
hard, _ = compare(base, b)
check("artifact reuse regression is caught",
      any("artifact_reused_on_same_spec differs" in h for h in hard), f"got: {hard}")

# 13) name resolution breaking MUST be caught
b = run("master-remote")
b["records"][5]["facts"]["name_resolves_to_same_id"] = False
hard, _ = compare(base, b)
check("name resolution regression is caught",
      any("name_resolves_to_same_id" in h for h in hard), f"got: {hard}")

# 14) an op recorded by only one link is a note, not a divergence
b = run("master-remote")
b["records"] = [r for r in b["records"] if r["op"] != "read.download_head"]
hard, notes = compare(base, b)
check("missing op is reported as a note", not hard, f"got: {hard}")
check("missing op appears in notes",
      any("only recorded in" in n for n in notes), str(notes))

# 15) an op the link cannot support is a note, not a divergence
b = run("master-remote")
b["records"][6]["facts"] = {"unsupported": True}
b["records"][6]["response"] = None
hard, notes = compare(base, b)
check("unsupported op is not a divergence", not hard, f"got: {hard}")

# =====================================================================
# cross-dialect: SDK (CubeAPI HTTP codes) vs /cube/* (ret envelope)
# =====================================================================
sdk = run("sdk-local", via="sdk")

hard, notes = compare(base, sdk)
check("cross-dialect equivalent runs are clean", not hard, f"got: {hard}")
check("cross-dialect mode is announced",
      any("cross-dialect" in n for n in notes), str(notes))

# a DB divergence must still be caught across dialects: the DB is the one thing
# both links write identically
sdk_bad = run("sdk-local", via="sdk")
sdk_bad["records"][4]["db"]["t_cube_rootfs_artifact"] = []
hard, _ = compare(base, sdk_bad)
check("cross-dialect DB divergence is caught",
      any("t_cube_rootfs_artifact row count differs" in h for h in hard), f"got: {hard}")

# and so must an outcome divergence
sdk_bad = run("sdk-local", via="sdk")
sdk_bad["records"][1]["response"] = rest(500, error="ApiError: boom")
hard, _ = compare(base, sdk_bad)
check("cross-dialect outcome divergence is caught",
      any("outcome differs" in h for h in hard), f"got: {hard}")

# =====================================================================
# outcome vocabulary
# =====================================================================
check("ret NotFound and HTTP 404 map to the same outcome",
      outcome_of(envelope(130404)) == outcome_of(rest(404, error="TemplateNotFoundError: x"))
      == "not-found",
      f"{outcome_of(envelope(130404))} vs {outcome_of(rest(404, error='x'))}")
check("ret Success and HTTP 200 map to the same outcome",
      outcome_of(envelope(200)) == outcome_of(rest(200)) == "ok")
check("ret ParamsError and a local SDK ValueError both mean invalid",
      outcome_of(envelope(130400)) == outcome_of(rest(0, error="ValueError: image is required"))
      == "invalid",
      f"{outcome_of(rest(0, error='ValueError: image is required'))}")
check("a 5xx is server-error", outcome_of(rest(503)) == "server-error")
check("a dead socket is a transport error",
      outcome_of(rest(0, error="URLError: connection refused")) == "transport-error")
check("no response at all is distinguishable", outcome_of(None) == "no-call")

# =====================================================================
# normalization / flattening
# =====================================================================
n1 = normalize({"job_id": "job-abc", "status": "READY", "error_message": ""})
n2 = normalize({"job_id": "job-xyz", "status": "READY", "error_message": ""})
check("volatile ids normalize equal", n1 == n2, f"{n1} vs {n2}")
check("status is preserved verbatim", n1["status"] == "READY", str(n1))
check("zero vs non-zero survives normalization",
      normalize({"ext4_size_bytes": 0}) != normalize({"ext4_size_bytes": 12345}))
check("empty vs non-empty string survives normalization",
      normalize({"error_message": "boom"}) != normalize({"error_message": ""}))
check("flatten yields dotted paths",
      flatten({"a": {"b": [1, 2]}}) == {"a.b[0]": 1, "a.b[1]": 2},
      str(flatten({"a": {"b": [1, 2]}})))
check("flatten distinguishes empty containers",
      flatten({"a": []}) != flatten({"a": {}}),
      f"{flatten({'a': []})} vs {flatten({'a': {}})}")

# =====================================================================
# boundary tables and the policy-skip path
# =====================================================================
from vfy_boundaries import (  # noqa: E402
    ALIAS_MAX,
    ALIAS_TOO_LONG,
    HOSTILE_IDENTIFIERS,
    create_boundary_rows,
)
from vfy_links import OMIT, HTTP_FIELD_NAMES, SDK_FIELD_NAMES, _Omit  # noqa: E402

rows = create_boundary_rows("nginx:latest", "cubebox")
cases = [r["case"] for r in rows]

check("boundary cases are unique", len(cases) == len(set(cases)),
      f"duplicates: {[c for c in cases if cases.count(c) > 1]}")
check("every boundary row explains itself", all(r["why"] for r in rows))
check("every boundary row carries a cost flag",
      all(isinstance(r["costly"], bool) for r in rows))

# A boundary is only informative in pairs. If the accepted side were missing,
# "everything is rejected" would look identical to "the rule is enforced".
check("alias boundary covers the accepted side",
      any(c.startswith("alias-") and "too-long" not in c and "uppercase" not in c
          for c in cases))
check("alias length boundary is paired",
      len(ALIAS_MAX) == 64 and len(ALIAS_TOO_LONG) == 65,
      f"{len(ALIAS_MAX)} / {len(ALIAS_TOO_LONG)}")
check("port range boundary is paired on both ends",
      {"ports-min", "ports-max", "ports-zero", "ports-above-max"} <= set(cases))
check("custom port count boundary is paired",
      {"ports-three-custom", "ports-four-custom"} <= set(cases))
check("the reserved port exemption is covered",
      "ports-three-custom-plus-reserved" in cases)
check("reserved alias prefixes are covered and so is their near-miss",
      {"alias-reserved-tpl-prefix", "alias-reserved-snap-prefix",
       "alias-tpl-without-hyphen"} <= set(cases))
check("required fields are probed both omitted and empty",
      {"image-omitted", "image-empty", "size-omitted", "size-empty",
       "request-id-omitted", "request-id-empty"} <= set(cases))

# Rows that would trigger a real build must be tagged, or a default run would
# take hours; rows that cannot be accepted must NOT be tagged, or the cheap
# cases would be needlessly skipped.
costly = {r["case"] for r in rows if r["costly"]}
check("fingerprint-changing rows are marked costly",
      {"ports-min", "ports-max", "size-absurd", "instance-type-unknown"} <= costly,
      f"missing: {{'ports-min','ports-max','size-absurd','instance-type-unknown'}} - {costly}")
check("rows that can only be rejected are not marked costly",
      not ({"image-empty", "image-omitted", "ports-zero", "ports-above-max",
            "ports-four-custom", "alias-uppercase", "size-empty"} & costly),
      f"wrongly costly: {{'image-empty','ports-zero','alias-uppercase'}} & {costly}")

# OMIT must survive into "field absent", which is a different boundary from
# "field empty": the server may well answer differently to each.
omitted = [r for r in rows if isinstance(r["fields"].get("image"), _Omit)]
check("OMIT is used to express an absent field", len(omitted) == 1, str(len(omitted)))
check("OMIT is not equal to an empty string", OMIT != "" and OMIT is not None)
check("field maps cover every logical key used by the table",
      all(k in HTTP_FIELD_NAMES for r in rows for k in r["fields"]),
      f"unmapped: {{k for r in rows for k in r['fields']}} - {set(HTTP_FIELD_NAMES)}")
check("the SDK map omits request_id, which CubeAPI does not accept",
      "request_id" not in SDK_FIELD_NAMES and "request_id" in HTTP_FIELD_NAMES)

ids = [c for c, _ in HOSTILE_IDENTIFIERS]
check("hostile identifier cases are unique", len(ids) == len(set(ids)))
check("path traversal and SQL injection are both probed",
      {"path-traversal", "sql-tautology"} <= set(ids))
check("empty and whitespace identifiers are probed",
      {"empty", "whitespace"} <= set(ids))

# A row skipped by policy has no response. Comparing it against a submitted one
# must be a note, not a divergence, or every default-vs-full comparison would be
# flooded with false positives.
a_skip = run("master-local")
b_full = run("master-remote")
a_skip["records"].append(rec(20, "probe.create.ports-max", "65535 is valid", None,
                            facts={"skipped_by_policy": True, "costly": True}))
b_full["records"].append(rec(20, "probe.create.ports-max", "65535 is valid",
                            envelope(200), facts={"costly": True}))
hard, notes = compare(a_skip, b_full)
check("a policy-skipped row is not a divergence", not hard, f"got: {hard}")
check("a policy-skipped row is surfaced as a note",
      any("exercised in one run only" in n for n in notes), str(notes))

# But when both runs exercised it, a real difference must still be caught.
a_both = run("master-local")
b_both = run("master-remote")
a_both["records"].append(rec(20, "probe.create.ports-max", "65535 is valid",
                             envelope(200), facts={"costly": True}))
b_both["records"].append(rec(20, "probe.create.ports-max", "65535 is valid",
                             envelope(130400), facts={"costly": True}))
hard, _ = compare(a_both, b_both)
check("a boundary outcome flip is caught when both runs exercised it",
      any("outcome differs" in h for h in hard), f"got: {hard}")

# =====================================================================
# normalization of per-run values
#
# These two were found by running the harness against a stub server: both links
# behaved identically, yet the comparison reported eight divergences purely
# because two runs create different ids. A comparison tool that cries wolf on
# every run is as useless as one that never fires, so they are pinned here.
# =====================================================================
check("derived id keys normalize as ids",
      normalize({"resolved_template_id": "tpl-aaa"})
      == normalize({"resolved_template_id": "tpl-bbb"}),
      "a suffix like _template_id must be recognised, not just the bare key")
check("parent/child id keys normalize as ids",
      normalize({"parent_job_id": "job-1"}) == normalize({"parent_job_id": "job-2"}))
check("ret_msg normalizes: it embeds the id it is about",
      normalize({"ret_msg": "template 'tpl-aaa' not found"})
      == normalize({"ret_msg": "template 'tpl-bbb' not found"}))
check("error_message normalizes for the same reason",
      normalize({"error_message": "node 10.0.0.1 refused"})
      == normalize({"error_message": "node 10.0.0.2 refused"}))
# ...but the SHAPE must survive, or "it failed" would compare equal to "it did
# not". The semantic outcome is compared separately, from ret_code.
check("an empty message stays distinguishable from a non-empty one",
      normalize({"ret_msg": ""}) != normalize({"ret_msg": "boom"}))
check("an empty error stays distinguishable from a non-empty one",
      normalize({"error_message": ""}) != normalize({"error_message": "boom"}))

# =====================================================================
# environment-dependent list bodies, leaks, and build provenance
#
# All three come from a real run on a shared environment: it reported "a lot of
# diffs" that were entirely pre-existing templates shifting position, while the
# two findings that mattered (a 3-template leak, and no proof the remote link
# used TC at all) were not surfaced as findings at all.
# =====================================================================
LIST_BODY = {"ret": {"ret_code": 200},
             "data": [{"template_id": f"tpl-old{i}", "status": "READY"} for i in range(160)]}


def with_list(link, via="http", *, before=160, after=160, delta=0, **ctx):
    t = run(link, via=via)
    body_a = dict(LIST_BODY)
    t["records"].append(rec(30, "read.list_before", "before", rest(200, **body_a),
                            facts={"count": before}, via=via))
    t["records"].append(rec(31, "read.list_after", "after", rest(200, **body_a),
                            facts={"count": after}, via=via))
    t["records"].append(rec(32, "cleanup.leak_check", "after cleanup", rest(200, **body_a),
                            facts={"count": before + delta,
                                   "net_template_delta": delta,
                                   "leaked": delta != 0}, via=via))
    t["context"]["net_template_delta"] = delta
    t["context"].update(ctx)
    return t


# A shared environment holds a different number of pre-existing templates for
# each run, and their order is not stable. Neither may produce a divergence.
a_list = with_list("master-local", before=164, after=167)
b_list = with_list("master-remote", before=166, after=169)
b_list["records"][9]["response"]["json"]["data"].reverse()
hard, notes = compare(a_list, b_list)
check("a different pre-existing template count is not a divergence", not hard,
      f"got: {hard}")
check("a reordered list body is not a divergence",
      not any("data[" in h for h in hard), f"got: {hard}")

# The delta, however, is environment-independent and must be compared.
a_clean = with_list("master-local", delta=0)
b_leaky = with_list("master-remote", delta=3)
hard, _ = compare(a_clean, b_leaky)
check("a leak on one link only is caught",
      any("net_template_delta differs" in h for h in hard), f"got: {hard}")

# When both links leak the same amount there is no divergence, but staying
# silent about it would let the residue accumulate unnoticed.
hard, notes = compare(with_list("master-local", delta=3), with_list("master-remote", delta=3))
check("an equal leak on both links is not a divergence", not hard, f"got: {hard}")
check("an equal leak is still reported as a note",
      sum("leaked 3 template" in n for n in notes) == 2, str(notes))

# Build provenance is expected to differ between a local and a remote link, so
# it is a note — but it must be visible, because it is the only thing that says
# whether the remote link actually went through TC.
a_prov = with_list("master-local", remote_build_evidence="overwritten")
b_prov = with_list("master-remote", remote_build_evidence="confirmed")
hard, notes = compare(a_prov, b_prov)
check("differing build provenance is not a divergence", not hard, f"got: {hard}")
check("build provenance is surfaced as a note",
      any("remote_build_evidence" in n for n in notes), str(notes))

# Interpretation guardrails: a clean diff between two runs that both died early
# is narrow evidence, and a reused artifact means the build path never ran.
both_failed_a = with_list("master-local")
both_failed_b = with_list("master-remote")
for t in (both_failed_a, both_failed_b):
    t["context"]["terminal_status"] = "FAILED"
hard, notes = compare(both_failed_a, both_failed_b)
check("two runs failing at the same point is not a divergence", not hard, f"got: {hard}")
check("a shared early failure is flagged as narrow evidence",
      any("narrow evidence" in n for n in notes), str(notes))
check("a reused artifact is flagged as an unexercised build path",
      any("was not exercised" in n for n in notes), str(notes))

print()
if FAILURES:
    print(f"{len(FAILURES)} self-test(s) FAILED: {FAILURES}")
    sys.exit(1)
print("all self-tests passed")