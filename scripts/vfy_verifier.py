# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Recorder: walks the lifecycle through one link and stores the RAW data.

The recorder is intentionally dumb. It does not know what a correct response
looks like; it knows how to capture one. For every operation it stores:

  * the request exactly as the link sent it
  * the response verbatim (parsed JSON and the raw text, plus the error when
    the call blew up)
  * `SELECT *` of every DB row the operation could have touched

Only two things can make a run stop early: the entry point not answering at
all, or the build never reaching a terminal state. Those are recorded in
`trace.problems` because they make the run unusable as comparison input - they
are not "test failures".
"""

from __future__ import annotations

import json
import time
from typing import Any

from vfy_core import (
    JOB_TERMINAL,
    LINKS,
    TBL_ARTIFACT,
    TBL_DEFINITION,
    TBL_IMAGE_JOB,
    TBL_PLACEMENT,
    TBL_REPLICA,
    C,
    Config,
    Database,
    Record,
    Trace,
    UnsafeQueryValue,
    info,
    render_record,
    section,
    warn,
)
from vfy_links import Link, LinkCall, build_link


class Recorder:
    def __init__(self, cfg: Config, link_name: str, print_raw: bool = True,
                 print_limit: int = 1200):
        self.cfg = cfg
        self.link_name = link_name
        self.link: Link = build_link(cfg, link_name)
        self.db = Database(cfg)
        self.print_raw = print_raw
        self.print_limit = print_limit
        self.started = time.time()
        self.seq = 0
        self.trace = Trace(
            link=link_name,
            via=self.link.via,
            entry_url=self.link.entry,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        # Per-run tag so parallel runs and reruns never collide on an alias.
        self.tag = f"vfy{int(time.time()) % 100000}"
        self.created: list[tuple[str, str]] = []

    # ---- recording -----------------------------------------------------
    def add(self, op: str, label: str, call: LinkCall | None = None,
            db: dict[str, Any] | None = None, facts: dict[str, Any] | None = None,
            note: str = "") -> Record:
        self.seq += 1
        merged: dict[str, Any] = {}
        if call is not None:
            merged.update(call.facts)
            if call.unsupported:
                merged["unsupported"] = True
        if facts:
            merged.update(facts)
        rec = Record(
            seq=self.seq,
            op=op,
            label=label,
            via=self.link.via,
            at_ms=int((time.time() - self.started) * 1000),
            request=call.request if call else None,
            response=call.response if call else None,
            db=db,
            facts=merged,
            note=note or (call.note if call else ""),
        )
        self.trace.records.append(rec)
        if self.print_raw:
            print(render_record(rec, self.print_limit))
        return rec

    def problem(self, msg: str) -> None:
        self.trace.problems.append(msg)
        warn(msg)

    # ---- DB capture ----------------------------------------------------
    def db_snapshot(self, job_id: str = "", template_id: str = "",
                    artifact_id: str = "") -> dict[str, Any]:
        """Raw `SELECT *` fan-out for the ids known so far.

        Everything is captured by id rather than by a curated column list on
        purpose: a column that silently stops being written after the split is
        exactly what this tool must surface, and a hand-written projection
        would hide it.
        """
        snap: dict[str, Any] = {}
        if not self.db.enabled:
            return snap
        try:
            if job_id:
                rows = self.db.rows_where(TBL_IMAGE_JOB, "job_id", job_id)
                snap[TBL_IMAGE_JOB] = rows
                if rows and not template_id:
                    template_id = (rows[0].get("template_id") or "").strip()
                if rows and not artifact_id:
                    artifact_id = (rows[0].get("artifact_id") or "").strip()
            if artifact_id:
                snap[TBL_ARTIFACT] = self.db.rows_where(TBL_ARTIFACT, "artifact_id", artifact_id)
                snap[TBL_PLACEMENT] = self.db.rows_where(
                    TBL_PLACEMENT, "artifact_id", artifact_id, order_by="node_id")
            if template_id:
                snap[TBL_DEFINITION] = self.db.rows_where(
                    TBL_DEFINITION, "template_id", template_id)
                snap[TBL_REPLICA] = self.db.rows_where(
                    TBL_REPLICA, "template_id", template_id, order_by="node_id")
        except UnsafeQueryValue as e:
            warn(f"db snapshot skipped: {e}")
        return snap

    def db_job_row(self, job_id: str) -> dict[str, str]:
        if not job_id or not self.db.enabled:
            return {}
        try:
            return self.db.one_where(TBL_IMAGE_JOB, "job_id", job_id)
        except UnsafeQueryValue as e:
            warn(f"db read skipped: {e}")
            return {}

    # ---- environment ---------------------------------------------------
    def capture_environment(self) -> bool:
        """Record what this environment looks like. Returns False only when the
        entry point cannot serve the template control plane at all, since then
        there is nothing to record."""
        meta = LINKS[self.link_name]
        section(f"Environment - link={self.link_name} entry={self.link.entry}")
        info(meta["desc"])
        info(f"requires: {meta['requires']}")

        reachable_entry = False
        for label, call in self.link.health():
            rec = self.add("env.health", f"health: {label}", call)
            # /health on TC also reveals whether CUBE_TC_SERVE_TEMPLATE_API took
            # effect, because the node-view check is only registered then.
            if call.response and isinstance(call.response.get("json"), dict):
                rec.facts["checks"] = call.response["json"].get("checks")
            if label in ("CubeMaster", "CubeAPI") and call.facts.get("reachable"):
                reachable_entry = True

        if not self.db.available():
            self.db.enabled = False
            self.problem(
                f"database not queryable via `docker exec {self.cfg.mysql_container}`; "
                "continuing with API-only records (DB sections will be empty)")
        self.add("env.database", "database reachable",
                 facts={"reachable": self.db.enabled})

        call = self.link.probe_control_plane()
        rec = self.add("env.control_plane", "template control plane mounted at entry", call)
        mounted = bool(rec.facts.get("mounted"))
        if not mounted:
            hint = ""
            if self.link_name in ("tc", "master-proxy"):
                hint = "; set CUBE_TC_SERVE_TEMPLATE_API=true on CubeTemplateCenter"
            elif self.link.via == "sdk":
                hint = f"; check CubeAPI at {self.cfg.cubeapi_url}"
            self.problem(f"entry does not serve the template control plane{hint}")
        return mounted and reachable_entry

    # ---- polling -------------------------------------------------------
    def poll_until_terminal(self, job_id: str, template_id: str,
                            timeout: int) -> tuple[str, list[str]]:
        """Poll the job and record EVERY state transition with the DB row as it
        was at that moment.

        The transition timeline is the single most informative artifact this
        tool produces: local and remote reach READY through different internal
        routes (remote passes through BUILT), and a stall shows up as a state
        that stops advancing rather than as an opaque timeout.
        """
        deadline = time.time() + timeout
        timeline: list[str] = []
        last = ""
        status = ""
        polls = 0
        while time.time() < deadline:
            call = self.link.get_job(job_id, template_id)
            polls += 1
            if call.unsupported:
                self.add("create.poll", "poll job (unsupported by link)", call)
                return "UNKNOWN", timeline

            status = str(call.facts.get("status", "")).upper()
            phase = str(call.facts.get("phase", "")).upper()
            marker = f"{status}/{phase}"

            if status and marker != last:
                # Only transitions are recorded: a 200-poll build would
                # otherwise bury the interesting rows under identical ones.
                row = self.db_job_row(job_id)
                self.add(
                    "create.poll", f"state transition -> {marker}", call,
                    db={TBL_IMAGE_JOB: [row]} if row else None,
                    facts={"transition": marker, "poll_number": polls},
                )
                timeline.append(marker)
                last = marker

            if status in JOB_TERMINAL:
                return status, timeline
            time.sleep(self.cfg.poll_interval)

        timeline.append("TIMEOUT")
        self.problem(f"job {job_id} did not reach a terminal state within {timeout}s "
                     f"(last state {last or 'unknown'})")
        return status or "TIMEOUT", timeline

    # ---- summary -------------------------------------------------------
    def summarize(self) -> None:
        section(f"Recorded - link={self.link_name}")
        by_op: dict[str, int] = {}
        for rec in self.trace.records:
            by_op[rec.op] = by_op.get(rec.op, 0) + 1
        print(f"  records : {len(self.trace.records)}")
        for op in sorted(by_op):
            print(f"    {op:<24} {by_op[op]}")
        ctx = self.trace.context
        if ctx:
            print("  context :")
            for k in sorted(ctx):
                print(f"    {k:<24} {json.dumps(ctx[k], ensure_ascii=False, default=str)[:200]}")
        if self.trace.problems:
            print(f"\n  {C.Y}problems (this run may not be comparable):{C.X}")
            for p in self.trace.problems:
                print(f"    - {p}")
        else:
            print(f"  {C.G}no problems: this run is usable as comparison input{C.X}")
