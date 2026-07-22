# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Configuration resolution & runtime tunables for the e2e test suite."""

from __future__ import annotations

import os
import sys

from cubesandbox import Config

# ---------------------------------------------------------------------------
# Tunables (env-driven)
# ---------------------------------------------------------------------------


def _parse_int_list(env_name: str, default: list[int]) -> list[int]:
    """Parse a comma-separated int list from *env_name*, falling back to *default*."""
    raw = os.environ.get(env_name)
    if not raw:
        return list(default)
    try:
        parsed = [int(x.strip()) for x in raw.split(",") if x.strip()]
        return parsed or list(default)
    except ValueError:
        return list(default)


PERF_ROUNDS = int(os.environ.get("CUBE_PERF_ROUNDS", "10"))
DENSITY_COUNT = int(os.environ.get("CUBE_DENSITY_COUNT", "100"))

# Warm-up rounds discarded before measured rounds (removes cold-start spikes),
# and settle seconds slept between rounds (lets the node quiesce). Mirrors the
# methodology of the standalone examples/snapshot-rollback-clone bench scripts.
PERF_WARMUP = int(os.environ.get("CUBE_PERF_WARMUP", "1"))
PERF_SETTLE = float(os.environ.get("CUBE_PERF_SETTLE", "0"))

# Dirty-page write sizes (MB) swept by the snapshot-dirty scenario. Matches the
# published baseline sweep; override via CUBE_DIRTY_SWEEP, e.g. "0,100,1024".
DIRTY_SWEEP = _parse_int_list("CUBE_DIRTY_SWEEP", [0, 10, 50, 100, 200, 500, 800, 1024])

# Concurrency ladders, matching the published benchmark report
# (cubesandbox.com/zh/blog "CubeSandbox 核心操作性能基准测试报告"):
#   - the "light" ladder 1/5/10 for the snapshot-create / rollback / pause-resume
#     scenarios (report §4.1 / §4.4 / §4.6);
#   - the "create" ladder 1/10/20/50 for the heavier template-create /
#     create-from-snapshot / clone scenarios (report §3.2 / §4.3 / §4.5).
# Override via CUBE_PERF_CONCURRENCY / CUBE_CREATE_CONCURRENCY. On a small node
# the top levels may hit CubeMaster error 130597 ("no more resource"); trim the
# ladders there.
CONCURRENCY_LEVELS = _parse_int_list("CUBE_PERF_CONCURRENCY", [1, 5, 10])
CREATE_CONCURRENCY_LEVELS = _parse_int_list("CUBE_CREATE_CONCURRENCY", [1, 10, 20, 50])

# Node-local cleanup of residual micro-VMs (mvm) between rounds. Perf runs leak
# residual sandboxes that the SDK ``kill()`` does not always reap, eventually
# exhausting node resources. We shell out to the node-local cubecli to force a
# clean cold-start state before each measured round.
#   CUBE_PERF_CLEANUP  - set to "0" to disable (default: enabled)
#   CUBE_CLEANUP_CMD   - override the cleanup command
CLEANUP_ENABLED = os.environ.get("CUBE_PERF_CLEANUP", "1") != "0"
CLEANUP_CMD = os.environ.get("CUBE_CLEANUP_CMD", "echo y | cubecli unsafe destroyall -f")


def resolve_config() -> Config:
    """Resolve a `Config` from environment variables, auto-discovering a
    READY template if `CUBE_TEMPLATE_ID` is not set.
    """
    # CUBE_API_URL is optional: when unset the SDK Config defaults to the
    # local CubeAPI at http://127.0.0.1:3000, so `python3 -m e2e_perf` works
    # out of the box against a local backend. Point it elsewhere via
    # CUBE_API_URL (env or .env) to hit a remote deployment.
    cfg = Config()

    if not cfg.template_id:
        print("Discovering a READY template ...")
        import httpx

        try:
            headers = {}
            api_key = os.environ.get("CUBE_API_KEY") or os.environ.get("E2B_API_KEY", "")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            resp = httpx.get(
                f"{cfg.api_url}/templates",
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            templates = resp.json()
            for t in templates:
                if t.get("templateID") and t.get("status", "").upper() == "READY":
                    cfg.template_id = t["templateID"]
                    break
            if not cfg.template_id and templates:
                cfg.template_id = templates[0].get("templateID") or ""
        except Exception as exc:
            sys.exit(f"Failed to discover template from {cfg.api_url}: {exc}")
        if not cfg.template_id:
            sys.exit("No READY templates found; set CUBE_TEMPLATE_ID")

    print(f"Target API : {cfg.api_url}")
    print(f"Template   : {cfg.template_id}")
    if cfg.proxy_node_ip:
        print(f"Proxy      : {cfg.proxy_node_ip}:{cfg.proxy_port}")
    print()
    return cfg
