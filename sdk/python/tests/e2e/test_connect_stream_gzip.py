#!/usr/bin/env python3
# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
#
# E2E test: verify that Connect stream commands with large output work correctly
# after the Accept-Encoding fix.  Without the fix, nginx gzip-compresses the
# stream response and the raw byte parser misreads gzip magic as a frame header
# → RuntimeError("Connect stream message too large: ...").
#
# Requires a running CubeAPI backend.  Set CUBE_API_URL and CUBE_TEMPLATE_ID
# (or CUBE_API_KEY if auth is enabled).
#
# Usage:
#   CUBE_API_URL=http://127.0.0.1:3000 \
#   CUBE_TEMPLATE_ID=tpl-xxxx \
#   python3 test_connect_stream_gzip.py

from __future__ import annotations

import base64
import json
import os
import struct
import sys

import httpx

# Allow import of the local SDK without installation.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Helpers — minimal Connect stream client that bypasses Sandbox.auth
# ---------------------------------------------------------------------------

ENVD_PORT = 49983
MAX_SIZE = 64 * 1024 * 1024


def connect_envelope(flags: int, payload: bytes) -> bytes:
    return bytes([flags]) + struct.pack(">I", len(payload)) + payload


def parse_process_start_stream(chunks) -> tuple[str, str, int | None]:
    """Return (stdout, stderr, exit_code) from raw Connect stream chunks."""
    stdout: list[str] = []
    stderr: list[str] = []
    exit_code: int | None = None
    buf = bytearray()

    for chunk in chunks:
        if not chunk:
            continue
        buf.extend(chunk)
        while len(buf) >= 5:
            flags = buf[0]
            size = struct.unpack(">I", buf[1:5])[0]
            if size > MAX_SIZE:
                raise RuntimeError(
                    f"Connect stream message too large: {size} bytes"
                )
            if len(buf) < 5 + size:
                break
            raw = bytes(buf[5 : 5 + size])
            del buf[: 5 + size]

            if flags & 0x02:  # CONNECT_END_STREAM_FLAG
                if raw:
                    end = json.loads(raw)
                    if end.get("error"):
                        raise RuntimeError(
                            f"Connect stream error: {end['error']}"
                        )
                continue

            event = (json.loads(raw).get("event") or {})
            data = event.get("data") or {}
            if data.get("stdout"):
                stdout.append(base64.b64decode(data["stdout"]).decode())
            if data.get("stderr"):
                stderr.append(base64.b64decode(data["stderr"]).decode())
            end = event.get("end")
            if end is not None:
                ec = end.get("exitCode") or end.get("exit_code")
                if ec is not None:
                    exit_code = int(ec)

    return "".join(stdout), "".join(stderr), exit_code


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

PASS = 0
FAIL = 0


def green(msg: str) -> None:
    print(f"\033[32m  PASS: {msg}\033[0m")


def red(msg: str) -> None:
    print(f"\033[31m  FAIL: {msg}\033[0m")


def assert_true(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        green(label)
    else:
        FAIL += 1
        red(f"{label} ({detail})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    api_url = os.environ.get("CUBE_API_URL", "http://127.0.0.1:3000")
    template_id = os.environ.get("CUBE_TEMPLATE_ID")
    api_key = os.environ.get("CUBE_API_KEY") or os.environ.get("E2B_API_KEY")

    if not template_id:
        sys.exit("CUBE_TEMPLATE_ID is required")

    auth_headers: dict[str, str] = {}
    if api_key:
        auth_headers["X-API-Key"] = api_key

    print("=" * 60)
    print(" Connect Stream gzip E2E Test")
    print(f" API: {api_url}")
    print(f" Template: {template_id}")
    print("=" * 60)
    print()

    # -- Step 1: Create a sandbox -------------------------------------------
    print("[1] Creating sandbox …")
    client = httpx.Client(
        base_url=api_url,
        headers={"Accept-Encoding": "identity", **auth_headers},
        timeout=httpx.Timeout(connect=10, read=None, write=30, pool=30),
    )
    resp = client.post("/sandboxes", json={"templateID": template_id}, timeout=30)
    if resp.status_code != 200:
        sys.exit(f"Create sandbox failed: HTTP {resp.status_code} {resp.text}")
    sb_data = resp.json()
    sandbox_id = sb_data["sandboxID"]
    sandbox_ip = sb_data.get("sandboxIP") or sb_data.get("sandboxIP", "0.0.0.0")
    envd_access_token = sb_data.get("envdAccessToken", "")
    print(f"  sandboxID: {sandbox_id}  IP: {sandbox_ip}")
    print()

    should_cleanup = os.environ.get("CUBE_E2E_KEEP_SANDBOX", "") != "1"

    try:
        # -- Step 2: Run a command with large output (>1000 bytes) ----------
        # Without Accept-Encoding: identity, nginx would gzip this response
        # and the gzip magic bytes would break the Connect stream parser.
        print("[2] Running command with ~2000-byte stdout …")
        payload = {
            "process": {
                "cmd": "/usr/bin/python3",
                "args": ["-c", "print('x' * 2000)"],
            }
        }
        headers = {
            "Content-Type": "application/connect+json",
            "connect-protocol-version": "1",
            "connect-content-encoding": "identity",
            "Authorization": "Basic cm9vdDo=",
            **auth_headers,
        }
        if envd_access_token:
            headers["X-Access-Token"] = envd_access_token

        url = f"http://{sandbox_ip}:{ENVD_PORT}/process.Process/Start"
        with client.stream(
            "POST",
            url,
            content=connect_envelope(0, json.dumps(payload).encode()),
            headers=headers,
        ) as stream_resp:
            stdout, stderr, exit_code = parse_process_start_stream(
                stream_resp.iter_raw()
            )

        assert_true(
            "stdout is ~2000 x's",
            len(stdout) >= 1990 and "x" * 1900 in stdout,
            f"len={len(stdout)}",
        )
        assert_true("stderr is empty", stderr == "", f"stderr={stderr!r}")
        assert_true("exit code 0", exit_code == 0, f"exit_code={exit_code}")
        print()

        # -- Step 3: Run a second command (stderr output) --------------------
        print("[3] Running command with stderr output …")
        payload2 = {
            "process": {
                "cmd": "/bin/sh",
                "args": [
                    "-c",
                    "echo ok > /dev/stdout; echo err > /dev/stderr",
                ],
            }
        }
        with client.stream(
            "POST",
            url,
            content=connect_envelope(0, json.dumps(payload2).encode()),
            headers=headers,
        ) as stream_resp:
            stdout2, stderr2, exit_code2 = parse_process_start_stream(
                stream_resp.iter_raw()
            )

        assert_true("stdout has 'ok'", "ok" in stdout2, stdout2)
        assert_true("stderr has 'err'", "err" in stderr2, stderr2)
        assert_true("exit code 0", exit_code2 == 0, f"exit_code={exit_code2}")
        print()

        # -- Step 4: Run a command with binary output -----------------------
        print("[4] Binary output (>4096 bytes) …")
        payload3 = {
            "process": {
                "cmd": "/usr/bin/python3",
                "args": [
                    "-c",
                    "import sys; d = bytes(range(256)) * 20; sys.stdout.buffer.write(d)",
                ],
            }
        }
        with client.stream(
            "POST",
            url,
            content=connect_envelope(0, json.dumps(payload3).encode()),
            headers=headers,
        ) as stream_resp:
            stdout3, stderr3, exit_code3 = parse_process_start_stream(
                stream_resp.iter_raw()
            )

        # 256 bytes * 20 = 5120 bytes
        assert_true(
            "5120 bytes binary output",
            len(stdout3) == 5120,
            f"len={len(stdout3)}",
        )
        assert_true(
            "binary output correct",
            bytes(range(256)) * 20 == stdout3.encode("latin-1"),
        )
        assert_true("exit code 0", exit_code3 == 0, f"exit_code={exit_code3}")
        print()

    finally:
        if should_cleanup:
            print("[cleanup] Killing sandbox …")
            try:
                client.delete(f"/sandboxes/{sandbox_id}", timeout=15)
            except Exception:
                pass
        client.close()

    # -- Summary ------------------------------------------------------------
    print()
    print("=" * 60)
    print(f" Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
