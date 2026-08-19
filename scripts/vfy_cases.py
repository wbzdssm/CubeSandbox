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
from vfy_boundaries import HOSTILE_IDENTIFIERS, create_boundary_rows
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
        # Aliases claimed by accepted boundary rows. Tracked so the cleanup can
        # release them even when the delete-by-id already succeeded.
        self._probe_aliases: set[str] = set()
        # Template count before the run, for the post-cleanup leak check.
        self._count_before: int | None = None
        # Marker port for --force-build. Stable within a run so the dedup
        # re-submit still lands on the same fingerprint, unique across runs so
        # each run builds a fresh artifact. Kept in the ephemeral/dynamic range.
        digits = "".join(ch for ch in rec.tag if ch.isdigit()) or "0"
        self._marker_port = 40000 + (int(digits) % 20000)

    # =====================================================================
    # 1. Input boundary probes
    # =====================================================================
    def probes(self) -> None:
        section("1. input boundary probes (recorded, not judged)")
        r = self.r
        rows = create_boundary_rows(self.cfg.image, self.cfg.instance_type)
        submitted = skipped = accepted = 0

        for spec in rows:
            case, why, fields = spec["case"], spec["why"], spec["fields"]

            if spec["costly"] and not self.cfg.full_boundaries:
                # Recorded rather than dropped, so the trace still shows the row
                # exists and why it was not exercised.
                r.add(f"probe.create.{case}", why,
                      facts={"skipped_by_policy": True, "costly": True},
                      note="acceptance would trigger a distinct full build; "
                           "enable with --full-boundaries")
                skipped += 1
                continue

            call = self.link.create_probe(fields)
            rec = r.add(f"probe.create.{case}", why, call,
                        facts={"boundary": case, "costly": spec["costly"]})
            submitted += 1

            # An accepted row created a real template AND started a real build.
            # It is deleted immediately and deliberately NOT polled: waiting for
            # every accepted boundary row to finish would take hours.
            ident, name = _created_identity(rec, call)
            if ident:
                accepted += 1
                rec.facts["accepted"] = True
                rec.note = (rec.note + " " if rec.note else "") + \
                    "accepted: template deleted immediately, its build was not awaited"
                cleanup = self.link.delete_probe(ident)
                r.add(f"probe.create.{case}.cleanup",
                      f"delete the template this boundary row created ({ident})",
                      cleanup)
                if name:
                    self._probe_aliases.add(name)

        r.add("probe.create.summary", "boundary rows for create-from-image",
              facts={"rows": len(rows), "submitted": submitted,
                     "skipped_by_policy": skipped, "accepted": accepted,
                     "full_boundaries": self.cfg.full_boundaries})

        self._identifier_probes()

    def _identifier_probes(self) -> None:
        """Read and delete by identifiers that must never reach a path or a query.

        The artifact store is addressed by id and ids are interpolated into SQL
        in places, so a traversal or a quote that escaped would be a security
        bug, not a validation nicety. These cost nothing: none of them can match
        a real template.
        """
        r = self.r
        for case, ident in HOSTILE_IDENTIFIERS:
            r.add(f"probe.read_id.{case}", f"read a template by a {case} identifier",
                  self.link.get_probe(ident), facts={"identifier": repr(ident)})
        for case, ident in HOSTILE_IDENTIFIERS:
            r.add(f"probe.job_id.{case}", f"read a build job by a {case} identifier",
                  self.link.job_probe(ident), facts={"identifier": repr(ident)})
        # Delete is the dangerous one: a traversal here would delete files, and a
        # SQL tautology could match every row.
        for case, ident in HOSTILE_IDENTIFIERS:
            r.add(f"probe.delete_id.{case}", f"delete by a {case} identifier",
                  self.link.delete_probe(ident), facts={"identifier": repr(ident)})

    # =====================================================================
    # 2. World before
    # =====================================================================
    def list_before(self) -> None:
        section("2. list templates (before)")
        rec = self.r.add("read.list_before", "list templates before creating anything",
                         self.link.list_templates())
        # Only the COUNT is usable across runs. The list body is every template
        # that already existed in this environment, so diffing it element by
        # element reports a difference for every pre-existing template whose
        # position shifted — on a shared DB that is hundreds of false positives
        # and it buries the handful of records that are actually about this run.
        # verify_templatecenter.py therefore skips the body for list ops and
        # compares these derived facts instead.
        self._count_before = _list_count(rec)
        rec.facts["count"] = self._count_before
        rec.note = "body not compared: it is pre-existing environment state"

    # =====================================================================
    # 3. Create and follow the whole build
    # =====================================================================
    def _main_spec(self, name: str, request_id: str) -> CreateSpec:
        """The spec used for the main create and for the dedup re-submit.

        With --force-build the exposed ports carry a per-run marker port. The
        artifact fingerprint covers exposed_ports (fingerprint.go), so this makes
        the run land on an artifact_id nothing has built yet, forcing the real
        pull + envd + CA + mkfs path to execute instead of TC taking its
        "artifact already built by a sibling job, reusing" shortcut.

        Without it, a repeat run against an environment that already holds the
        artifact never exercises the build path at all — which is how a
        local-vs-remote comparison can come back clean while proving nothing
        about remote building.

        A port is used as the knob rather than writable_layer_size because it
        changes the fingerprint without changing the size of the ext4 that gets
        produced. Only one marker port is added, keeping the request inside the
        3-custom-port limit.
        """
        ports = list(self.cfg.exposed_ports)
        if self.cfg.force_build:
            ports = ports[:2] + [self._marker_port]
        return CreateSpec(
            image=self.cfg.image,
            instance_type=self.cfg.instance_type,
            name=name,
            exposed_ports=ports,
            request_id=request_id,
        )

    def create(self) -> None:
        section("3. create from image, following every state transition")
        r = self.r
        self.name = f"{r.tag}-main"
        spec = self._main_spec(self.name, f"{r.tag}-create")
        if self.cfg.force_build:
            r.add("create.force_build",
                  "a per-run marker port makes the artifact fingerprint unique, "
                  "so the real pull + mkfs path runs instead of artifact reuse",
                  facts={"marker_port": self._marker_port,
                         "exposed_ports": spec.exposed_ports})
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

        # BUILT only exists in remote build mode, but it is observed by polling
        # and the callback resumes synchronously, so a reused artifact reaches
        # BUILT and leaves it within a second. saw_built_state is therefore a
        # SAMPLED signal and its absence proves nothing; the durable evidence is
        # recorded separately below.
        saw_built = any(m.startswith(JOB_BUILT) for m in timeline)
        provenance = dict(r.build_provenance_result or {})
        if not provenance:
            provenance = r.build_provenance(self.job_id)
        r.add("create.terminal", f"terminal state {status}",
              facts={"terminal_status": status, "timeline": timeline,
                     "saw_built_state": saw_built,
                     "built_state_is_sampled": True,
                     "transitions": len(timeline)})
        rec = r.add("create.build_provenance",
                    "durable evidence for where the ext4 was built",
                    facts=provenance)
        if provenance.get("remote_build_evidence") != "confirmed" and not saw_built:
            rec.note = ("neither a BUILT observation nor durable evidence: this run "
                        "does NOT prove which side built the artifact. Confirm from "
                        "CubeTemplateCenter's log (build.go / reporter.go) instead")

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
            "remote_build_evidence": provenance.get("remote_build_evidence", "unknown"),
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

        # The download endpoint serves raw bytes off disk and is authorised by
        # nothing but this token, so its boundaries are security boundaries.
        for case, token in (
            ("wrong-token", "wrong-token"),
            ("empty-token", ""),
            ("token-prefix", self.download_token[:8] if self.download_token else "x"),
            ("token-with-wildcard", "%"),
            ("token-sql-tautology", "' OR '1'='1"),
        ):
            r.add(f"read.download_token.{case}",
                  f"HEAD the artifact download with a {case}",
                  self.link.head_download(self.artifact_id, token))

        # A traversal in the artifact id must not escape the artifact store.
        for case, artifact_id in (
            ("path-traversal", "../../../../etc/passwd"),
            ("absolute-path", "/etc/passwd"),
            ("absent", "rfs-0000000000000000000000000000"),
            ("empty", ""),
        ):
            r.add(f"read.download_artifact.{case}",
                  f"HEAD the artifact download with a {case} artifact id",
                  self.link.head_download(artifact_id, self.download_token))

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

        # Rebuilding something that is not there, and rebuilding the one that is
        # already rebuilding. The second is the interesting one: it must be
        # either rejected as a conflict or coalesced, never allowed to run two
        # builds over the same template.
        r.add("rebuild.absent", "rebuild a template id that does not exist",
              self.link.rebuild("tpl-0000000000000000000000000000"))
        r.add("rebuild.again_immediately",
              "rebuild the same template while the previous rebuild is in flight",
              self.link.rebuild(self.template_id))
        r.add("rebuild.concurrent_db",
              "DB fan-out after two rebuilds were requested back to back",
              db=r.db_snapshot(job_id=self.job_id, template_id=self.template_id))

    # =====================================================================
    # 5b. Alias uniqueness
    # =====================================================================
    def alias_conflict(self) -> None:
        """Claiming an alias that is already taken.

        The alias is the only user-chosen key in the system, so its uniqueness
        is what stops two templates from being addressable by the same name.
        This runs after the main create, when the alias is definitely claimed.
        """
        section("5c. alias uniqueness")
        r = self.r
        if not self.name:
            r.add("conflict.alias", "skipped: no alias was claimed", note="no name")
            return

        call = self.link.create_probe({
            "image": self.cfg.image,
            "instance_type": self.cfg.instance_type,
            "writable_layer_size": "1G",
            "name": self.name,
            "request_id": f"{r.tag}-alias-conflict",
        })
        rec = r.add("conflict.alias",
                    f"create a second template claiming the alias {self.name!r}", call)
        ident, _ = _created_identity(rec, call)
        if ident:
            # Accepted, so the alias is NOT unique. Recorded as a fact rather
            # than judged, and cleaned up so the run stays repeatable.
            rec.facts["alias_reuse_accepted"] = True
            r.add("conflict.alias.cleanup",
                  f"delete the duplicate-alias template ({ident})",
                  self.link.delete_probe(ident))
        else:
            rec.facts["alias_reuse_accepted"] = False

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
        # rather than reimplemented inside CubeTemplateCenter. The spec must be
        # byte-identical to the main create, marker port included, or this stops
        # testing dedup and starts testing a fresh build.
        dup_name = f"{r.tag}-dup"
        call = self.link.create(self._main_spec(dup_name, f"{r.tag}-dup"))
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
    def _delete_when_quiescent(self, target: str) -> Any:
        """Retry a delete until no build job blocks it any more.

        Bounded by build_timeout, because that is how long the blocking job may
        legitimately take to finish: a redo runs the same pipeline as a create.
        Every attempt is recorded, so a delete that never becomes possible shows
        up as a run problem instead of as silent residue.
        """
        r = self.r
        deadline = time.time() + self.cfg.build_timeout
        attempt = 1
        call = None
        while time.time() < deadline:
            time.sleep(min(self.cfg.poll_interval, 5))
            attempt += 1
            call = self.link.delete(target)
            if not _is_conflict(call):
                r.add("delete.retry_after_build",
                      f"delete accepted once the build job cleared (attempt {attempt})",
                      call, facts={"attempts": attempt})
                return call
        r.add("delete.retry_after_build",
              f"delete still blocked after {self.cfg.build_timeout}s", call,
              facts={"attempts": attempt, "still_blocked": True})
        r.problem(f"template {target} could not be deleted within "
                  f"{self.cfg.build_timeout}s: a build job kept it locked, so this "
                  "run leaves residue behind")
        return call

    def delete(self) -> None:
        section("7. delete and post-delete state")
        r = self.r
        if not r.created:
            r.add("delete.submit", "skipped: nothing was created", note="nothing created")
            return

        template_id, name = r.created[0]
        target = template_id or name

        # Deleting while a build job is still PENDING/RUNNING is refused by
        # design (delete.go: hasActiveJob -> ErrTemplateAttemptInProgress ->
        # Conflict). The rebuild step above just queued exactly such a job, so
        # this is both a real boundary worth recording AND the reason an earlier
        # revision of this harness leaked a template on every run: it read the
        # Conflict as "still settling" and moved on.
        first = self.link.delete(target)
        rec = r.add("delete.submit", "delete the template", first)
        blocked = _is_conflict(first)
        rec.facts["blocked_by_active_build"] = blocked

        if blocked:
            r.add("delete.blocked_by_active_build",
                  "delete is refused while a build job is active: expected, "
                  "and the reason the delete has to be retried",
                  facts={"boundary": "delete-during-active-build"})
            first = self._delete_when_quiescent(target)

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

        # Deleting the same thing twice. A second delete must be a clean
        # "already gone", not a 500 and not a success that implies it deleted
        # something. Retries and concurrent cleanups make this happen for real.
        rec = r.add("delete.again", "delete the same template a second time",
                    self.link.delete_probe(target))
        rec.facts["second_delete_looks_absent"] = _looks_absent(rec)
        if name:
            r.add("delete.by_name_after_delete",
                  "delete by the alias of an already-deleted template",
                  self.link.delete_probe(name))

        # The build job survives its template by design (build history), so
        # reading it after the delete must still work rather than 500.
        if self.job_id:
            r.add("delete.job_after", "read the build job after its template was deleted",
                  self.link.get_job(self.job_id, template_id))

        # The alias must be reusable once released, otherwise a name is
        # effectively burned by its first use.
        if name:
            call = self.link.create_probe({
                "image": self.cfg.image,
                "instance_type": self.cfg.instance_type,
                "writable_layer_size": "1G",
                "name": name,
                "request_id": f"{r.tag}-alias-reclaim",
            })
            rec = r.add("delete.alias_reclaimable",
                        f"claim the alias {name!r} again after the delete", call)
            ident, _ = _created_identity(rec, call)
            rec.facts["alias_reclaimed"] = bool(ident)
            if ident:
                r.add("delete.alias_reclaim_cleanup",
                      f"delete the template that reclaimed the alias ({ident})",
                      self.link.delete_probe(ident))

        r.created.pop(0)

    # =====================================================================
    # 8. World after
    # =====================================================================
    def list_after(self) -> None:
        section("8. list templates (after)")
        rec = self.r.add("read.list_after", "list templates after the lifecycle",
                         self.link.list_templates())
        count = _list_count(rec)
        rec.facts["count"] = count
        rec.note = "body not compared: it is pre-existing environment state"
        # Runs before cleanup, so a positive delta here is expected (the dedup
        # template is still alive). The real leak signal is measured after
        # cleanup, in cleanup() below.
        if self._count_before is not None and count is not None:
            rec.facts["delta_before_cleanup"] = count - self._count_before

    # ---- cleanup -------------------------------------------------------
    def cleanup(self) -> None:
        r = self.r
        if not r.created and not self._probe_aliases:
            self._final_leak_check()
            return
        section("cleanup")
        for template_id, name in list(r.created):
            target = template_id or name
            call = self.link.delete(target)
            rec = r.add("cleanup.delete", f"cleanup {target}", call)
            # Same conflict as in delete(): the dedup template may still have an
            # active job. Left unhandled, this is silent residue.
            if _is_conflict(call):
                rec.facts["blocked_by_active_build"] = True
                self._delete_when_quiescent(target)
        r.created.clear()
        # Aliases claimed by accepted boundary rows. Deleting by id normally
        # releases them, but a partially-applied create can leave the alias
        # behind, and a burned alias would break the next run.
        for alias in sorted(self._probe_aliases):
            r.add("cleanup.alias", f"release the boundary alias {alias!r}",
                  self.link.delete_probe(alias))
        self._probe_aliases.clear()
        self._final_leak_check()

    def _final_leak_check(self) -> None:
        """Did this run put the environment back the way it found it?

        Every template this run created is supposed to be gone by now. A
        non-zero delta means the run leaks, and a leak is not cosmetic: the
        residue accumulates in the list body of every future run, burns aliases,
        and pins artifacts that GC then refuses to collect. The count is the only
        cross-run-comparable part of the list, so it is the signal used here.
        """
        r = self.r
        rec = r.add("cleanup.leak_check",
                    "list templates after cleanup, to detect residue from this run",
                    self.link.list_templates())
        count = _list_count(rec)
        rec.facts["count"] = count
        rec.note = "body not compared: it is pre-existing environment state"
        if self._count_before is None or count is None:
            rec.facts["leak_measurable"] = False
            return

        leaked = count - self._count_before
        rec.facts.update({"leak_measurable": True, "net_template_delta": leaked,
                          "leaked": leaked != 0})
        r.trace.context["net_template_delta"] = leaked
        if leaked != 0:
            # Recorded as a problem rather than a note: a leaking run degrades
            # every subsequent run, so it should be visible without reading the
            # trace.
            r.problem(f"this run leaked {leaked} template(s): "
                      f"{self._count_before} before, {count} after cleanup")


def _list_count(rec: Any) -> int | None:
    """Number of templates in a list response, in either dialect.

    /cube/* wraps the array in `data`, CubeAPI returns it bare, and the SDK
    hands back a list of objects. Returns None when the shape is unrecognised,
    so an unparseable response is never mistaken for an empty environment.
    """
    facts = getattr(rec, "facts", None) or {}
    if isinstance(facts.get("count"), int):
        return facts["count"]
    resp = getattr(rec, "response", None) or {}
    if resp.get("error"):
        return None
    body = resp.get("json")
    if isinstance(body, list):
        return len(body)
    if isinstance(body, dict):
        for key in ("data", "templates", "items"):
            if isinstance(body.get(key), list):
                return len(body[key])
    return None


def _histogram(rows: list[dict[str, Any]]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status", "?"))
        hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items()))


def _created_identity(rec: Any, call: Any) -> tuple[str, str]:
    """Best-effort (identifier, alias) of whatever a create actually created.

    A boundary row that turned out to be ACCEPTED has made a real template and
    started a real build, so it must be cleaned up. The id may arrive as a
    template id or only as a job id depending on the link, and the response
    shape differs per dialect, so every plausible source is consulted rather
    than assuming one.
    """
    facts = getattr(rec, "facts", None) or {}
    template_id = str(facts.get("template_id") or "").strip()
    if template_id:
        return template_id, str(facts.get("name") or "").strip()

    resp = (getattr(call, "response", None) or {})
    if resp.get("error"):
        return "", ""
    body = resp.get("json")
    if isinstance(body, dict):
        job = body.get("job") if isinstance(body.get("job"), dict) else body
        for key in ("template_id", "templateID", "templateId"):
            value = str(job.get(key) or "").strip()
            if value:
                return value, ""
    return "", ""


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


def _is_conflict(call: Any) -> bool:
    """'Refused because something is still in flight', in both dialects.

    /cube/* returns ret 130409 for both ErrTemplateAttemptInProgress and
    ErrTemplateInUse; CubeAPI turns those into HTTP 409, which the SDK raises.
    """
    resp = getattr(call, "response", None)
    if not resp:
        return False
    if resp.get("ret_code") == 130409:
        return True
    if resp.get("http_status") == 409:
        return True
    text = str(resp.get("error") or "") + " " + str(resp.get("ret_msg") or "")
    return "Conflict" in text or "in progress" in text or "in use" in text


ORDER = [
    ("probes", Scenario.probes),
    ("list_before", Scenario.list_before),
    ("create", Scenario.create),
    ("read_created", Scenario.read_created),
    ("rebuild", Scenario.rebuild),
    ("alias_conflict", Scenario.alias_conflict),
    ("dedup", Scenario.dedup),
    ("delete", Scenario.delete),
    ("list_after", Scenario.list_after),
]
