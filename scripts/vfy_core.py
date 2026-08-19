# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Dual-link verification harness for the CubeTemplateCenter split.

WHY THIS EXISTS
---------------
The template flow can now run in three shapes, and they must be
externally indistinguishable:

  A) master + local   CubeMaster does everything in-process (pre-split)
  B) master + remote  CubeMaster validates/persists/distributes,
                      CubeTemplateCenter builds the ext4
  C) tc               Requests go straight to CubeTemplateCenter,
                      which owns the whole flow (next iteration preview)

A shell script cannot express what we actually need here: run the same
API sequence against different entry points, snapshot the database
after every step, then DIFF the shapes against each other field by
field. So this harness does three things a bash script does not:

  1. Records a full trace per link: every HTTP request/response plus a
     DB snapshot taken right after it.
  2. Normalizes volatile values (ids, timestamps, tokens, paths) so
     two runs can be compared structurally.
  3. Diffs traces and reports the FIRST divergence, which is almost
     always the actual bug.

ZERO DEPENDENCIES
-----------------
Standard library only. DB access goes through `docker exec <container>
mysql`, exactly like scripts/test-templatecenter.sh, so there is no
pymysql/requests install step on a test box.

USAGE
-----
  # verify one shape end to end
  ./scripts/verify_templatecenter.py run --shape master-local
  ./scripts/verify_templatecenter.py run --shape master-remote
  ./scripts/verify_templatecenter.py run --shape tc

  # verify a shape and diff it against a previously recorded baseline
  ./scripts/verify_templatecenter.py run --shape master-local --save-baseline
  ./scripts/verify_templatecenter.py run --shape master-remote --compare-baseline

  # diff two recorded traces
  ./scripts/verify_templatecenter.py diff a.json b.json

  # only exercise the read-only endpoints (safe against a live env)
  ./scripts/verify_templatecenter.py run --shape master-local --read-only

EXIT CODES
----------
  0 all checks passed (and diff clean, when comparing)
  1 a check failed
  2 environment/configuration problem (service down, switch not set)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

# --------------------------------------------------------------------------
# Protocol constants
#
# /cube/* endpoints ALWAYS answer HTTP 200 and carry the business outcome in
# ret.ret_code (see CubeMaster/pkg/service/httpservice/common/gin_response.go).
# Asserting on the HTTP status alone would therefore pass on every error.
# --------------------------------------------------------------------------
RET_SUCCESS = 200
RET_PARAMS_ERROR = 130400
RET_NOT_FOUND = 130404
RET_CONFLICT = 130409
RET_INTERNAL_ERROR = 130593
RET_DB_ERROR = 130594

RET_NAMES = {
    RET_SUCCESS: "Success",
    RET_PARAMS_ERROR: "MasterParamsError",
    RET_NOT_FOUND: "NotFound",
    RET_CONFLICT: "Conflict",
    RET_INTERNAL_ERROR: "MasterInternalError",
    RET_DB_ERROR: "DBError",
    -1: "Unknown",
}

# Job lifecycle (CubeMaster/pkg/templatecenter/job_constants.go). Uppercase in
# both the DB and the API.
JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_BUILT = "BUILT"
JOB_READY = "READY"
JOB_FAILED = "FAILED"
JOB_TERMINAL = {JOB_READY, JOB_FAILED}

# Real table names (CubeMaster/pkg/base/constants/constants.go).
TBL_IMAGE_JOB = "t_cube_template_image_job"
TBL_ARTIFACT = "t_cube_rootfs_artifact"
TBL_REPLICA = "t_cube_template_replica"
TBL_DEFINITION = "t_cube_template_definition"


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass
class Config:
    master_url: str = os.environ.get("MASTER_URL", "http://localhost:8089")
    tc_url: str = os.environ.get("TC_URL", "http://localhost:8090")
    mysql_container: str = os.environ.get("MYSQL_CONTAINER", "cube-mysql")
    db_user: str = os.environ.get("DB_USER", "cube")
    db_pass: str = os.environ.get("DB_PASS", "cube_pass")
    db_name: str = os.environ.get("DB_NAME", "cube_mvp")
    image: str = os.environ.get(
        "TEST_IMAGE",
        "cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest",
    )
    instance_type: str = os.environ.get("TEST_INSTANCE_TYPE", "cubebox")
    build_timeout: int = int(os.environ.get("BUILD_TIMEOUT", "600"))
    http_timeout: int = int(os.environ.get("HTTP_TIMEOUT", "30"))
    poll_interval: float = float(os.environ.get("POLL_INTERVAL", "3"))

    def api_url(self, shape: str) -> str:
        return self.tc_url if shape == "tc" else self.master_url


SHAPES = {
    "master-local": {
        "entry": "master",
        "desc": "CubeMaster in-process (pre-split baseline)",
        "requires": "CubeMaster conf: template_build_mode: local (or unset)",
    },
    "master-remote": {
        "entry": "master",
        "desc": "CubeMaster orchestrates, CubeTemplateCenter builds",
        "requires": "CubeMaster conf: template_build_mode: remote + template_center_endpoint",
    },
    "master-proxy": {
        "entry": "master",
        "desc": "CubeMaster reverse-proxies to CubeTemplateCenter",
        "requires": "CubeMaster conf: template_route_mode: proxy; TC: CUBE_TC_SERVE_TEMPLATE_API=true",
    },
    "tc": {
        "entry": "tc",
        "desc": "Direct to CubeTemplateCenter (next-iteration preview)",
        "requires": "TC env: CUBE_TC_SERVE_TEMPLATE_API=true",
    },
}


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------
class C:
    R = "\033[0;31m"
    G = "\033[0;32m"
    Y = "\033[1;33m"
    B = "\033[0;34m"
    DIM = "\033[2m"
    X = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for k in ("R", "G", "Y", "B", "DIM", "X"):
            setattr(cls, k, "")


if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C.strip()

_VERBOSE = False


def info(msg: str) -> None:
    print(f"{C.B}[INFO]{C.X} {msg}")


def ok(msg: str) -> None:
    print(f"{C.G}[PASS]{C.X} {msg}")


def fail(msg: str) -> None:
    print(f"{C.R}[FAIL]{C.X} {msg}")


def warn(msg: str) -> None:
    print(f"{C.Y}[WARN]{C.X} {msg}")


def vlog(msg: str) -> None:
    if _VERBOSE:
        print(f"{C.DIM}       {msg}{C.X}")


def section(msg: str) -> None:
    print(f"\n{C.B}{'=' * 68}{C.X}")
    print(f"{C.B}  {msg}{C.X}")
    print(f"{C.B}{'=' * 68}{C.X}")


def ret_name(code: Any) -> str:
    try:
        return RET_NAMES.get(int(code), f"code={code}")
    except (TypeError, ValueError):
        return f"code={code!r}"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
@dataclass
class HttpResult:
    method: str
    path: str
    http_status: int
    body: Any = None
    raw: str = ""
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0

    @property
    def ret_code(self) -> int | None:
        if isinstance(self.body, dict):
            ret = self.body.get("ret")
            if isinstance(ret, dict) and "ret_code" in ret:
                try:
                    return int(ret["ret_code"])
                except (TypeError, ValueError):
                    return None
        return None

    @property
    def ret_msg(self) -> str:
        if isinstance(self.body, dict):
            ret = self.body.get("ret")
            if isinstance(ret, dict):
                return str(ret.get("ret_msg", ""))
        return ""

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.ret_code == RET_SUCCESS

    def describe(self) -> str:
        if self.error:
            return f"transport error: {self.error}"
        rc = self.ret_code
        if rc is None:
            return f"HTTP {self.http_status} (no ret envelope)"
        return f"HTTP {self.http_status} ret={ret_name(rc)} msg={self.ret_msg!r}"


class Client:
    """Minimal HTTP client. Never raises on HTTP status: /cube/* encodes
    failures in the body, and error bodies are exactly what we want to see."""

    def __init__(self, base_url: str, timeout: int):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
        timeout: int | None = None,
    ) -> HttpResult:
        url = self.base + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Caller", "verify-templatecenter")

        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = resp.status
                headers = dict(resp.headers)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            status = e.code
            headers = dict(e.headers or {})
        except Exception as e:  # noqa: BLE001 - transport failure is data here
            return HttpResult(
                method, path, 0, error=f"{type(e).__name__}: {e}",
                elapsed_ms=int((time.time() - started) * 1000),
            )

        elapsed = int((time.time() - started) * 1000)
        parsed: Any = None
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None

        result = HttpResult(method, path, status, parsed, raw[:4096], None, headers, elapsed)
        vlog(f"{method} {path} -> {result.describe()} ({elapsed}ms)")
        return result


# --------------------------------------------------------------------------
# Database access
# --------------------------------------------------------------------------
class Database:
    """Read-only DB access through `docker exec`, so no Python driver is
    needed on the test host."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def query(self, sql: str) -> list[dict[str, str]]:
        cmd = [
            "docker", "exec", "-i", self.cfg.mysql_container,
            "mysql", f"-u{self.cfg.db_user}", f"-p{self.cfg.db_pass}",
            self.cfg.db_name, "--batch", "--raw", "-e", sql,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            warn(f"db query failed: {e}")
            return []
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            # mysql prints a password warning to stderr on every invocation.
            if stderr and "Using a password" not in stderr:
                warn(f"db query error: {stderr[:200]}")
            return []

        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if len(lines) < 2:
            return []
        header = lines[0].split("\t")
        rows = []
        for line in lines[1:]:
            values = line.split("\t")
            values += [""] * (len(header) - len(values))
            rows.append({h: v for h, v in zip(header, values)})
        return rows

    def one(self, sql: str) -> dict[str, str] | None:
        rows = self.query(sql)
        return rows[0] if rows else None

    def available(self) -> bool:
        return self.one("SELECT 1 AS ok") is not None


# --------------------------------------------------------------------------
# Value normalization
#
# Two links necessarily produce different ids, timestamps and tokens. To
# compare them structurally we replace volatile values with stable
# placeholders, keeping the SHAPE (present/absent, empty/non-empty) that
# actually matters.
# --------------------------------------------------------------------------
VOLATILE_KEY_PATTERNS = [
    re.compile(r"^(job_id|template_id|artifact_id|build_id|request_?id|requestID)$", re.I),
    re.compile(r"(_at|_time|_unix|timestamp)$", re.I),
    re.compile(r"^(created_at|updated_at|gc_deadline)$", re.I),
    re.compile(r"(token|fingerprint|sha256|digest)$", re.I),
    re.compile(r"^(node_id|node_ip|ins_id|ins_ip|master_node_ip)$", re.I),
    re.compile(r"^(ext4_path|ext4_size_bytes|display_name|alias)$", re.I),
    re.compile(r"^(progress|pull_.*|expected_node_count|ready_node_count|failed_node_count)$", re.I),
    re.compile(r"^(image_config_json|create_request|generated_request_json|result_json|request_json)$", re.I),
    re.compile(r"(version)$", re.I),
]


def is_volatile(key: str) -> bool:
    return any(p.search(key) for p in VOLATILE_KEY_PATTERNS)


def normalize(value: Any, key: str = "") -> Any:
    """Replace volatile leaves with placeholders that preserve emptiness."""
    if key and is_volatile(key):
        if value is None:
            return "<null>"
        if isinstance(value, str):
            return "<empty>" if value.strip() == "" else "<value>"
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return "<zero>" if value == 0 else "<number>"
        if isinstance(value, list):
            return f"<list:{len(value)}>"
        if isinstance(value, dict):
            return "<object>"
        return "<value>"

    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [normalize(v, key) for v in value]
    return value


# --------------------------------------------------------------------------
# Trace model
# --------------------------------------------------------------------------
@dataclass
class Step:
    """One verification step: what was called, what came back, what the DB
    looked like right after, and whether the expectation held."""
    name: str
    api: str = ""
    passed: bool = True
    skipped: bool = False
    detail: str = ""
    response: dict[str, Any] | None = None
    db: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Trace:
    shape: str
    entry_url: str
    started_at: str
    steps: list[Step] = field(default_factory=list)
    phase_track: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> list[Step]:
        return [s for s in self.steps if not s.passed and not s.skipped]

    @property
    def passed_count(self) -> int:
        return len([s for s in self.steps if s.passed and not s.skipped])

    @property
    def skipped_count(self) -> int:
        return len([s for s in self.steps if s.skipped])

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "entry_url": self.entry_url,
            "started_at": self.started_at,
            "phase_track": self.phase_track,
            "context": self.context,
            "steps": [asdict(s) for s in self.steps],
        }
