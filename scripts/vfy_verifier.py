# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Verifier: runs the whole template API surface against one entry point.

Split out of verify_templatecenter.py to keep each module readable; the CLI
lives in the main script.
"""

from __future__ import annotations

import time
from typing import Any

from vfy_core import (
    JOB_TERMINAL,
    RET_NOT_FOUND,
    RET_PARAMS_ERROR,
    RET_SUCCESS,
    SHAPES,
    TBL_ARTIFACT,
    TBL_DEFINITION,
    TBL_IMAGE_JOB,
    TBL_REPLICA,
    Client,
    Config,
    Database,
    HttpResult,
    Step,
    Trace,
    fail,
    info,
    normalize,
    ok,
    ret_name,
    section,
    vlog,
    warn,
)


class Verifier:
    """Runs the template API surface against one entry point and records a
    comparable trace."""

    def __init__(self, cfg: Config, shape: str, read_only: bool = False):
        self.cfg = cfg
        self.shape = shape
        self.read_only = read_only
        self.entry = cfg.api_url(SHAPES[shape]["entry"])
        self.client = Client(self.entry, cfg.http_timeout)
        # The artifact download endpoint is never proxied and always lives on
        # CubeMaster (design 9.7), so it needs its own client.
        self.master = Client(cfg.master_url, cfg.http_timeout)
        self.db = Database(cfg)
        self.trace = Trace(
            shape=shape,
            entry_url=self.entry,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.created: list[tuple[str, str]] = []
        self.tag = f"vfy{int(time.time()) % 100000}"

    # ---- step plumbing -------------------------------------------------
    def step(
        self,
        name: str,
        api: str = "",
        response: HttpResult | None = None,
        db: dict | None = None,
        passed: bool = True,
        detail: str = "",
        notes: list[str] | None = None,
    ) -> Step:
        s = Step(
            name=name,
            api=api,
            passed=passed,
            detail=detail,
            response=self._resp_snapshot(response) if response else None,
            db=db,
            notes=notes or [],
        )
        self.trace.steps.append(s)
        (ok if passed else fail)(f"{name}{' - ' + detail if detail else ''}")
        return s

    def skip(self, name: str, why: str) -> Step:
        s = Step(name=name, passed=True, skipped=True, detail=why)
        self.trace.steps.append(s)
        warn(f"{name} - SKIP: {why}")
        return s

    @staticmethod
    def _resp_snapshot(r: HttpResult) -> dict[str, Any]:
        """Keep both the raw outcome (for humans debugging a failure) and the
        normalized body (what the diff compares)."""
        return {
            "method": r.method,
            "path": r.path,
            "http_status": r.http_status,
            "ret_code": r.ret_code,
            "ret_name": ret_name(r.ret_code) if r.ret_code is not None else None,
            "has_error": r.error is not None,
            "error": r.error,
            "normalized_body": normalize(r.body) if isinstance(r.body, dict) else None,
            "body_keys": sorted(r.body.keys()) if isinstance(r.body, dict) else None,
        }

    def expect_ret(
        self, name: str, api: str, r: HttpResult, want: int, db: dict | None = None
    ) -> bool:
        if r.error:
            self.step(name, api, r, db, False, f"transport error: {r.error}")
            return False
        good = r.ret_code == want
        detail = "" if good else f"expected ret={ret_name(want)}, got {r.describe()}"
        self.step(name, api, r, db, good, detail)
        return good

    # ---- DB snapshots --------------------------------------------------
    def snapshot_job(self, job_id: str) -> dict[str, Any]:
        """Capture the job row plus everything it fans out to. This is the
        'state at each stage' view the diff relies on."""
        snap: dict[str, Any] = {}
        job = self.db.one(
            "SELECT job_id, template_id, status, phase, progress, artifact_id, "
            "artifact_status, error_message, expected_node_count, ready_node_count, "
            "failed_node_count, template_status, source_image_digest, "
            "template_spec_fingerprint, updated_at "
            f"FROM {TBL_IMAGE_JOB} WHERE job_id='{job_id}'"
        )
        snap["job"] = job or {}
        if not job:
            return snap

        artifact_id = (job.get("artifact_id") or "").strip()
        if artifact_id:
            snap["artifact"] = (
                self.db.one(
                    "SELECT artifact_id, status, ext4_size_bytes, ext4_sha256, "
                    "CASE WHEN download_token IS NULL OR download_token='' "
                    "THEN 0 ELSE 1 END AS has_token, "
                    "CASE WHEN image_config_json IS NULL OR image_config_json='' "
                    "THEN 0 ELSE 1 END AS has_image_config, "
                    "CASE WHEN generated_request_json IS NULL OR generated_request_json='' "
                    "THEN 0 ELSE 1 END AS has_generated_request, "
                    "cube_egress_ca_baked, gc_deadline "
                    f"FROM {TBL_ARTIFACT} WHERE artifact_id='{artifact_id}'"
                )
                or {}
            )

        template_id = (job.get("template_id") or "").strip()
        if template_id:
            snap["replicas"] = self.db.query(
                "SELECT node_id, status, phase, artifact_id, error_message "
                f"FROM {TBL_REPLICA} WHERE template_id='{template_id}' ORDER BY node_id"
            )
            snap["definition"] = (
                self.db.one(
                    "SELECT template_id, instance_type, status, "
                    "CASE WHEN request_json IS NULL OR request_json='' "
                    "THEN 0 ELSE 1 END AS has_request "
                    f"FROM {TBL_DEFINITION} WHERE template_id='{template_id}'"
                )
                or {}
            )
        return snap

    @staticmethod
    def normalize_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
        """Normalize a DB snapshot for cross-link comparison.

        Replica rows collapse into a status histogram on purpose: node identity
        and row order legitimately differ between runs, the status distribution
        must not.
        """
        out: dict[str, Any] = {}
        for key in ("job", "artifact", "definition"):
            if key in snap:
                out[key] = normalize(snap[key])
        if "replicas" in snap:
            rows = snap["replicas"] or []
            hist: dict[str, int] = {}
            for row in rows:
                status = row.get("status", "?")
                hist[status] = hist.get(status, 0) + 1
            out["replica_count"] = len(rows)
            out["replica_status_histogram"] = dict(sorted(hist.items()))
        return out

    # ---- polling -------------------------------------------------------
    def wait_terminal(self, job_id: str, timeout: int) -> tuple[str, list[str]]:
        """Poll until the job is terminal, recording the phase trajectory.

        The trajectory is the most valuable comparison artifact: it shows local
        and remote taking different internal routes to the same outcome (remote
        passes through BUILT, local does not).
        """
        deadline = time.time() + timeout
        track: list[str] = []
        last = ""
        status = ""
        while time.time() < deadline:
            r = self.client.call(
                "GET", "/cube/template/from-image", query={"job_id": job_id}
            )
            status, phase = "", ""
            if isinstance(r.body, dict):
                job = r.body.get("job") or {}
                status = str(job.get("status", "")).upper()
                phase = str(job.get("phase", "")).upper()
            marker = f"{status}/{phase}"
            if status and marker != last:
                track.append(marker)
                vlog(f"  job {job_id[:12]}: {marker}")
                last = marker
            if status in JOB_TERMINAL:
                return status, track
            time.sleep(self.cfg.poll_interval)
        track.append("TIMEOUT")
        return status or "TIMEOUT", track

    # ---- preflight -----------------------------------------------------
    def preflight(self) -> bool:
        section(f"Preflight - shape={self.shape} entry={self.entry}")
        info(SHAPES[self.shape]["desc"])
        info(f"requires: {SHAPES[self.shape]['requires']}")

        healthy = True
        for label, url in (
            ("CubeMaster", self.cfg.master_url),
            ("CubeTemplateCenter", self.cfg.tc_url),
        ):
            r = Client(url, 5).call("GET", "/health")
            up = r.http_status == 200
            # TC readiness includes the node view only when it serves the
            # public API, so /health reveals whether that switch took effect.
            checks = (r.body or {}).get("checks") if isinstance(r.body, dict) else None
            self.step(
                f"health: {label}",
                "GET /health",
                r,
                None,
                up,
                "" if up else f"unreachable ({r.describe()})",
                notes=[f"checks={checks}"] if checks else [],
            )
            healthy = healthy and up

        if not healthy:
            return False

        if not self.db.available():
            self.step(
                "database reachable", "", None, None, False,
                f"cannot query via docker exec {self.cfg.mysql_container}",
            )
            return False
        self.step("database reachable", "", None, None, True)

        # Probe that the entry point really serves the template control plane.
        # A 404 here is the most common misconfiguration: forgetting
        # CUBE_TC_SERVE_TEMPLATE_API on the TC side.
        probe = self.client.call(
            "GET", "/cube/template", query={"template_id": "__vfy_absent__"}
        )
        mounted = probe.http_status != 404
        detail = ""
        if not mounted:
            detail = "entry does not serve /cube/template (HTTP 404)"
            if self.shape in ("tc", "master-proxy"):
                detail += "; set CUBE_TC_SERVE_TEMPLATE_API=true on CubeTemplateCenter"
        self.step(
            "template routes mounted at entry",
            "GET /cube/template",
            probe,
            None,
            mounted,
            detail,
        )
        return mounted
