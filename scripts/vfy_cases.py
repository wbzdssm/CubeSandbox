# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""The lifecycle walk: what gets recorded, in what order.

Every step is an observation. Nothing here decides whether a value is correct;
the `op` names are stable so that two runs through different links line up and
`verify_templatecenter.py compare` can do that job.

Order matters and is not arbitrary:

  1. probe.*    Negative/edge inputs first: they need no state, and an error
                that used to be a clean ret_code turning into a 500 after the
                split is the cheapest regression to catch.
  2. read.list  The world before we touch it.
  3. create.*   Submit, then record EVERY state transition with the DB row as
                it was at that instant, then the settled fan-out.
  4. read.*     Every read path against the thing we just created, including
                the artifact data plane.
  5. rebuild.*  A write that is not a create.
  6. dedup.*    Same spec again: must land on the same artifact_id.
  7. delete.*   Delete, then the post-delete state of the API AND of every
                table - "delete left rows behind" is invisible from the API.
"""

from __future__ import annotations

import time
from typing import Any

from vfy_core import (
    JOB_BUILT,
    TBL_ARTIFACT,
    TBL_DEFINITION,
    TBL_IMAGE_JOB,
    TBL_PLACEMENT,
    TBL_REPLICA,
    Config,
    UnsafeQueryValue,
    info,
    section,
)
from vfy_links import CreateSpec
from vfy_verifier import Recorder


class Scenario:
    """Holds the ids discovered while walking, so later steps can reuse them."""

    def __init__(self, rec: Recorder):
        self.r = rec
        self.cfg: Config = rec.cfg
        self.link = rec.link
        self.job_id = ""
        self.template_id = ""
        self.name = ""
        self.artifact_id = ""
        self.download_token = ""

    # =====================================================================
    # 1. Negative / edge probes
    # =====================================================================
    def probes(self) -> None:
        section("1. edge-input probes (recorded, not judged)")
        r = self.r
        r.add("probe.unknown_template", "get a template id that does not exist",
              self.link.probe_unknown_template())
        r.add("probe.unknown_job", "get a build/job id that does not exist",
              self.link.probe_unknown_job())
        r.add("probe.empty_image", "create with an empty image reference",
              self.link.probe_empty_image())

        # Name/alias grammar is ^[a-z0-9][a-z0-9-]{0,63}$ and must not start
        # with tpl-/snap-. Both rules are worth recording because the name is
        # what survives a rebuild.
        for bad, why in (("Bad_Name", "uppercase+underscore"),
                         ("tpl-reserved", "reserved prefix")):
            r.add("probe.bad_name", f"create with an invalid name ({why})",
                  self.link.probe_bad_name(bad))

    # =====================================================================
    # 2. World before
    # =====================================================================
    def list_before(self) -> None:
        section("2. list templates (before)")
        self.r.add("read.list_before", "list templates before creating anything",
                   self.link.list_templates())

    # =====================================================================
    # 3. Create and follow the whole build
    # =====================================================================
    def create(self) -> None:
        section("3. create from image, following every state transition")
        r = self.r
        self.name = f"{r.tag}-main"
        spec = CreateSpec(
            image=self.cfg.image,
            instance_type=self.cfg.instance_type,
            name=self.name,
            request_id=f"{r.tag}-create",
        )
        call = self.link.create(spec)
        rec = r.add("create.submit", "submit create-from-image", call)

        self.job_id = str(rec.facts.get("job_id", "") or "")
        self.template_id = str(rec.facts.get("template_id", "") or "")
        if not self.job_id and not self.template_id:
            r.problem("create was not accepted (no job_id/template_id in the response); "
                      "the rest of the lifecycle cannot be recorded")
            return
        r.created.append((self.template_id, self.name))

        # Right after acceptance: proves the API is async rather than blocking,
        # and shows which columns are populated at insert time.
        r.add("create.accepted_db", "DB fan-out immediately after acceptance",
              db=r.db_snapshot(job_id=self.job_id, template_id=self.template_id),
              facts={"job_id": self.job_id, "template_id": self.template_id})

        info(f"following the build (timeout {self.cfg.build_timeout}s)...")
        status, timeline = r.poll_until_terminal(
            self.job_id, self.template_id, self.cfg.build_timeout)

        # BUILT only exists in remote build mode. Recording whether it appeared
        # is how a comparison proves remote really went through
        # CubeTemplateCenter instead of silently falling back to local.
        saw_built = any(m.startswith(JOB_BUILT) for m in timeline)
        r.add("create.terminal", f"terminal state {status}",
              facts={"terminal_status": status, "timeline": timeline,
                     "saw_built_state": saw_built, "transitions": len(timeline)})

        final = r.db_snapshot(job_id=self.job_id, template_id=self.template_id)
        job_rows = final.get(TBL_IMAGE_JOB) or []
        if job_rows:
            self.artifact_id = (job_rows[0].get("artifact_id") or "").strip()
        r.add("create.final_db", "settled DB fan-out after the build",
              db=final,
              facts={
                  "artifact_id": self.artifact_id,
                  "artifact_rows": len(final.get(TBL_ARTIFACT) or []),
                  "replica_rows": len(final.get(TBL_REPLICA) or []),
                  "definition_rows": len(final.get(TBL_DEFINITION) or []),
                  "placement_rows": len(final.get(TBL_PLACEMENT) or []),
                  "replica_status": _histogram(final.get(TBL_REPLICA) or []),
              })

        r.trace.context.update({
            "name": self.name,
            "job_id": self.job_id,
            "template_id": self.template_id,
            "artifact_id": self.artifact_id,
            "terminal_status": status,
            "timeline": timeline,
            "saw_built_state": saw_built,
        })

    # =====================================================================
    # 4. Read paths against the created template
    # =====================================================================
    def read_created(self) -> None:
        section("4. read paths for the created template")
        r = self.r
        if not self.template_id:
            r.add("read.by_id", "skipped: nothing was created", note="no template_id")
            return

        r.add("read.by_id", "get the template by id",
              self.link.get_template(self.template_id))

        # Name/alias resolution is a distinct code path
        # (ResolveTemplateIdentifier) and the whole reason names exist.
        call = self.link.get_template(self.name)
        rec = r.add("read.by_name", "get the same template by name/alias", call)
        rec.facts["name_resolves_to_same_id"] = (
            str(rec.facts.get("resolved_template_id", "")) == self.template_id)

        r.add("read.with_request", "get the template including its create request",
              self.link.get_template(self.template_id, include_request=True))

        if self.artifact_id:
            call = self.link.get_artifact(self.artifact_id)
            rec = r.add("read.artifact", "get the rootfs artifact metadata", call)
            self.download_token = str(rec.facts.get("download_token", "") or "")
        else:
            r.add("read.artifact", "skipped: no artifact_id captured",
                  note="no artifact_id")

        self._read_download()

    def _read_download(self) -> None:
        """The download endpoint is the Cubelet data plane. It always lives on
        CubeMaster - never proxied to TC (design 9.7) - so it is probed there
        regardless of which link is under test."""
        r = self.r
        if not self.artifact_id:
            r.add("read.download_head", "skipped: no artifact_id", note="no artifact_id")
            return
        if not self.download_token and r.db.enabled:
            try:
                row = r.db.one_where(TBL_ARTIFACT, "artifact_id", self.artifact_id)
            except UnsafeQueryValue:
                row = {}
            self.download_token = (row.get("download_token") or "")

        r.add("read.download_head", "HEAD the artifact download (on CubeMaster)",
              self.link.head_download(self.artifact_id, self.download_token))
        r.add("read.download_bad_token", "HEAD the artifact download with a wrong token",
              self.link.head_download(self.artifact_id, "wrong-token"))

    # =====================================================================
    # 5. Rebuild / redo
    # =====================================================================
    def rebuild(self) -> None:
        section("5. rebuild / redo")
        r = self.r
        if not self.template_id:
            r.add("rebuild.submit", "skipped: nothing was created", note="no template_id")
            return
        r.add("rebuild.submit", "request a rebuild of the existing template",
              self.link.rebuild(self.template_id))
        r.add("rebuild.db", "DB fan-out right after the rebuild request",
              db=r.db_snapshot(job_id=self.job_id, template_id=self.template_id))

    # =====================================================================
    # 6. Same spec again: dedup must land on the same artifact
    # =====================================================================
    def dedup(self) -> None:
        section("6. same spec again (artifact reuse)")
        r = self.r
        if not self.job_id:
            r.add("dedup.submit", "skipped: no base template", note="no job_id")
            return

        # Same spec => same fingerprint => same artifact_id. This must hold
        # ACROSS links too, which is why the fingerprint helpers are shared code
        # rather than reimplemented inside CubeTemplateCenter.
        dup_name = f"{r.tag}-dup"
        call = self.link.create(CreateSpec(
            image=self.cfg.image,
            instance_type=self.cfg.instance_type,
            name=dup_name,
            request_id=f"{r.tag}-dup",
        ))
        rec = r.add("dedup.submit", "submit the identical spec a second time", call)
        job2 = str(rec.facts.get("job_id", "") or "")
        tpl2 = str(rec.facts.get("template_id", "") or "")
        if tpl2:
            r.created.append((tpl2, dup_name))
        if not job2:
            return

        status2, timeline2 = r.poll_until_terminal(job2, tpl2, self.cfg.build_timeout)
        snap2 = r.db_snapshot(job_id=job2, template_id=tpl2)
        rows2 = snap2.get(TBL_IMAGE_JOB) or []
        artifact2 = (rows2[0].get("artifact_id") or "").strip() if rows2 else ""
        reused = bool(artifact2) and artifact2 == self.artifact_id
        r.add("dedup.terminal", f"second build terminal state {status2}",
              db=snap2,
              facts={"terminal_status": status2, "timeline": timeline2,
                     "artifact_id": artifact2,
                     "artifact_reused": reused})
        r.trace.context["artifact_reused_on_same_spec"] = reused

    # =====================================================================
    # 7. Delete and the state it leaves behind
    # =====================================================================
    def delete(self) -> None:
        section("7. delete and post-delete state")
        r = self.r
        if not r.created:
            r.add("delete.submit", "skipped: nothing was created", note="nothing created")
            return

        template_id, name = r.created[0]
        target = template_id or name
        r.add("delete.submit", "delete the template", self.link.delete(target))

        # Deletion touches several tables and is not guaranteed synchronous, so
        # the API is re-read until it stops resolving (bounded), and the last
        # read is recorded whatever it says.
        deadline = time.time() + self.cfg.delete_settle
        call = self.link.get_template(target)
        attempts = 1
        while time.time() < deadline and not _looks_absent(call):
            time.sleep(1)
            call = self.link.get_template(target)
            attempts += 1
        rec = r.add("delete.resolve_after", "read the template after deleting it", call)
        rec.facts.update({"attempts": attempts, "looks_absent": _looks_absent(call)})

        # The name must be released as well, otherwise recreating with the same
        # name would conflict forever.
        if name:
            call = self.link.get_template(name)
            rec = r.add("delete.name_after", "read the template by name after deleting it", call)
            rec.facts["looks_absent"] = _looks_absent(call)

        # Rows left behind are invisible from the API: this is the only place
        # they show up.
        after = r.db_snapshot(job_id=self.job_id if template_id == self.template_id else "",
                              template_id=template_id,
                              artifact_id=self.artifact_id)
        r.add("delete.final_db", "DB fan-out after the delete settled",
              db=after,
              facts={
                  "job_rows": len(after.get(TBL_IMAGE_JOB) or []),
                  "definition_rows": len(after.get(TBL_DEFINITION) or []),
                  "replica_rows": len(after.get(TBL_REPLICA) or []),
                  "artifact_rows": len(after.get(TBL_ARTIFACT) or []),
                  "placement_rows": len(after.get(TBL_PLACEMENT) or []),
                  "replica_status": _histogram(after.get(TBL_REPLICA) or []),
              })
        r.created.pop(0)

    # =====================================================================
    # 8. World after
    # =====================================================================
    def list_after(self) -> None:
        section("8. list templates (after)")
        self.r.add("read.list_after", "list templates after the lifecycle",
                   self.link.list_templates())

    # ---- cleanup -------------------------------------------------------
    def cleanup(self) -> None:
        r = self.r
        if not r.created:
            return
        section("cleanup")
        for template_id, name in list(r.created):
            target = template_id or name
            r.add("cleanup.delete", f"cleanup {target}", self.link.delete(target))
        r.created.clear()


def _histogram(rows: list[dict[str, Any]]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status", "?"))
        hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items()))


def _looks_absent(call: Any) -> bool:
    """'Not found' in both dialects: a NotFound ret envelope on /cube/*, or an
    HTTP 404 / TemplateNotFoundError through CubeAPI + SDK."""
    resp = getattr(call, "response", None)
    if not resp:
        return False
    if resp.get("ret_code") == 130404:
        return True
    if resp.get("http_status") == 404:
        return True
    return "NotFound" in str(resp.get("error") or "")


ORDER = [
    ("probes", Scenario.probes),
    ("list_before", Scenario.list_before),
    ("create", Scenario.create),
    ("read_created", Scenario.read_created),
    ("rebuild", Scenario.rebuild),
    ("dedup", Scenario.dedup),
    ("delete", Scenario.delete),
    ("list_after", Scenario.list_after),
]
