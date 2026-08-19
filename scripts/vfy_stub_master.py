#!/usr/bin/env python3
# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""A stub CubeMaster that answers /cube/* well enough to exercise the harness.

The verification harness has a lot of moving parts that only run against a live
server: the boundary tables, the accepted-row cleanup, the hostile-identifier
probes, the polling loop. Reaching them requires an environment nobody has on a
laptop, so this stands in for one — it validates exactly the rules the real
server documents and nothing else.

It is a TEST FIXTURE for the harness, not a mock of CubeMaster: it exists to
prove the harness drives every code path and produces comparable records, not to
predict what CubeMaster answers. Real verdicts only ever come from real runs.

    python3 scripts/vfy_stub_master.py 8089 &
    MASTER_URL=http://127.0.0.1:8089 python3 scripts/verify_templatecenter.py \
        run --link master-local --out /tmp/stub.json
"""

from __future__ import annotations

import json
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RET_SUCCESS = 200
RET_PARAMS_ERROR = 130400
RET_NOT_FOUND = 130404
RET_CONFLICT = 130409

ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
RESERVED_PREFIXES = ("tpl-", "snap-")
RESERVED_PORT = 49983

_LOCK = threading.Lock()
TEMPLATES: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
ALIASES: dict[str, str] = {}


def validate_alias(alias: str) -> str | None:
    alias = (alias or "").strip()
    if alias == "":
        return None
    if alias.startswith(RESERVED_PREFIXES):
        return f"alias {alias!r} must not start with 'tpl-' or 'snap-'"
    if not ALIAS_RE.match(alias):
        return f"alias {alias!r} is invalid"
    return None


def validate_image(ref: object) -> str | None:
    if not isinstance(ref, str):
        return "source_image_ref must be a string"
    raw = ref.strip()
    if raw == "":
        return "source_image_ref is required"
    stripped = raw[len("docker://"):] if raw.startswith("docker://") else raw
    if stripped.startswith("docker://"):
        return f"invalid image reference: {ref}"
    if stripped.startswith("-"):
        return f"invalid image reference: {ref}"
    if any(ch in stripped for ch in " \t\n\x00;$`"):
        return f"invalid image reference: {ref}"
    if "@" in stripped:
        digest = stripped.split("@", 1)[1]
        if not digest.startswith("sha256:") or len(digest) != len("sha256:") + 64:
            return f"invalid image reference: {ref}"
        if ":" in stripped.split("@", 1)[0].rsplit("/", 1)[-1]:
            return f"invalid image reference: {ref}"
    return None


def validate_ports(ports: object) -> str | None:
    if ports is None:
        return None
    if not isinstance(ports, list):
        return "exposed_ports must be a list"
    custom = 0
    seen = set()
    for port in ports:
        if isinstance(port, bool) or not isinstance(port, int):
            return f"invalid exposed port {port!r}"
        if port <= 0 or port > 65535:
            return f"invalid exposed port {port}"
        if port in seen:
            continue
        seen.add(port)
        if port != RESERVED_PORT:
            custom += 1
    if custom > 3:
        return "at most 3 custom exposed ports are supported"
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):  # silence
        pass

    # ---- plumbing ----------------------------------------------------
    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _ret(self, code: int, msg: str = "", **extra) -> None:
        # /cube/* always answers HTTP 200 and carries the outcome in the ret
        # envelope, which is the behaviour the harness has to cope with.
        self._send({"ret": {"ret_code": code, "ret_msg": msg}, **extra})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _query(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    # ---- routes ------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send({"status": "ok", "checks": {"db": "ok"}})
        elif path == "/cube/template":
            self._get_template()
        elif path == "/cube/template/from-image":
            self._get_job()
        elif path == "/cube/rootfs-artifact":
            self._get_artifact()
        elif path == "/cube/template/artifact/download":
            self._download()
        else:
            self._send({"error": "not found"}, 404)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/cube/template/from-image":
            self._create()
        elif path == "/cube/template/redo":
            self._redo()
        else:
            self._send({"error": "not found"}, 404)

    def do_DELETE(self):  # noqa: N802
        if urlparse(self.path).path == "/cube/template":
            self._delete()
        else:
            self._send({"error": "not found"}, 404)

    # ---- handlers ----------------------------------------------------
    def _resolve(self, ident: str) -> str | None:
        if ident in TEMPLATES:
            return ident
        return ALIASES.get(ident)

    def _get_template(self):
        ident = self._query().get("template_id", "")
        if ident == "":
            with _LOCK:
                return self._ret(RET_SUCCESS, data=list(TEMPLATES.values()))
        with _LOCK:
            resolved = self._resolve(ident)
            if resolved is None:
                return self._ret(RET_NOT_FOUND, f"template {ident!r} not found")
            tpl = dict(TEMPLATES[resolved])
        if self._query().get("include_request") != "true":
            tpl.pop("create_request", None)
        self._ret(RET_SUCCESS, **tpl)

    def _get_job(self):
        job_id = self._query().get("job_id", "")
        if job_id == "":
            return self._ret(RET_PARAMS_ERROR, "job_id is required")
        with _LOCK:
            job = JOBS.get(job_id)
        if job is None:
            return self._ret(RET_NOT_FOUND, f"job {job_id!r} not found")
        self._ret(RET_SUCCESS, job=job)

    def _create(self):
        body = self._body()
        if "requestID" not in body or not str(body.get("requestID") or "").strip():
            return self._ret(RET_PARAMS_ERROR, "requestID is required")
        err = validate_image(body.get("source_image_ref"))
        if err:
            return self._ret(RET_PARAMS_ERROR, err)
        if not str(body.get("writable_layer_size") or "").strip():
            return self._ret(RET_PARAMS_ERROR, "writable_layer_size is required")
        alias = str(body.get("alias") or "").strip()
        err = validate_alias(alias)
        if err:
            return self._ret(RET_PARAMS_ERROR, err)
        err = validate_ports(body.get("exposed_ports"))
        if err:
            return self._ret(RET_PARAMS_ERROR, err)

        with _LOCK:
            if alias and alias in ALIASES:
                return self._ret(RET_CONFLICT, f"alias {alias!r} is already in use")
            template_id = "tpl-" + uuid.uuid4().hex[:24]
            job_id = str(uuid.uuid4())
            artifact_id = "rfs-" + uuid.uuid4().hex[:24]
            # Terminal immediately: the harness's polling loop is exercised by
            # the transition it records, not by how long the build takes.
            JOBS[job_id] = {
                "job_id": job_id, "template_id": template_id,
                "status": "READY", "phase": "READY", "progress": 100,
                "artifact_id": artifact_id, "error_message": "",
            }
            TEMPLATES[template_id] = {
                "template_id": template_id, "alias": alias, "status": "READY",
                "artifact_id": artifact_id,
                "create_request": {"source_image_ref": body.get("source_image_ref")},
            }
            if alias:
                ALIASES[alias] = template_id
        self._ret(RET_SUCCESS, job=JOBS[job_id])

    def _get_artifact(self):
        artifact_id = self._query().get("artifact_id", "")
        with _LOCK:
            known = any(t.get("artifact_id") == artifact_id for t in TEMPLATES.values())
        if not artifact_id or not known:
            return self._ret(RET_NOT_FOUND, f"artifact {artifact_id!r} not found")
        self._ret(RET_SUCCESS, artifact={
            "artifact_id": artifact_id, "status": "READY",
            "download_token": "tok-" + artifact_id[-8:],
            "ext4_sha256": "0" * 64, "ext4_size_bytes": 1073741824,
        })

    def _download(self):
        q = self._query()
        artifact_id, token = q.get("artifact_id", ""), q.get("token", "")
        with _LOCK:
            known = any(t.get("artifact_id") == artifact_id for t in TEMPLATES.values())
        # Serves bytes, so it uses real status codes rather than a ret envelope.
        if not artifact_id or not known:
            return self._send({"error": "artifact not found"}, 404)
        if token != "tok-" + artifact_id[-8:]:
            return self._send({"error": "bad token"}, 403)
        self._send({"ok": True})

    def _redo(self):
        template_id = str(self._body().get("template_id") or "").strip()
        with _LOCK:
            resolved = self._resolve(template_id)
        if resolved is None:
            return self._ret(RET_NOT_FOUND, f"template {template_id!r} not found")
        self._ret(RET_SUCCESS)

    def _delete(self):
        ident = str(self._body().get("template_id") or "").strip()
        with _LOCK:
            resolved = self._resolve(ident)
            if resolved is None:
                return self._ret(RET_NOT_FOUND, f"template {ident!r} not found")
            tpl = TEMPLATES.pop(resolved)
            ALIASES.pop(tpl.get("alias") or "", None)
        self._ret(RET_SUCCESS)


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8089
    # Bound to loopback only: this answers unauthenticated mutating requests and
    # must never be reachable off the host.
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"stub CubeMaster on http://127.0.0.1:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
