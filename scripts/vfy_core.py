# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Core model for the CubeTemplateCenter dual-link verification harness.

DESIGN: RECORD FIRST, JUDGE LATER
---------------------------------
This harness deliberately does NOT assert. An assertion encodes what the
author expected at writing time, which is exactly the wrong instrument when
the question is "did splitting the template flow out of CubeMaster change
anything?". A hidden expectation that happens to hold in both links proves
nothing, and one that is slightly wrong produces noise.

So the flow is strictly three phases:

  1. RECORD   Walk the whole template lifecycle (create, poll every state
              transition, read, rebuild, delete, post-delete) and store the
              RAW data of each step: the request as sent, the response body
              verbatim, and `SELECT *` of every DB row it touched.
  2. OUTPUT   Dump those raw records (stdout + a JSON file). This is readable
              on its own and is the artifact a human inspects.
  3. COMPARE  Diff two recorded runs field by field. The verdict comes only
              from this phase.

Consequence: a single run can never "fail" a check, it can only fail to make
progress. Correctness is a property of the COMPARISON between links.

ZERO DEPENDENCIES for the HTTP links (standard library only; DB access via
`docker exec <container> mysql`, same as scripts/test-templatecenter.sh).
The SDK link additionally needs the repo's `sdk/python` package.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Protocol constants
#
# /cube/* endpoints ALWAYS answer HTTP 200 and carry the business outcome in
# ret.ret_code (CubeMaster/pkg/service/httpservice/common/gin_response.go), so
# the ret envelope - not the HTTP status - is the interesting datum.
# CubeAPI (which the SDK talks to) is the opposite: real HTTP status codes and
# no envelope. Both shapes are recorded verbatim.
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
}

# Job lifecycle (CubeMaster/pkg/templatecenter/job_constants.go). Uppercase in
# both the DB and the API. BUILT only ever appears in remote build mode.
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
TBL_PLACEMENT = "t_cube_artifact_node_placement"

ALL_TABLES = (TBL_IMAGE_JOB, TBL_ARTIFACT, TBL_REPLICA, TBL_DEFINITION, TBL_PLACEMENT)


# --------------------------------------------------------------------------
# Configuration (env-only for anything secret)
# --------------------------------------------------------------------------
@dataclass
class Config:
    master_url: str = os.environ.get("MASTER_URL", "http://localhost:8089")
    tc_url: str = os.environ.get("TC_URL", "http://localhost:8090")
    cubeapi_url: str = os.environ.get("CUBE_API_URL", "http://127.0.0.1:3000")
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
    delete_settle: int = int(os.environ.get("DELETE_SETTLE", "15"))
    # Boundary rows whose acceptance would start a distinct full build (every
    # exposed_ports / writable_layer_size / instance_type variant is its own
    # template spec fingerprint) are skipped unless this is on. Submitting all
    # of them takes hours and one of them asks for 1000Ti.
    full_boundaries: bool = os.environ.get("FULL_BOUNDARIES", "").lower() in ("1", "true", "yes")

    def entry_url(self, entry: str) -> str:
        return {
            "master": self.master_url,
            "tc": self.tc_url,
            "cubeapi": self.cubeapi_url,
        }[entry]


# Every link is the same lifecycle reached through a different route. They must
# all produce the same observable data; where they legitimately differ is
# enumerated in EXPECTED_FACT_DIVERGENCE (see verify_templatecenter.py).
LINKS: dict[str, dict[str, str]] = {
    "master-local": {
        "via": "http",
        "entry": "master",
        "desc": "HTTP /cube/* on CubeMaster, build in-process (pre-split baseline)",
        "requires": "CubeMaster conf: template_build_mode: local (or unset)",
    },
    "master-remote": {
        "via": "http",
        "entry": "master",
        "desc": "HTTP /cube/* on CubeMaster, ext4 built by CubeTemplateCenter",
        "requires": "CubeMaster conf: template_build_mode: remote + template_center_endpoint",
    },
    "master-proxy": {
        "via": "http",
        "entry": "master",
        "desc": "HTTP /cube/* on CubeMaster, reverse-proxied to CubeTemplateCenter",
        "requires": "CubeMaster conf: template_route_mode: proxy; "
                    "TC env: CUBE_TC_SERVE_TEMPLATE_API=true",
    },
    "tc": {
        "via": "http",
        "entry": "tc",
        "desc": "HTTP /cube/* straight to CubeTemplateCenter (next-iteration preview)",
        "requires": "TC env: CUBE_TC_SERVE_TEMPLATE_API=true",
    },
    "sdk-local": {
        "via": "sdk",
        "entry": "cubeapi",
        "desc": "Python SDK -> CubeAPI /templates -> CubeMaster, build in-process",
        "requires": "sdk/python importable; CubeAPI up; template_build_mode: local",
    },
    "sdk-remote": {
        "via": "sdk",
        "entry": "cubeapi",
        "desc": "Python SDK -> CubeAPI /templates -> CubeMaster -> CubeTemplateCenter",
        "requires": "sdk/python importable; CubeAPI up; template_build_mode: remote",
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
    CY = "\033[0;36m"
    DIM = "\033[2m"
    X = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for k in ("R", "G", "Y", "B", "CY", "DIM", "X"):
            setattr(cls, k, "")


if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C.strip()

_VERBOSE = False


def info(msg: str) -> None:
    print(f"{C.B}[INFO]{C.X} {msg}")


def ok(msg: str) -> None:
    print(f"{C.G}[ OK ]{C.X} {msg}")


def fail(msg: str) -> None:
    print(f"{C.R}[DIFF]{C.X} {msg}")


def warn(msg: str) -> None:
    print(f"{C.Y}[WARN]{C.X} {msg}")


def vlog(msg: str) -> None:
    if _VERBOSE:
        print(f"{C.DIM}       {msg}{C.X}")


def section(msg: str) -> None:
    print(f"\n{C.B}{'=' * 72}{C.X}")
    print(f"{C.B}  {msg}{C.X}")
    print(f"{C.B}{'=' * 72}{C.X}")


def ret_name(code: Any) -> str:
    if code is None:
        return "none"
    try:
        return RET_NAMES.get(int(code), f"code={code}")
    except (TypeError, ValueError):
        return f"code={code!r}"


# --------------------------------------------------------------------------
# Raw record model
#
# A Record is an observation, not a judgement: there is no pass/fail field.
# `op` is the stable identity used to align records across links, so it must
# not embed ids or link names.
# --------------------------------------------------------------------------
@dataclass
class Record:
    seq: int
    op: str
    label: str
    via: str = ""
    at_ms: int = 0
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    db: dict[str, Any] | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass
class Trace:
    link: str
    via: str
    entry_url: str
    started_at: str
    records: list[Record] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "link": self.link,
            "via": self.via,
            "entry_url": self.entry_url,
            "started_at": self.started_at,
            "context": self.context,
            "problems": self.problems,
            "records": [asdict(r) for r in self.records],
        }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
@dataclass
class HttpResult:
    method: str
    url: str
    path: str
    request_body: Any = None
    http_status: int = 0
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

    def describe(self) -> str:
        if self.error:
            return f"transport error: {self.error}"
        rc = self.ret_code
        if rc is None:
            return f"HTTP {self.http_status}"
        return f"HTTP {self.http_status} ret={ret_name(rc)} msg={self.ret_msg!r}"

    def snapshot(self) -> dict[str, Any]:
        """The RAW response, kept whole. `json` is the parsed body and `raw` the
        exact bytes as text; both are stored because a body that fails to parse
        is itself a finding."""
        return {
            "http_status": self.http_status,
            "elapsed_ms": self.elapsed_ms,
            "ret_code": self.ret_code,
            "ret_name": ret_name(self.ret_code) if self.ret_code is not None else None,
            "ret_msg": self.ret_msg,
            "error": self.error,
            "json": self.body,
            "raw": self.raw,
            "content_type": self.headers.get("Content-Type", ""),
            "content_length": self.headers.get("Content-Length", ""),
        }

    def request_snapshot(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "path": self.path,
            "body": self.request_body,
        }


class Client:
    """Minimal HTTP client. Never raises on HTTP status: /cube/* encodes
    failures in the body, and an error body is data we want to keep."""

    def __init__(self, base_url: str, timeout: int, headers: dict[str, str] | None = None):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.extra_headers = headers or {}

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
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
        for k, v in self.extra_headers.items():
            req.add_header(k, v)

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
        except Exception as e:  # noqa: BLE001 - a transport failure is data here
            return HttpResult(
                method, url, path, body, 0,
                error=f"{type(e).__name__}: {e}",
                elapsed_ms=int((time.time() - started) * 1000),
            )

        elapsed = int((time.time() - started) * 1000)
        parsed: Any = None
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None

        result = HttpResult(method, url, path, body, status, parsed, raw, None, headers, elapsed)
        vlog(f"{method} {path} -> {result.describe()} ({elapsed}ms)")
        return result


# --------------------------------------------------------------------------
# Database access
# --------------------------------------------------------------------------
# Values are interpolated into a SQL string because `docker exec mysql -e`
# offers no parameter binding. Everything interpolated is therefore validated
# against a strict allow-list first: ids in this system are opaque tokens, so a
# value that does not look like one is refused rather than escaped.
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:@/=+-]{1,190}$")
_SAFE_COLUMN = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class UnsafeQueryValue(ValueError):
    pass


def safe_value(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_VALUE.match(value):
        raise UnsafeQueryValue(f"refusing to interpolate {value!r} into SQL")
    return value


class Database:
    """Read-only DB access through `docker exec`, so no Python driver has to be
    installed on a test box."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.enabled = True

    def query(self, sql: str) -> list[dict[str, str]]:
        if not self.enabled:
            return []
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

        lines = [ln for ln in proc.stdout.split("\n") if ln.strip()]
        if len(lines) < 2:
            return []
        header = lines[0].split("\t")
        rows: list[dict[str, str]] = []
        for line in lines[1:]:
            values = line.split("\t")
            values += [""] * (len(header) - len(values))
            rows.append(dict(zip(header, values)))
        return rows

    def rows_where(self, table: str, column: str, value: str,
                   order_by: str | None = None) -> list[dict[str, str]]:
        """`SELECT *` so the raw dump contains every column, including ones this
        tool does not know about yet - a column that stops being written after
        the split is precisely the kind of regression worth catching."""
        if table not in ALL_TABLES or not _SAFE_COLUMN.match(column):
            raise UnsafeQueryValue(f"unknown table/column: {table}.{column}")
        try:
            val = safe_value(value)
        except UnsafeQueryValue as e:
            warn(str(e))
            return []
        sql = f"SELECT * FROM {table} WHERE {column}='{val}'"
        if order_by:
            if not _SAFE_COLUMN.match(order_by):
                raise UnsafeQueryValue(f"unsafe order_by: {order_by}")
            sql += f" ORDER BY {order_by}"
        return self.query(sql)

    def one_where(self, table: str, column: str, value: str) -> dict[str, str]:
        rows = self.rows_where(table, column, value)
        return rows[0] if rows else {}

    def available(self) -> bool:
        return bool(self.query("SELECT 1 AS ok"))


# --------------------------------------------------------------------------
# Normalization (used by the COMPARE phase only - never when recording)
#
# Two links necessarily produce different ids, timestamps and tokens. To
# compare them structurally, volatile leaves are replaced by placeholders that
# preserve the SHAPE that matters: null vs empty vs set, zero vs non-zero,
# list length.
# --------------------------------------------------------------------------
VOLATILE_KEY_PATTERNS = [
    # Ids, matched as a whole key OR as a suffix, so derived names like
    # resolved_template_id / parent_job_id are covered too. Missing the suffix
    # form made every read report a difference purely because the two runs
    # created different templates.
    re.compile(r"(^|_)(id|job_?id|template_?id|artifact_?id|build_?id|"
               r"request_?id|snapshot_?id)$", re.I),
    re.compile(r"(_at|_time|_unix|timestamp|deadline)$", re.I),
    re.compile(r"(token|fingerprint|sha256|digest|checksum)$", re.I),
    re.compile(r"^(node_id|node_ip|host_ip|host_id|ins_id|ins_ip|master_node_ip|local_ip)$", re.I),
    re.compile(r"^(ext4_path|ext4_size_bytes|artifact_path|store_path)$", re.I),
    re.compile(r"^(display_name|alias|aliases|name|version)$", re.I),
    re.compile(r"^(progress|pull_.*|.*_node_count|elapsed_ms|duration.*)$", re.I),
    # Human-readable diagnostics. These embed ids and aliases verbatim, so
    # comparing the text is meaningless — but the SHAPE still matters, and the
    # normalizer preserves it: <empty> vs <value> keeps "did this fail" while
    # discarding "which template it was about". The semantic outcome is compared
    # separately from ret_code / HTTP status.
    re.compile(r"(^|_)(msg|message|error|last_error|reason|detail|details)$", re.I),
    re.compile(r"^(image_config_json|create_request|createRequest|generated_request_json|"
               r"result_json|request_json|payload_json|logs)$", re.I),
]


def is_volatile(key: str) -> bool:
    return any(p.search(key) for p in VOLATILE_KEY_PATTERNS)


def normalize(value: Any, key: str = "") -> Any:
    if key and is_volatile(key):
        if value is None:
            return "<null>"
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return "<empty>" if value.strip() == "" else "<value>"
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


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten to dotted paths so a diff can name the exact field."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj:
            out[prefix or "<root>"] = "<empty-object>"
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        if not obj:
            out[prefix or "<root>"] = "<empty-list>"
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix or "<root>"] = obj
    return out


# --------------------------------------------------------------------------
# Raw rendering
# --------------------------------------------------------------------------
def clip(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"... <clipped {len(text) - limit} chars>"


def render_record(rec: Record, limit: int = 1200) -> str:
    """Human-readable dump of ONE raw record. Deliberately shows the response
    body verbatim: reading it is how a human verifies the flow."""
    lines: list[str] = []
    lines.append(f"{C.CY}#{rec.seq:03d} [{rec.at_ms / 1000:7.1f}s] {rec.op}{C.X}  {rec.label}")
    if rec.request:
        q = rec.request.get("url") or rec.request.get("path")
        lines.append(f"      {C.DIM}-> {rec.request.get('method', '')} {q}{C.X}")
        body = rec.request.get("body")
        if body is not None:
            lines.append(f"      {C.DIM}   body: {clip(json.dumps(body, sort_keys=True), limit)}{C.X}")
    if rec.response:
        r = rec.response
        head = f"HTTP {r.get('http_status')}"
        if r.get("ret_code") is not None:
            head += f" ret={r.get('ret_name')}"
        if r.get("error"):
            head += f" ERROR {r.get('error')}"
        lines.append(f"      <- {head} ({r.get('elapsed_ms')}ms)")
        payload = r.get("json")
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None \
            else (r.get("raw") or "")
        if text:
            lines.append(f"         {clip(text, limit)}")
    if rec.db:
        for table, rows in rec.db.items():
            if isinstance(rows, list):
                lines.append(f"      db {table}: {len(rows)} row(s)")
                for row in rows:
                    lines.append(f"         {clip(json.dumps(row, ensure_ascii=False, sort_keys=True), limit)}")
            else:
                lines.append(f"      db {table}: {clip(json.dumps(rows, ensure_ascii=False, sort_keys=True), limit)}")
    if rec.facts:
        lines.append(f"      facts: {clip(json.dumps(rec.facts, ensure_ascii=False, sort_keys=True), limit)}")
    if rec.note:
        lines.append(f"      {C.Y}note: {rec.note}{C.X}")
    return "\n".join(lines)
