# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Links: the different routes the same template lifecycle can take.

A "link" is one entry point plus the client used to reach it. Every link
implements the SAME set of logical operations under the SAME `op` names, which
is what makes two recorded runs comparable:

  http links  ->  /cube/* on CubeMaster or on CubeTemplateCenter
  sdk link    ->  sdk/python `cubesandbox.Template` -> CubeAPI /templates
                  -> CubeMaster (-> CubeTemplateCenter when build mode is remote)

The SDK link exists because that is what a user actually runs (the e2b-style
flow). A split that keeps `/cube/*` byte-identical but breaks the SDK path is
still a broken split, so the SDK is exercised through the real package rather
than by re-implementing its requests here.

Links never assert. Each method returns a LinkCall holding the raw request and
the raw response; the recorder stores it as-is.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from vfy_core import (
    LINKS,
    Client,
    Config,
    HttpResult,
    vlog,
)

SDK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sdk", "python"
)


@dataclass
class CreateSpec:
    """One template request expressed link-independently.

    The HTTP and SDK links name these fields differently (snake_case /cube/*
    vs camelCase CubeAPI), so the translation lives in each link. Keeping the
    spec neutral is what lets both links be asked for "the same" template.
    """
    image: str
    instance_type: str
    name: str  # alias on /cube/*, `name` on CubeAPI
    writable_layer_size: str = "1G"
    exposed_ports: list[int] = field(default_factory=lambda: [80])
    request_id: str = ""


@dataclass
class LinkCall:
    """Raw outcome of one link operation. No verdict, no expectation."""
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    unsupported: bool = False
    note: str = ""


def _plain(value: Any) -> Any:
    """Make SDK return values JSON-dumpable without losing anything."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


class Link:
    """Interface. Subclasses translate logical ops into concrete calls."""

    name = ""
    via = ""

    def __init__(self, cfg: Config, name: str):
        self.cfg = cfg
        self.name = name
        meta = LINKS[name]
        self.via = meta["via"]
        self.entry = cfg.entry_url(meta["entry"])

    # -- environment ----------------------------------------------------
    def health(self) -> list[tuple[str, LinkCall]]:
        raise NotImplementedError

    def probe_control_plane(self) -> LinkCall:
        """Is the template control plane actually mounted at this entry?"""
        raise NotImplementedError

    # -- negative probes (recorded as data, not asserted) ---------------
    def probe_unknown_template(self) -> LinkCall:
        raise NotImplementedError

    def probe_unknown_job(self) -> LinkCall:
        raise NotImplementedError

    def probe_empty_image(self) -> LinkCall:
        raise NotImplementedError

    def probe_bad_name(self, name: str) -> LinkCall:
        raise NotImplementedError

    # -- lifecycle ------------------------------------------------------
    def list_templates(self) -> LinkCall:
        raise NotImplementedError

    def create(self, spec: CreateSpec) -> LinkCall:
        raise NotImplementedError

    def get_job(self, job_id: str, template_id: str = "") -> LinkCall:
        raise NotImplementedError

    def get_template(self, ident: str, include_request: bool = False) -> LinkCall:
        raise NotImplementedError

    def get_artifact(self, artifact_id: str) -> LinkCall:
        return LinkCall(unsupported=True, note="artifact API not reachable through this link")

    def head_download(self, artifact_id: str, token: str) -> LinkCall:
        return LinkCall(unsupported=True, note="download API not reachable through this link")

    def rebuild(self, template_id: str) -> LinkCall:
        raise NotImplementedError

    def delete(self, ident: str) -> LinkCall:
        raise NotImplementedError


# ==========================================================================
# HTTP link: /cube/* directly on CubeMaster or CubeTemplateCenter
# ==========================================================================
class HttpLink(Link):
    def __init__(self, cfg: Config, name: str):
        super().__init__(cfg, name)
        self.client = Client(self.entry, cfg.http_timeout)
        # The artifact download endpoint is the Cubelet data plane. It is never
        # proxied and always lives on CubeMaster (design 9.7), so it gets its
        # own client regardless of the link under test.
        self.master = Client(cfg.master_url, cfg.http_timeout)

    @staticmethod
    def _call(r: HttpResult, **facts: Any) -> LinkCall:
        return LinkCall(r.request_snapshot(), r.snapshot(), facts)

    def health(self) -> list[tuple[str, LinkCall]]:
        out = []
        for label, url in (("CubeMaster", self.cfg.master_url),
                           ("CubeTemplateCenter", self.cfg.tc_url)):
            r = Client(url, 5).call("GET", "/health")
            out.append((label, self._call(r, reachable=r.http_status == 200)))
        return out

    def probe_control_plane(self) -> LinkCall:
        r = self.client.call("GET", "/cube/template", query={"template_id": "__vfy_absent__"})
        # A refused connection is not "mounted": only an actual answer that is
        # not 404 proves the route exists at this entry.
        return self._call(r, mounted=r.error is None and r.http_status != 404)

    def probe_unknown_template(self) -> LinkCall:
        return self._call(self.client.call(
            "GET", "/cube/template", query={"template_id": "tpl-does-not-exist-xyz"}))

    def probe_unknown_job(self) -> LinkCall:
        return self._call(self.client.call(
            "GET", "/cube/template/from-image", query={"job_id": "job-absent-xyz"}))

    def probe_empty_image(self) -> LinkCall:
        return self._call(self.client.call("POST", "/cube/template/from-image", body={
            "requestID": "vfy-neg-empty-image",
            "source_image_ref": "",
            "instance_type": self.cfg.instance_type,
        }))

    def probe_bad_name(self, name: str) -> LinkCall:
        return self._call(self.client.call("POST", "/cube/template/from-image", body={
            "requestID": "vfy-neg-bad-name",
            "source_image_ref": self.cfg.image,
            "instance_type": self.cfg.instance_type,
            "alias": name,
        }))

    def list_templates(self) -> LinkCall:
        r = self.client.call("GET", "/cube/template")
        data = (r.body or {}).get("data") if isinstance(r.body, dict) else None
        return self._call(r, count=len(data) if isinstance(data, list) else None)

    def create(self, spec: CreateSpec) -> LinkCall:
        r = self.client.call("POST", "/cube/template/from-image", body={
            "requestID": spec.request_id,
            "source_image_ref": spec.image,
            "instance_type": spec.instance_type,
            "alias": spec.name,
            "writable_layer_size": spec.writable_layer_size,
            "exposed_ports": spec.exposed_ports,
        })
        job = (r.body or {}).get("job") or {} if isinstance(r.body, dict) else {}
        return self._call(
            r,
            job_id=str(job.get("job_id", "")).strip(),
            template_id=str(job.get("template_id", "")).strip(),
            status=str(job.get("status", "")).upper(),
        )

    def get_job(self, job_id: str, template_id: str = "") -> LinkCall:
        r = self.client.call("GET", "/cube/template/from-image", query={"job_id": job_id})
        job = (r.body or {}).get("job") or {} if isinstance(r.body, dict) else {}
        return self._call(
            r,
            status=str(job.get("status", "")).upper(),
            phase=str(job.get("phase", "")).upper(),
            progress=job.get("progress"),
            error_message=job.get("error_message", ""),
        )

    def get_template(self, ident: str, include_request: bool = False) -> LinkCall:
        query: dict[str, Any] = {"template_id": ident}
        if include_request:
            query["include_request"] = "true"
        r = self.client.call("GET", "/cube/template", query=query)
        body = r.body if isinstance(r.body, dict) else {}
        return self._call(
            r,
            resolved_template_id=str(body.get("template_id", "")),
            status=str(body.get("status", "")),
            has_create_request=bool(body.get("create_request")),
        )

    def get_artifact(self, artifact_id: str) -> LinkCall:
        r = self.client.call("GET", "/cube/rootfs-artifact", query={"artifact_id": artifact_id})
        body = r.body if isinstance(r.body, dict) else {}
        artifact = body.get("artifact") or body
        return self._call(
            r,
            download_token=str(artifact.get("download_token", "") or ""),
            ext4_sha256=str(artifact.get("ext4_sha256", "") or ""),
            ext4_size_bytes=artifact.get("ext4_size_bytes"),
        )

    def head_download(self, artifact_id: str, token: str) -> LinkCall:
        r = self.master.call("HEAD", "/cube/template/artifact/download",
                             query={"artifact_id": artifact_id, "token": token})
        # Serves bytes: real HTTP status codes, no ret envelope.
        return self._call(r, content_length=r.headers.get("Content-Length", ""))

    def rebuild(self, template_id: str) -> LinkCall:
        r = self.client.call("POST", "/cube/template/redo", body={
            "requestID": "vfy-redo",
            "template_id": template_id,
            "failed_only": True,
        })
        return self._call(r)

    def delete(self, ident: str) -> LinkCall:
        r = self.client.call("DELETE", "/cube/template", body={
            "RequestID": "vfy-delete",
            "template_id": ident,
        })
        return self._call(r)


# ==========================================================================
# SDK link: cubesandbox.Template -> CubeAPI /templates -> CubeMaster
# ==========================================================================
class SdkLink(Link):
    """Drives the real Python SDK.

    CubeAPI answers with real HTTP status codes and the SDK turns non-2xx into
    exceptions, so an "error" here is recorded as an exception with its status
    code instead of a ret envelope. The comparison layer knows both dialects.
    """

    def __init__(self, cfg: Config, name: str):
        super().__init__(cfg, name)
        self.sdk: Any = None
        self.sdk_cfg: Any = None
        self.exc: Any = None
        self.import_error = ""
        self._import()

    def _import(self) -> None:
        if SDK_PATH not in sys.path:
            sys.path.insert(0, SDK_PATH)
        try:
            from cubesandbox import Config as SdkConfig  # noqa: PLC0415
            from cubesandbox import Template  # noqa: PLC0415
            from cubesandbox import _exceptions as exc  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            self.import_error = f"{type(e).__name__}: {e}"
            return
        self.sdk = Template
        self.exc = exc
        # api_key stays env-only (CUBE_API_KEY), never a CLI flag.
        self.sdk_cfg = SdkConfig(api_url=self.cfg.cubeapi_url)

    @property
    def ready(self) -> bool:
        return self.sdk is not None

    # -- call recording --------------------------------------------------
    def _invoke(self, sdk_method: str, http_hint: str, payload: dict[str, Any],
                fn, *args, **kwargs) -> tuple[LinkCall, Any]:
        """Run one SDK call and record it in the same raw shape as an HTTP call."""
        request = {
            "method": f"SDK {sdk_method}",
            "url": f"{self.cfg.cubeapi_url}{http_hint}",
            "path": http_hint,
            "body": payload or None,
        }
        if not self.ready:
            return LinkCall(request, None, {}, True,
                            f"sdk/python not importable: {self.import_error}"), None

        started = time.time()
        try:
            result = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - the exception IS the observation
            elapsed = int((time.time() - started) * 1000)
            status = getattr(e, "status_code", None)
            response = {
                "http_status": status or 0,
                "elapsed_ms": elapsed,
                "ret_code": None,
                "ret_name": None,
                "ret_msg": str(e),
                "error": f"{type(e).__name__}: {e}",
                "json": None,
                "raw": str(e),
                "content_type": "",
                "content_length": "",
            }
            vlog(f"SDK {sdk_method} -> {type(e).__name__}: {e}")
            return LinkCall(request, response, {"exception": type(e).__name__}), None

        elapsed = int((time.time() - started) * 1000)
        plain = _plain(result)
        response = {
            "http_status": 200,
            "elapsed_ms": elapsed,
            "ret_code": None,
            "ret_name": None,
            "ret_msg": "",
            "error": None,
            "json": plain,
            "raw": json.dumps(plain, ensure_ascii=False, sort_keys=True, default=str),
            "content_type": "application/json",
            "content_length": "",
        }
        vlog(f"SDK {sdk_method} -> ok ({elapsed}ms)")
        return LinkCall(request, response, {}), result

    # -- environment -----------------------------------------------------
    def health(self) -> list[tuple[str, LinkCall]]:
        out = []
        for label, url in (("CubeAPI", self.cfg.cubeapi_url),
                           ("CubeMaster", self.cfg.master_url),
                           ("CubeTemplateCenter", self.cfg.tc_url)):
            r = Client(url, 5).call("GET", "/health")
            out.append((label, LinkCall(r.request_snapshot(), r.snapshot(),
                                        {"reachable": r.http_status == 200})))
        return out

    def probe_control_plane(self) -> LinkCall:
        call, _ = self._invoke("Template.list()", "/templates", {},
                               lambda: self.sdk.list(config=self.sdk_cfg))
        call.facts["mounted"] = call.response is not None and call.response.get("error") is None
        return call

    # -- negative probes -------------------------------------------------
    def probe_unknown_template(self) -> LinkCall:
        return self._invoke(
            "Template.get('tpl-does-not-exist-xyz')", "/templates/tpl-does-not-exist-xyz", {},
            lambda: self.sdk.get("tpl-does-not-exist-xyz", config=self.sdk_cfg))[0]

    def probe_unknown_job(self) -> LinkCall:
        return self._invoke(
            "Template.get_build_status('tpl-absent-xyz','job-absent-xyz')",
            "/templates/tpl-absent-xyz/builds/job-absent-xyz/status", {},
            lambda: self.sdk.get_build_status("tpl-absent-xyz", "job-absent-xyz",
                                              config=self.sdk_cfg))[0]

    def probe_empty_image(self) -> LinkCall:
        # The SDK validates locally and raises ValueError before any request:
        # a legitimate difference from the HTTP link, recorded as such.
        return self._invoke("Template.build(image='')", "/templates", {"image": ""},
                            lambda: self.sdk.build(image="", config=self.sdk_cfg))[0]

    def probe_bad_name(self, name: str) -> LinkCall:
        return self._invoke(f"Template.build(name={name!r})", "/templates",
                            {"image": self.cfg.image, "name": name},
                            lambda: self.sdk.build(image=self.cfg.image, name=name,
                                                   instance_type=self.cfg.instance_type,
                                                   config=self.sdk_cfg))[0]

    # -- lifecycle -------------------------------------------------------
    def list_templates(self) -> LinkCall:
        call, result = self._invoke("Template.list()", "/templates", {},
                                    lambda: self.sdk.list(config=self.sdk_cfg))
        if result is not None:
            call.facts["count"] = len(result)
        return call

    def create(self, spec: CreateSpec) -> LinkCall:
        payload = {
            "image": spec.image,
            "name": spec.name,
            "instanceType": spec.instance_type,
            "writableLayerSize": spec.writable_layer_size,
            "exposedPorts": spec.exposed_ports,
        }
        call, result = self._invoke(
            "Template.build(...)", "/templates", payload,
            lambda: self.sdk.build(
                image=spec.image,
                name=spec.name,
                instance_type=spec.instance_type,
                writable_layer_size=spec.writable_layer_size,
                exposed_ports=spec.exposed_ports,
                config=self.sdk_cfg,
            ))
        if result is not None:
            call.facts.update({
                "job_id": result.job_id or "",
                "template_id": result.template_id or "",
                "status": (result.status or "").upper(),
            })
        return call

    def get_job(self, job_id: str, template_id: str = "") -> LinkCall:
        # CubeAPI scopes builds under a template, so the SDK needs both ids.
        if not template_id:
            return LinkCall(None, None, {}, True, "sdk build status requires a template_id")
        call, result = self._invoke(
            f"Template.get_build_status({template_id!r}, {job_id!r})",
            f"/templates/{template_id}/builds/{job_id}/status", {},
            lambda: self.sdk.get_build_status(template_id, job_id, config=self.sdk_cfg))
        if result is not None:
            call.facts.update({
                "status": (result.status or "").upper(),
                "phase": (result.phase or "").upper(),
                "progress": result.progress,
                "error_message": result.error_message or "",
            })
        return call

    def get_template(self, ident: str, include_request: bool = False) -> LinkCall:
        call, result = self._invoke(
            f"Template.get({ident!r})", f"/templates/{ident}", {},
            lambda: self.sdk.get(ident, config=self.sdk_cfg))
        if result is not None:
            call.facts.update({
                "resolved_template_id": result.template_id,
                "status": result.status,
                "has_create_request": bool(result.create_request),
                "replica_count": len(result.replicas or []),
                "build_count": len(result.builds or []),
            })
        return call

    def rebuild(self, template_id: str) -> LinkCall:
        return self._invoke(
            f"Template.rebuild({template_id!r})", f"/templates/{template_id}", {},
            lambda: self.sdk.rebuild(template_id, config=self.sdk_cfg))[0]

    def delete(self, ident: str) -> LinkCall:
        return self._invoke(
            f"Template.delete({ident!r})", f"/templates/{ident}", {},
            lambda: self.sdk.delete(ident, config=self.sdk_cfg))[0]


def build_link(cfg: Config, name: str) -> Link:
    if name not in LINKS:
        raise KeyError(f"unknown link: {name}")
    return SdkLink(cfg, name) if LINKS[name]["via"] == "sdk" else HttpLink(cfg, name)
