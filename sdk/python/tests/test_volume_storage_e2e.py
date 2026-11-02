# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in MinIO/COS object-store E2E through a loopback E2B-shaped gateway.

The gateway in this test is a stub, not a deployed volume-content service. It
verifies real backend bytes, SDK HTTP routing and cleanup, not tenant isolation,
production token issuance, metadata fidelity or server-side volume leases.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import posixpath
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from cubesandbox import Config, Sandbox, Volume, VolumeFileType, VolumePathNotFoundException

pytestmark = pytest.mark.e2e
PAYLOAD = b"\x00\xffvolume-backend-probe-\xe4\xb8\xad\xe6\x96\x87\n"


def backend_settings(name: str, request) -> dict[str, str]:
    if not request.config.getoption("--run-e2e") or os.environ.get("CUBE_VOLUME_BACKEND_E2E") != "1":
        pytest.skip("requires --run-e2e and CUBE_VOLUME_BACKEND_E2E=1; creates a temporary object")
    prefix = "CUBE_VOLUME_" + name.upper() + "_"
    keys = ("VOLUME_ID", "BUCKET", "REGION", "SECRET_ID", "SECRET_KEY") if name == "cos" else (
        "VOLUME_ID", "BUCKET", "REGION", "ENDPOINT", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY")
    values = {key: os.environ.get(prefix + key, "") for key in keys}
    missing = [prefix + key for key, value in values.items() if not value or "<" in value or ">" in value]
    if missing:
        pytest.fail("missing storage E2E configuration: " + ", ".join(missing), pytrace=False)
    volume_id = values["VOLUME_ID"]
    if not volume_id.isascii() or not volume_id.replace("-", "").replace("_", "").isalnum():
        pytest.fail("volume ID must contain only ASCII letters, digits, '-' or '_'", pytrace=False)
    if name == "minio":
        endpoint = urlsplit(values["ENDPOINT"])
        if endpoint.scheme not in ("http", "https") or not endpoint.netloc or endpoint.username or endpoint.password or endpoint.path not in ("", "/"):
            pytest.fail("MinIO endpoint must be an HTTP(S) origin without userinfo or path", pytrace=False)
    return values


def required_module(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        pytest.fail(f"missing {name}; install pip extra .[storage-e2e]", pytrace=False)


def object_store(name: str, settings: dict[str, str]):
    bucket = settings["BUCKET"]
    if name == "minio":
        boto3 = required_module("boto3")
        client = boto3.client(
            "s3", endpoint_url=settings["ENDPOINT"], region_name=settings["REGION"],
            aws_access_key_id=settings["ACCESS_KEY_ID"],
            aws_secret_access_key=settings["SECRET_ACCESS_KEY"],
        )

        class Store:
            def put(self, key: str):
                client.put_object(Bucket=bucket, Key=key, Body=PAYLOAD)

            def read(self, key: str) -> bytes:
                body = client.get_object(Bucket=bucket, Key=key)["Body"]
                try:
                    return body.read()
                finally:
                    body.close()

            def delete(self, key: str):
                client.delete_object(Bucket=bucket, Key=key)
                try:
                    client.head_object(Bucket=bucket, Key=key)
                except client.exceptions.ClientError as exc:
                    if exc.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
                        return
                    raise
                raise AssertionError("probe object still exists after deletion")

        return Store()

    cos = required_module("qcloud_cos")
    client = cos.CosS3Client(cos.CosConfig(
        Region=settings["REGION"], SecretId=settings["SECRET_ID"],
        SecretKey=settings["SECRET_KEY"], Scheme="https",
    ))

    class Store:
        def put(self, key: str):
            client.put_object(Bucket=bucket, Key=key, Body=PAYLOAD)

        def read(self, key: str) -> bytes:
            body = client.get_object(Bucket=bucket, Key=key)["Body"]
            try:
                return body.get_raw_stream().read()
            finally:
                close = getattr(body, "close", None)
                if close:
                    close()

        def delete(self, key: str):
            client.delete_object(Bucket=bucket, Key=key)
            try:
                client.head_object(Bucket=bucket, Key=key)
            except cos.CosServiceError as exc:
                if exc.get_status_code() == 404:
                    return
                raise
            raise AssertionError("probe object still exists after deletion")

    return Store()


def entry(path: str, size: int) -> dict:
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        "name": posixpath.basename(path), "type": "file", "path": path,
        "size": size, "mode": 0o644, "uid": 0, "gid": 0,
        "atime": stamp, "mtime": stamp, "ctime": stamp,
    }


@pytest.mark.parametrize("backend", ["minio", "cos"])
def test_direct_read_real_backend(backend, request, monkeypatch):
    settings = backend_settings(backend, request)
    store = object_store(backend, settings)
    volume_id = settings["VOLUME_ID"]
    relative = "sdk-direct-probe/file-" + uuid.uuid4().hex + ".bin"
    key = f"volumes/{volume_id}/{relative}"
    path = "/" + relative
    token = "probe-" + uuid.uuid4().hex
    reads = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status: int, data: bytes, content_type: str = "application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            reads.append((url.path, query))
            if self.headers.get("Authorization") != "Bearer " + token:
                return self.respond(401, b'{}')
            if self.headers.get("X-API-Key") or self.headers.get("X-Access-Token"):
                return self.respond(403, b'{}')
            prefix = f"/volumecontent/{volume_id}/"
            if not url.path.startswith(prefix) or len(query.get("path", [])) != 1:
                return self.respond(404, b'{}')
            target = query["path"][0]
            operation = url.path[len(prefix):]
            if operation == "dir" and target == posixpath.dirname(path):
                if query.get("depth") != ["1"]:
                    return self.respond(400, b'{}')
                return self.respond(200, json.dumps([entry(path, len(PAYLOAD))]).encode())
            if target != path:
                return self.respond(404, b'{}')
            if operation == "path":
                return self.respond(200, json.dumps(entry(path, len(PAYLOAD))).encode())
            if operation == "file":
                try:
                    data = store.read(key)
                except Exception:
                    return self.respond(502, b'{}')
                return self.respond(200, data, "application/octet-stream")
            return self.respond(404, b'{}')

    def no_sandbox(*args, **kwargs):
        pytest.fail("direct-volume E2E must not create or connect a sandbox", pytrace=False)

    monkeypatch.setattr(Sandbox, "create", no_sandbox)
    monkeypatch.setattr(Sandbox, "connect", no_sandbox)
    monkeypatch.setattr(Sandbox, "__init__", no_sandbox)
    server = None
    put_attempted = False
    try:
        # The generated key is under this volume only; never delete a volume or bucket.
        put_attempted = True
        store.put(key)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        volume = Volume(volume_id, backend, token, config=Config(
            volume_api_url=f"http://127.0.0.1:{server.server_port}",
        ))
        assert hashlib.sha256(volume.read_file(path, format="bytes")).digest() == hashlib.sha256(PAYLOAD).digest()
        with closing(volume.read_file(path, format="stream")) as chunks:
            assert hashlib.sha256(b"".join(chunks)).digest() == hashlib.sha256(PAYLOAD).digest()
        info = volume.get_info(path)
        assert info.type is VolumeFileType.FILE and info.size == len(PAYLOAD)
        entries = volume.list(posixpath.dirname(path), depth=1)
        assert len(entries) == 1 and entries[0].path == path
        assert volume.exists(path)
        missing = "/sdk-direct-probe/missing-" + uuid.uuid4().hex
        assert not volume.exists(missing)
        with pytest.raises(VolumePathNotFoundException):
            volume.read_file(missing)
        assert any(route.endswith("/file") for route, _ in reads)
        assert any(route.endswith("/dir") for route, _ in reads)
        assert any(route.endswith("/path") for route, _ in reads)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        if put_attempted:
            store.delete(key)


@pytest.mark.parametrize("backend", ["minio", "cos"])
def test_storage_e2e_loopback_contract(backend, request, monkeypatch):
    """Exercise both E2E paths offline without requiring credentials or remote storage."""
    import sys

    calls = []

    class FakeStore:
        def put(self, key):
            calls.append(("put", key))

        def read(self, key):
            calls.append(("read", key))
            return PAYLOAD

        def delete(self, key):
            calls.append(("delete", key))

    monkeypatch.setattr(sys.modules[__name__], "backend_settings", lambda name, req: {"VOLUME_ID": "test-volume"})
    monkeypatch.setattr(sys.modules[__name__], "object_store", lambda name, settings: FakeStore())
    test_direct_read_real_backend(backend, request, monkeypatch)
    key = calls[0][1]
    assert key.startswith("volumes/test-volume/sdk-direct-probe/file-")
    assert calls[0] == ("put", key)
    assert sum(method == "read" for method, _ in calls) == 2
    assert calls[-1] == ("delete", key)
