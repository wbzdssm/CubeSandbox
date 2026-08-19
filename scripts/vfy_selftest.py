#!/usr/bin/env python3
# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Self-test for the verification harness.

A comparison tool that silently always reports "equivalent" is worse than no
tool at all, so this exercises the diff engine on synthetic traces before it is
trusted against a real environment. Run it anywhere; it needs no services.

  python3 scripts/vfy_selftest.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_templatecenter import diff_traces, flatten  # noqa: E402
from vfy_core import normalize  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name}  {detail}")
        FAILURES.append(name)


def trace(shape: str, **over):
    """Build a minimal but realistic trace."""
    t = {
        "shape": shape,
        "entry_url": f"http://{shape}:8089",
        "started_at": "2026-08-19T10:00:00",
        "phase_track": ["RUNNING/PULLING", "READY/READY"],
        "context": {
            "alias": f"{shape}-alias",
            "saw_built_state": False,
            "final_snapshot": {
                "job": {"status": "READY", "phase": "READY", "job_id": "<value>"},
                "artifact": {"status": "READY", "has_token": "1"},
                "replica_count": 1,
                "replica_status_histogram": {"READY": 1},
            },
        },
        "steps": [
            {
                "name": "POST from-image: accepted",
                "api": "POST /cube/template/from-image",
                "passed": True,
                "skipped": False,
                "detail": "",
                "response": {
                    "ret_code": 200,
                    "ret_name": "Success",
                    "body_keys": ["job", "requestID", "ret"],
                    "normalized_body": {"job": {"status": "PENDING"}},
                },
                "db": {"job": {"status": "PENDING"}},
                "notes": [],
            },
            {
                "name": "job reaches READY",
                "api": "GET /cube/template/from-image",
                "passed": True,
                "skipped": False,
                "detail": "",
                "response": None,
                "db": None,
                "notes": [],
            },
        ],
    }
    t.update(over)
    return t


# 1) identical traces (modulo per-shape values) must be clean
a = trace("master-local")
b = trace("master-remote")
hard, notes = diff_traces(a, b)
check("identical shapes produce no divergence", not hard, f"got: {hard}")

# 2) trajectory + saw_built_state differences must be tolerated: this is the
#    legitimate remote-mode difference and must NOT be reported as a failure
b2 = trace("master-remote")
b2["phase_track"] = ["RUNNING/PULLING", "BUILT/READY", "READY/READY"]
b2["context"]["saw_built_state"] = True
hard, notes = diff_traces(a, b2)
check("BUILT trajectory difference is tolerated", not hard, f"got: {hard}")
check("trajectory is surfaced as a note",
      any("trajectory" in n for n in notes), f"notes: {notes}")

# 3) a step outcome flip MUST be caught
b3 = trace("master-remote")
b3["steps"][1]["passed"] = False
b3["steps"][1]["detail"] = "terminal status=FAILED"
hard, _ = diff_traces(a, b3)
check("step outcome flip is caught", any("passed differs" in h for h in hard), f"got: {hard}")

# 4) a ret_code difference MUST be caught
b4 = trace("master-remote")
b4["steps"][0]["response"]["ret_code"] = 130400
b4["steps"][0]["response"]["ret_name"] = "MasterParamsError"
hard, _ = diff_traces(a, b4)
check("ret_code difference is caught", any("ret_code differs" in h for h in hard), f"got: {hard}")

# 5) a missing response field MUST be caught (API contract drift)
b5 = trace("master-remote")
b5["steps"][0]["response"]["body_keys"] = ["requestID", "ret"]
hard, _ = diff_traces(a, b5)
check("missing response field is caught",
      any("response fields differ" in h for h in hard), f"got: {hard}")

# 6) a DB fan-out difference MUST be caught -- this is the case that would
#    have exposed "remote mode never wrote rootfs_artifacts"
b6 = trace("master-remote")
b6["context"]["final_snapshot"]["artifact"] = {}
hard, _ = diff_traces(a, b6)
check("missing artifact row is caught",
      any("final DB snapshot" in h for h in hard), f"got: {hard}")

# 7) replica histogram difference MUST be caught
b7 = trace("master-remote")
b7["context"]["final_snapshot"]["replica_status_histogram"] = {"FAILED": 1}
b7["context"]["final_snapshot"]["replica_count"] = 1
hard, _ = diff_traces(a, b7)
check("replica status difference is caught",
      any("replica_status_histogram" in h for h in hard), f"got: {hard}")

# 8) per-step db difference MUST be caught
b8 = trace("master-remote")
b8["steps"][0]["db"] = {"job": {"status": "READY"}}
hard, _ = diff_traces(a, b8)
check("per-step db difference is caught",
      any("db field" in h for h in hard), f"got: {hard}")

# 9) normalization must preserve emptiness while hiding volatile values
n1 = normalize({"job_id": "job-abc", "status": "READY", "error_message": ""})
n2 = normalize({"job_id": "job-xyz", "status": "READY", "error_message": ""})
check("volatile ids normalize equal", n1 == n2, f"{n1} vs {n2}")
check("status is preserved verbatim", n1["status"] == "READY", str(n1))

n3 = normalize({"ext4_size_bytes": 0})
n4 = normalize({"ext4_size_bytes": 12345})
check("zero vs non-zero survives normalization", n3 != n4, f"{n3} vs {n4}")

n5 = normalize({"error_message": "boom"})
n6 = normalize({"error_message": ""})
check("empty vs non-empty string survives normalization", n5 != n6, f"{n5} vs {n6}")

# 10) flatten must produce addressable paths
flat = flatten({"a": {"b": [1, 2]}})
check("flatten yields dotted paths",
      flat == {"a.b[0]": 1, "a.b[1]": 2}, str(flat))

print()
if FAILURES:
    print(f"{len(FAILURES)} self-test(s) FAILED: {FAILURES}")
    sys.exit(1)
print("all self-tests passed")
