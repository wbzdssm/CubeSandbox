# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Test cases: the full template API surface, plus stage-by-stage state capture.

Each case is a method on Cases and is registered in ORDER at the bottom.
Cases are ordered so later ones can reuse artifacts created by earlier ones.
"""

from __future__ import annotations

import time
from typing import Any

from vfy_core import (
    JOB_BUILT,
    JOB_FAILED,
    JOB_READY,
    RET_NOT_FOUND,
    RET_PARAMS_ERROR,
    RET_SUCCESS,
    TBL_ARTIFACT,
    Config,
    info,
    section,
    vlog,
)
from vfy_verifier import Verifier


class Cases:
    """Holds the case implementations. `v` is the Verifier doing the work."""

    def __init__(self, v: Verifier):
        self.v = v
        self.cfg: Config = v.cfg
        # Carried between cases.
        self.job_id: str = ""
        self.template_id: str = ""
        self.alias: str = ""
        self.artifact_id: str = ""
        self.download_token: str = ""

    # =====================================================================
    # Group 1: negative / validation paths
    #
    # These run first because they need no state and they catch the most
    # common regression: an error that used to be a clean ret_code turning
    # into a 500 or a transport failure after the split.
    # =====================================================================
    def case_validation(self) -> None:
        section("Group 1: parameter validation (no side effects)")
        v = self.v

        r = v.client.call("GET", "/cube/template", query={"template_id": "tpl-does-not-exist-xyz"})
        v.expect_ret("GET template: unknown id -> NotFound", "GET /cube/template", r, RET_NOT_FOUND)

        r = v.client.call("GET", "/cube/rootfs-artifact")
        v.expect_ret(
            "GET rootfs-artifact: missing artifact_id -> ParamsError",
            "GET /cube/rootfs-artifact", r, RET_PARAMS_ERROR,
        )

        r = v.client.call("GET", "/cube/rootfs-artifact", query={"artifact_id": "rfs-absent-xyz"})
        v.expect_ret(
            "GET rootfs-artifact: unknown id -> NotFound",
            "GET /cube/rootfs-artifact", r, RET_NOT_FOUND,
        )

        r = v.client.call("GET", "/cube/template/from-image", query={"job_id": "job-absent-xyz"})
        v.expect_ret(
            "GET job: unknown job_id -> NotFound",
            "GET /cube/template/from-image", r, RET_NOT_FOUND,
        )

        if v.read_only:
            v.skip("POST from-image: empty image -> ParamsError", "read-only mode")
            v.skip("DELETE template: missing template_id -> ParamsError", "read-only mode")
            return

        # Empty source_image_ref must be rejected by validation, not by the
        # builder. In remote mode this proves CubeMaster still validates before
        # forwarding anything to CubeTemplateCenter.
        r = v.client.call(
            "POST", "/cube/template/from-image",
            body={"requestID": f"{v.tag}-neg-1", "source_image_ref": "",
                  "instance_type": self.cfg.instance_type},
        )
        v.expect_ret(
            "POST from-image: empty image -> ParamsError",
            "POST /cube/template/from-image", r, RET_PARAMS_ERROR,
        )

        r = v.client.call(
            "DELETE", "/cube/template",
            body={"RequestID": f"{v.tag}-neg-2", "template_id": ""},
        )
        v.expect_ret(
            "DELETE template: missing template_id -> ParamsError",
            "DELETE /cube/template", r, RET_PARAMS_ERROR,
        )

        # Alias grammar is ^[a-z0-9][a-z0-9-]{0,63}$ and must not start with
        # tpl-/snap-; both rules are worth pinning because the alias is what
        # survives rebuilds.
        for bad_alias, why in (("Bad_Alias", "uppercase+underscore"), ("tpl-reserved", "reserved prefix")):
            r = v.client.call(
                "POST", "/cube/template/from-image",
                body={"requestID": f"{v.tag}-neg-alias", "source_image_ref": self.cfg.image,
                      "instance_type": self.cfg.instance_type, "alias": bad_alias},
            )
            v.expect_ret(
                f"POST from-image: invalid alias ({why}) -> ParamsError",
                "POST /cube/template/from-image", r, RET_PARAMS_ERROR,
            )

    # =====================================================================
    # Group 2: list endpoints (read-only, must work before anything exists)
    # =====================================================================
    def case_list(self) -> None:
        section("Group 2: list endpoints")
        v = self.v

        r = v.client.call("GET", "/cube/template")
        passed = v.expect_ret("GET template: list all", "GET /cube/template", r, RET_SUCCESS)
        if passed and isinstance(r.body, dict):
            data = r.body.get("data")
            v.step(
                "list response carries data array", "GET /cube/template", None, None,
                isinstance(data, list),
                f"data type={type(data).__name__}",
                notes=[f"count={len(data) if isinstance(data, list) else 'n/a'}"],
            )

        r = v.client.call("GET", "/cube/template/from-image")
        v.expect_ret("GET from-image: list jobs", "GET /cube/template/from-image", r, RET_SUCCESS)

    # =====================================================================
    # Group 3: the create -> build -> ready happy path
    #
    # This is the core comparison: identical request, identical terminal
    # state, identical DB fan-out -- but different internal trajectories.
    # =====================================================================
    def case_create(self) -> None:
        section("Group 3: create from image (full build)")
        v = self.v
        if v.read_only:
            v.skip("POST from-image: create", "read-only mode")
            return

        self.alias = f"{v.tag}-main"
        body = {
            "requestID": f"{v.tag}-create",
            "source_image_ref": self.cfg.image,
            "instance_type": self.cfg.instance_type,
            "alias": self.alias,
            "writable_layer_size": "1G",
            "exposed_ports": [80],
        }
        r = v.client.call("POST", "/cube/template/from-image", body=body)
        if not v.expect_ret("POST from-image: accepted", "POST /cube/template/from-image", r, RET_SUCCESS):
            return

        job = (r.body or {}).get("job") or {}
        self.job_id = str(job.get("job_id", "")).strip()
        self.template_id = str(job.get("template_id", "")).strip()
        if not self.job_id:
            v.step("response carries job_id", "POST /cube/template/from-image", r, None, False,
                   "job.job_id missing from response")
            return
        self.created.append((self.template_id, self.alias)) if False else None
        v.created.append((self.template_id, self.alias))
        v.trace.context.update({
            "alias": self.alias,
            "has_template_id": bool(self.template_id),
        })
        v.step("response carries job_id + template_id", "POST /cube/template/from-image",
               None, None, bool(self.job_id and self.template_id),
               notes=[f"job_id={self.job_id}", f"template_id={self.template_id}"])

        # Immediately after acceptance the row must exist and be non-terminal:
        # this is what proves the API is async rather than blocking.
        early = v.snapshot_job(self.job_id)
        early_status = (early.get("job") or {}).get("status", "")
        v.step(
            "job row created (async accept)", "", None,
            v.normalize_snapshot(early),
            early_status not in ("", JOB_READY),
            f"status right after accept: {early_status or 'ROW MISSING'}",
        )

        info(f"waiting for build to finish (timeout {self.cfg.build_timeout}s)...")
        status, track = v.wait_terminal(self.job_id, self.cfg.build_timeout)
        v.trace.phase_track = track
        v.step(
            "job reaches READY", "GET /cube/template/from-image", None, None,
            status == JOB_READY,
            f"terminal status={status}",
            notes=[f"trajectory={' -> '.join(track)}"],
        )
        # BUILT is the remote-mode-only intermediate state. Recording whether
        # it appeared is how the diff proves remote really went through
        # CubeTemplateCenter rather than silently falling back to local.
        saw_built = any(m.startswith(JOB_BUILT) for m in track)
        v.trace.context["saw_built_state"] = saw_built
        vlog(f"passed through BUILT: {saw_built}")

        final = v.snapshot_job(self.job_id)
        v.trace.context["final_snapshot"] = v.normalize_snapshot(final)
        jobrow = final.get("job") or {}
        self.artifact_id = (jobrow.get("artifact_id") or "").strip()

        v.step("DB: job status READY", "", None, v.normalize_snapshot(final),
               jobrow.get("status") == JOB_READY,
               f"db status={jobrow.get('status')} phase={jobrow.get('phase')} "
               f"err={(jobrow.get('error_message') or '')[:120]}")

        art = final.get("artifact") or {}
        v.step("DB: artifact row registered and READY", "", None, None,
               bool(art) and art.get("status") == "READY",
               f"artifact={self.artifact_id or 'MISSING'} status={art.get('status')}")
        # An artifact with no size or no token is the exact failure mode that
        # made distribution fail with an opaque cubelet-side error before.
        v.step("DB: artifact has size + download token + image config", "", None, None,
               bool(art) and art.get("ext4_size_bytes", "0") not in ("", "0")
               and art.get("has_token") == "1" and art.get("has_image_config") == "1",
               f"size={art.get('ext4_size_bytes')} token={art.get('has_token')} "
               f"image_config={art.get('has_image_config')}")

        replicas = final.get("replicas") or []
        ready = [x for x in replicas if x.get("status") == "READY"]
        v.step("DB: at least one replica READY", "", None, None,
               len(ready) > 0,
               f"replicas={len(replicas)} ready={len(ready)}")

        definition = final.get("definition") or {}
        v.step("DB: template definition written", "", None, None,
               bool(definition) and definition.get("has_request") == "1",
               f"definition={'present' if definition else 'MISSING'}")

    # =====================================================================
    # Group 4: read paths against the created template
    # =====================================================================
    def case_read_created(self) -> None:
        section("Group 4: read endpoints for the created template")
        v = self.v
        if not self.template_id:
            for n in ("GET template by id", "GET template by alias", "GET template compat",
                      "GET rootfs-artifact", "HEAD artifact download", "GET ca file"):
                v.skip(n, "no template created")
            return

        r = v.client.call("GET", "/cube/template", query={"template_id": self.template_id})
        v.expect_ret("GET template by id", "GET /cube/template", r, RET_SUCCESS)

        # Alias resolution is a distinct code path (ResolveTemplateIdentifier)
        # and the reason aliases exist, so it gets its own assertion.
        r = v.client.call("GET", "/cube/template", query={"template_id": self.alias})
        alias_ok = v.expect_ret("GET template by alias", "GET /cube/template", r, RET_SUCCESS)
        if alias_ok and isinstance(r.body, dict):
            resolved = str(r.body.get("template_id", ""))
            v.step("alias resolves to the same template_id", "GET /cube/template", None, None,
                   resolved == self.template_id,
                   f"alias->{resolved} expected {self.template_id}")

        r = v.client.call("GET", "/cube/template",
                          query={"template_id": self.template_id, "include_request": "true"})
        inc_ok = v.expect_ret("GET template with include_request", "GET /cube/template", r, RET_SUCCESS)
        if inc_ok and isinstance(r.body, dict):
            v.step("include_request returns create_request", "GET /cube/template", None, None,
                   bool(r.body.get("create_request")),
                   "create_request present" if r.body.get("create_request") else "create_request MISSING")

        r = v.client.call("GET", "/cube/template/compat", query={"template_id": self.template_id})
        v.expect_ret("GET template compat", "GET /cube/template/compat", r, RET_SUCCESS)

        if self.artifact_id:
            r = v.client.call("GET", "/cube/rootfs-artifact", query={"artifact_id": self.artifact_id})
            art_ok = v.expect_ret("GET rootfs-artifact by id", "GET /cube/rootfs-artifact", r, RET_SUCCESS)
            if art_ok and isinstance(r.body, dict):
                artifact = r.body.get("artifact") or r.body
                self.download_token = str(artifact.get("download_token", "") or "")
                v.step("artifact info exposes ext4 metadata", "GET /cube/rootfs-artifact", None, None,
                       bool(artifact.get("ext4_sha256")) and bool(artifact.get("ext4_size_bytes")),
                       f"sha256={'set' if artifact.get('ext4_sha256') else 'MISSING'} "
                       f"size={artifact.get('ext4_size_bytes')}")
        else:
            v.skip("GET rootfs-artifact by id", "no artifact_id captured")

        self._check_download()
        self._check_ca()

    def _check_download(self) -> None:
        """The download endpoint is the data plane Cubelet uses. It always lives
        on CubeMaster (never proxied to TC, design 9.7), so it is probed against
        CubeMaster regardless of the shape under test."""
        v = self.v
        if not self.artifact_id:
            v.skip("HEAD artifact download", "no artifact_id captured")
            return
        if not self.download_token:
            token_row = v.db.one(
                f"SELECT download_token FROM {TBL_ARTIFACT} WHERE artifact_id='{self.artifact_id}'"
            )
            self.download_token = (token_row or {}).get("download_token", "")

        r = v.master.call("HEAD", "/cube/template/artifact/download",
                          query={"artifact_id": self.artifact_id, "token": self.download_token})
        # This endpoint serves bytes, so it uses real HTTP status codes rather
        # than the ret envelope.
        v.step("HEAD artifact download (on CubeMaster)", "HEAD /cube/template/artifact/download",
               r, None, r.http_status == 200,
               f"HTTP {r.http_status}",
               notes=[f"content-length={r.headers.get('Content-Length', 'n/a')}"])

        bad = v.master.call("HEAD", "/cube/template/artifact/download",
                            query={"artifact_id": self.artifact_id, "token": "wrong-token"})
        v.step("artifact download rejects a wrong token",
               "HEAD /cube/template/artifact/download", bad, None,
               bad.http_status not in (200, 0),
               f"HTTP {bad.http_status} (expected non-200)")

    def _check_ca(self) -> None:
        v = self.v
        r = v.master.call("GET", "/cube/ca/cube-egress-ca.crt")
        # A missing CA file is a valid deployment state, so only a 5xx is a
        # failure here.
        v.step("GET ca file (on CubeMaster)", "GET /cube/ca/:filename", r, None,
               r.http_status < 500,
               f"HTTP {r.http_status}")

    # =====================================================================
    # Group 5: idempotency and dedup
    # =====================================================================
    def case_dedup(self) -> None:
        section("Group 5: dedup / idempotency")
        v = self.v
        if v.read_only or not self.job_id:
            v.skip("re-create same spec reuses artifact", "read-only or no base template")
            return

        # Same spec => same fingerprint => same artifact_id. This must hold
        # ACROSS shapes too, which is why the fingerprint helpers are shared
        # rather than reimplemented in CubeTemplateCenter.
        r = v.client.call("POST", "/cube/template/from-image",
                          body={"requestID": f"{v.tag}-dup",
                                "source_image_ref": self.cfg.image,
                                "instance_type": self.cfg.instance_type,
                                "alias": f"{v.tag}-dup",
                                "writable_layer_size": "1G",
                                "exposed_ports": [80]})
        if not v.expect_ret("POST from-image: same spec accepted",
                            "POST /cube/template/from-image", r, RET_SUCCESS):
            return
        job2 = (r.body or {}).get("job") or {}
        job2_id = str(job2.get("job_id", "")).strip()
        tpl2 = str(job2.get("template_id", "")).strip()
        if tpl2:
            v.created.append((tpl2, f"{v.tag}-dup"))

        status, track = v.wait_terminal(job2_id, self.cfg.build_timeout)
        v.step("second build reaches READY", "GET /cube/template/from-image", None, None,
               status == JOB_READY, f"terminal status={status}",
               notes=[f"trajectory={' -> '.join(track)}"])

        snap2 = v.snapshot_job(job2_id)
        artifact2 = ((snap2.get("job") or {}).get("artifact_id") or "").strip()
        reused = bool(artifact2) and artifact2 == self.artifact_id
        v.trace.context["artifact_reused_on_same_spec"] = reused
        v.step("same spec reuses the same artifact_id", "", None, v.normalize_snapshot(snap2),
               reused,
               f"first={self.artifact_id} second={artifact2}"
               + ("" if reused else "  (rebuild instead of reuse)"))

    # =====================================================================
    # Group 6: redo / compat writes
    # =====================================================================
    def case_redo(self) -> None:
        section("Group 6: redo + compat write")
        v = self.v
        if v.read_only or not self.template_id:
            v.skip("POST template redo", "read-only or no template")
            v.skip("POST template compat", "read-only or no template")
            return

        r = v.client.call("POST", "/cube/template/redo",
                          body={"requestID": f"{v.tag}-redo",
                                "template_id": self.template_id,
                                "failed_only": True})
        # failed_only=True with everything already READY is a legitimate no-op;
        # accept Success or a clear NotFound, but never a 5xx or a hang.
        acceptable = r.ret_code in (RET_SUCCESS, RET_NOT_FOUND) and r.error is None
        v.step("POST template redo (failed_only)", "POST /cube/template/redo", r, None,
               acceptable, r.describe())

        r = v.client.call("POST", "/cube/template/compat",
                          body={"requestID": f"{v.tag}-compat",
                                "template_id": self.template_id})
        acceptable = r.error is None and r.ret_code in (RET_SUCCESS, RET_PARAMS_ERROR, RET_NOT_FOUND)
        v.step("POST template compat", "POST /cube/template/compat", r, None,
               acceptable, r.describe())

        r = v.client.call("GET", "/cube/template/build/nonexistent-build-id/status")
        v.step("GET build status (unknown id)", "GET /cube/template/build/:id/status", r, None,
               r.error is None and r.http_status < 500,
               f"HTTP {r.http_status} ret={r.ret_code}")

    # =====================================================================
    # Group 7: delete + post-delete state
    # =====================================================================
    def case_delete(self) -> None:
        section("Group 7: delete and post-delete state")
        v = self.v
        if v.read_only or not v.created:
            v.skip("DELETE template", "read-only or nothing created")
            return

        first_tpl, first_alias = v.created[0]
        target = first_tpl or first_alias
        r = v.client.call("DELETE", "/cube/template",
                          body={"RequestID": f"{v.tag}-del", "template_id": target})
        if not v.expect_ret("DELETE template", "DELETE /cube/template", r, RET_SUCCESS):
            return

        # After a delete the template must no longer resolve. Poll briefly:
        # deletion touches several tables and is not guaranteed synchronous.
        gone = False
        for _ in range(10):
            chk = v.client.call("GET", "/cube/template", query={"template_id": target})
            if chk.ret_code == RET_NOT_FOUND:
                gone = True
                break
            time.sleep(1)
        v.step("deleted template no longer resolves", "GET /cube/template", None, None,
               gone, "still resolvable after delete" if not gone else "")

        # Alias must be released as well, otherwise recreating with the same
        # alias would conflict forever.
        if first_alias:
            chk = v.client.call("GET", "/cube/template", query={"template_id": first_alias})
            v.step("alias released after delete", "GET /cube/template", None, None,
                   chk.ret_code == RET_NOT_FOUND, chk.describe())

        v.created.pop(0)

    # ---- cleanup -------------------------------------------------------
    def cleanup(self) -> None:
        v = self.v
        if v.read_only or not v.created:
            return
        section("Cleanup")
        for tpl, alias in list(v.created):
            target = tpl or alias
            r = v.client.call("DELETE", "/cube/template",
                              body={"RequestID": f"{v.tag}-cleanup", "template_id": target})
            (info if r.ret_code == RET_SUCCESS else vlog)(
                f"cleanup {target}: {r.describe()}")
        v.created.clear()


ORDER = [
    ("validation", Cases.case_validation),
    ("list", Cases.case_list),
    ("create", Cases.case_create),
    ("read_created", Cases.case_read_created),
    ("dedup", Cases.case_dedup),
    ("redo", Cases.case_redo),
    ("delete", Cases.case_delete),
]
