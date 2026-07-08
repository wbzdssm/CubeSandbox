#!/usr/bin/env python3
# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
#
# End-to-end integration test for the Python SDK Volume API against a live
# CubeAPI + CubeMaster + Cubelet stack with a volume plugin configured.
#
# It exercises the full lifecycle:
#   1. Volume.create / list / get
#   2. Sandbox.create(volume_mounts=[...])  — mount the volume
#   3. write a file into the mount, read it back (persistence smoke test)
#   4. kill the sandbox, Volume.delete
#
# Unlike test_volume.py (pure unit tests, fully mocked), this script talks to a
# real deployment and needs env vars set:
#
#   export CUBE_API_URL=http://<cubeapi-host>:3000
#   export CUBE_TEMPLATE_ID=<template-id>
#   export CUBE_PROXY_NODE_IP=<cubeproxy-node-ip>   # required for remote data-plane
#   export CUBE_VOLUME_DRIVER=cos                   # optional; omit to use default plugin
#   export CUBE_VOLUME_MOUNT_PATH=/workspace        # optional; where to mount inside sandbox
#
# Usage: python3 integration_test_volume.py

import os
import sys
import uuid

# Import the SDK from source without installing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cubesandbox import Sandbox, Volume, VolumeMount  # noqa: E402

PASS = 0
FAIL = 0


def green(s):
    print("\033[32m  PASS: %s\033[0m" % s)


def red(s):
    print("\033[31m  FAIL: %s\033[0m" % s)


def yellow(s):
    print("\033[33m%s\033[0m" % s)


def assert_true(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        green(label)
    else:
        FAIL += 1
        red("%s (%s)" % (label, detail))


def assert_eq(label, expected, actual):
    assert_true(label, expected == actual, "expected=%r actual=%r" % (expected, actual))


def main():
    api_url = os.environ.get("CUBE_API_URL", "http://127.0.0.1:3000")
    template_id = os.environ.get("CUBE_TEMPLATE_ID")
    driver = os.environ.get("CUBE_VOLUME_DRIVER") or None
    mount_path = os.environ.get("CUBE_VOLUME_MOUNT_PATH", "/workspace")

    if not template_id:
        sys.exit("CUBE_TEMPLATE_ID is required for the volume e2e test")

    # Unique name so re-runs don't collide. Matches ^[a-zA-Z0-9_-]+$.
    vol_name = "e2e-vol-%s" % uuid.uuid4().hex[:12]

    print("========================================")
    print(" Python SDK Volume E2E Tests")
    print(" CubeAPI: %s" % api_url)
    print(" Driver:  %s" % (driver or "<default>"))
    print(" Volume:  %s" % vol_name)
    print("========================================")
    print()

    volume_id = None
    sb = None
    try:
        # 1. Create volume
        print("[Test 1] Volume.create")
        vol = Volume.create(vol_name, driver=driver)
        volume_id = vol.volume_id
        assert_true("create returns a volume_id", bool(vol.volume_id), "empty volume_id")
        assert_eq("create echoes name", vol_name, vol.name)
        print()

        # 2. List volumes
        print("[Test 2] Volume.list")
        vols = Volume.list()
        ids = [v.volume_id for v in vols]
        assert_true("list includes new volume", volume_id in ids, "got %s" % ids)
        print()

        # 3. Get single volume
        print("[Test 3] Volume.get")
        got = Volume.get(volume_id)
        assert_eq("get returns same volume_id", volume_id, got.volume_id)
        print()

        # 4. Create sandbox with the volume mounted
        print("[Test 4] Sandbox.create(volume_mounts=...)")
        sb = Sandbox.create(
            template=template_id,
            volume_mounts=[VolumeMount(name=volume_id, path=mount_path)],
        )
        assert_true("sandbox created", bool(sb.sandbox_id), "no sandbox id")
        print("  sandbox_id=%s" % sb.sandbox_id)
        print()

        # 5. Write + read back inside the mount (persistence smoke test)
        print("[Test 5] write/read inside the volume mount")
        target = "%s/e2e_probe.txt" % mount_path.rstrip("/")
        payload = "cube-volume-e2e-%s" % uuid.uuid4().hex[:8]
        sb.files.write(target, payload)
        content = sb.files.read(target)
        assert_eq("file round-trips through the volume mount", payload, content)
        print()

    finally:
        # Cleanup: kill sandbox first (so the volume can be detached), then
        # delete the volume. Deleting a volume does NOT auto-detach it.
        if sb is not None:
            try:
                sb.kill()
                green("cleanup: sandbox killed")
            except Exception as exc:  # noqa: BLE001
                red("cleanup: sandbox kill failed (%s)" % exc)
        if volume_id is not None:
            try:
                Volume.delete(volume_id)
                green("cleanup: volume deleted")
            except Exception as exc:  # noqa: BLE001
                red("cleanup: volume delete failed (%s)" % exc)

    total = PASS + FAIL
    print()
    print("========================================")
    if FAIL == 0:
        green("ALL PASSED: %d/%d tests" % (PASS, total))
    else:
        red("FAILED: %d/%d tests" % (FAIL, total))
        yellow(" Passed: %d/%d" % (PASS, total))
    print("========================================")
    sys.exit(FAIL)


if __name__ == "__main__":
    main()
