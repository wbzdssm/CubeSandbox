# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Environment info collection: host machine, CPU, memory, disk, template metadata."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import cubesandbox
from cubesandbox import Config, Template


@dataclass
class EnvInfo:
    """Collected environment information."""

    hostname: str = ""
    os_name: str = ""
    os_version: str = ""
    kernel: str = ""
    arch: str = ""
    cpu_model: str = ""
    cpu_cores_physical: int = 0
    cpu_cores_logical: int = 0
    cpu_sockets: int = 0
    numa_nodes: int = 0
    memory_total_gb: float = 0
    memory_type: str = ""
    disk_model: str = ""
    disk_size_gb: float = 0
    disk_fs: str = ""
    disk_type: str = ""
    python_version: str = ""
    sdk_version: str = ""
    api_url: str = ""
    template_id: str = ""
    template_image: str = ""
    template_instance_type: str = ""
    template_status: str = ""
    template_cpu: int = 0
    template_memory_mb: int = 0
    template_spec: str = ""
    timestamp: str = ""
    # Component versions
    cubeapi_version: str = ""
    cubeapi_commit: str = ""
    cubeapi_build_time: str = ""
    cubeapi_go_version: str = ""
    cubemaster_version: str = ""
    cubemaster_commit: str = ""
    cubemaster_build_time: str = ""
    cubelet_version: str = ""
    cube_shim_version: str = ""
    guest_image_version: str = ""
    kernel_version_node: str = ""
    # SDK / Python details
    processor: str = ""
    platform_summary: str = ""
    python_impl: str = ""
    sdk_import_path: str = ""
    httpx_version: str = ""
    requests_version: str = ""


def run_cmd(cmd: list[str]) -> str:
    """Run a shell command and return stdout, or '' on failure."""
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=10).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def get_free_mem_gb() -> float:
    """Return currently available memory in GiB (Linux only, 0 otherwise)."""
    mem_kb = run_cmd(["sh", "-c", "grep MemAvailable /proc/meminfo | awk '{print $2}'"])
    return round(int(mem_kb) / (1024 * 1024), 2) if mem_kb.isdigit() else 0


def collect_env_info(cfg: Config) -> EnvInfo:
    """Gather host machine, CPU, memory, disk, and template information."""
    info = EnvInfo()
    info.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Host ---
    info.hostname = platform.node()
    info.os_name = platform.system()
    info.os_version = platform.version()
    info.kernel = platform.release()
    info.arch = platform.machine()
    info.python_version = sys.version.split()[0]
    info.sdk_version = cubesandbox.__version__

    # --- SDK / Python details ---
    info.processor = platform.processor() or platform.machine()
    info.platform_summary = platform.platform()
    info.python_impl = platform.python_implementation() + " " + platform.python_version()
    try:
        info.sdk_import_path = os.path.abspath(cubesandbox.__file__)
    except Exception:
        info.sdk_import_path = ""
    try:
        import httpx
        info.httpx_version = httpx.__version__
    except Exception:
        pass
    try:
        import requests
        info.requests_version = requests.__version__
    except Exception:
        pass

    # Linux-specific
    if info.os_name == "Linux":
        # CPU model
        model = run_cmd(["sh", "-c", r"grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^\s*//'"])
        info.cpu_model = model

        # Physical cores
        phys = run_cmd(["sh", "-c", "grep -c '^cpu cores' /proc/cpuinfo | head -1"])
        info.cpu_cores_physical = int(phys) if phys.isdigit() else 0

        # Logical cores
        info.cpu_cores_logical = os.cpu_count() or 0

        # Sockets
        sockets = run_cmd(["sh", "-c", "grep '^physical id' /proc/cpuinfo | sort -u | wc -l"])
        info.cpu_sockets = int(sockets) if sockets.isdigit() else 0

        # NUMA nodes
        numa = run_cmd(["sh", "-c", "ls -d /sys/devices/system/node/node* 2>/dev/null | wc -l"])
        info.numa_nodes = int(numa) if numa.isdigit() else 0

        # Memory
        mem_kb = run_cmd(["sh", "-c", "grep MemTotal /proc/meminfo | awk '{print $2}'"])
        info.memory_total_gb = round(int(mem_kb) / (1024 * 1024), 1) if mem_kb.isdigit() else 0

        # Memory type (DDR). Match the standalone "Type:" line only; the
        # "Error Correction Type:" line also contains "Type:" and would win a
        # loose grep, so anchor to the field name and skip the correction line.
        mem_type = run_cmd(["sh", "-c", "sudo dmidecode -t memory 2>/dev/null | grep -E '^[[:space:]]*Type:' | grep -viE 'Unknown|Other' | head -1 | cut -d: -f2 | sed 's/^[[:space:]]*//' || echo ''"])
        info.memory_type = mem_type or "N/A"

        # Disk (root fs device model, size, fs type)
        root_dev = run_cmd(["sh", "-c", "df / | tail -1 | awk '{print $1}'"]).rstrip("0123456789")
        info.disk_fs = run_cmd(["sh", "-c", "df -T / | tail -1 | awk '{print $2}'"])
        info.disk_size_gb = round(
            int(run_cmd(["sh", "-c", "df -BG / | tail -1 | awk '{print $2}' | tr -d 'G'"]) or 0), 1
        )

        disk_model = run_cmd(["sh", "-c", f"lsblk -dno MODEL $(lsblk -no PKNAME $(df / | tail -1 | awk '{{print $1}}') 2>/dev/null) 2>/dev/null || echo ''"])
        info.disk_model = disk_model or "N/A"

        # Disk type (NVMe/SSD/HDD)
        rotational = run_cmd(["sh", "-c", f"cat /sys/block/$(basename $(df / | tail -1 | awk '{{print $1}}') | sed 's/[0-9]*$//')/queue/rotational 2>/dev/null || echo ''"])
        if rotational == "0":
            info.disk_type = "SSD"
        elif rotational == "1":
            info.disk_type = "HDD"
        else:
            info.disk_type = "Unknown"

        # Check NVMe
        if "nvme" in (root_dev or "").lower():
            info.disk_type = "NVMe SSD"

    # --- SDK / API ---
    info.api_url = cfg.api_url
    info.template_id = cfg.template_id or ""

    # --- Template metadata ---
    try:
        tmpl = Template.get(cfg.template_id, config=cfg)
        info.template_image = tmpl.image_info or "N/A"
        info.template_instance_type = tmpl.instance_type or "N/A"
        info.template_status = tmpl.status or "N/A"
        # Template size / spec (CPU + memory). memory_mb is normalized to GiB
        # for the human-readable spec string ("2 vCPU / 4 GiB").
        info.template_cpu = int(tmpl.cpu_count or 0)
        info.template_memory_mb = int(tmpl.memory_mb or 0)
        if info.template_cpu or info.template_memory_mb:
            mem_gib = round(info.template_memory_mb / 1024, 1) if info.template_memory_mb else 0
            info.template_spec = f"{info.template_cpu} vCPU / {mem_gib} GiB"
        else:
            info.template_spec = info.template_instance_type or "N/A"
    except Exception:
        info.template_image = "N/A"
        info.template_instance_type = "N/A"
        info.template_status = "N/A"
        info.template_spec = "N/A"

    # --- CubeAPI component versions (via /health) ---
    try:
        import httpx

        headers = {}
        api_key = os.environ.get("CUBE_API_KEY") or os.environ.get("E2B_API_KEY", "")
        if api_key:
            headers["X-API-Key"] = api_key
        resp = httpx.get(f"{cfg.api_url}/health", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                info.cubeapi_version = str(data.get("version", ""))
                info.cubeapi_commit = str(data.get("commit", ""))
                info.cubeapi_build_time = str(data.get("build_time", ""))
                info.cubeapi_go_version = str(data.get("go_version", ""))
    except Exception:
        pass

    # --- Cluster component versions (via CubeAPI /cluster/versions) ---
    try:
        import httpx

        headers = {}
        api_key = os.environ.get("CUBE_API_KEY") or os.environ.get("E2B_API_KEY", "")
        if api_key:
            headers["X-API-Key"] = api_key
        resp = httpx.get(f"{cfg.api_url}/cluster/versions", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                cp = data.get("control_plane", {})
                if isinstance(cp, dict):
                    info.cubemaster_version = str(cp.get("version", ""))
                    info.cubemaster_commit = str(cp.get("commit", ""))
                    info.cubemaster_build_time = str(cp.get("build_time", ""))
                nodes = data.get("nodes", [])
                if isinstance(nodes, list) and nodes:
                    first_node = nodes[0]
                    if isinstance(first_node, dict):
                        for c in first_node.get("components", []):
                            name = c.get("component", "")
                            ver = c.get("version", "")
                            if name == "cubelet":
                                info.cubelet_version = ver
                            elif name == "cube-shim":
                                info.cube_shim_version = ver
                            elif name == "guest-image":
                                info.guest_image_version = ver
                            elif name == "kernel":
                                info.kernel_version_node = ver
    except Exception:
        pass

    # --- Fallback: get versions from local binaries ---
    _collect_local_versions(info)

    return info


def _parse_version_output(output: str) -> tuple[str, str, str]:
    """Parse 'name v1.2.3 (commit) built at 2026-01-01T00:00:00Z' into (version, commit, build_time)."""
    import re

    version = ""
    commit = ""
    build_time = ""
    # pattern: v0.5.1 (a164417...) built at 2026-07-11T08:09:01Z
    m = re.search(r"v?(\d+\.\d+\.\d+(?:[-\w.]*)?)\s*\((\w+)\)\s*built at\s*(\S+)", output)
    if m:
        version = m.group(1)
        commit = m.group(2)
        build_time = m.group(3)
    return version, commit, build_time


def _collect_local_versions(info: EnvInfo) -> None:
    """Try to get component versions from locally installed binaries."""
    import shutil

    # CubeAPI binary (priority: HTTP response, then local binary)
    if not info.cubeapi_version:
        for path in (
            "/usr/local/services/cubetoolbox/CubeAPI/bin/cube-api",
            "/usr/local/bin/cube-api",
        ):
            cubeapi_bin = shutil.which(path) or (path if os.path.exists(path) else None)
            if cubeapi_bin:
                out = run_cmd([cubeapi_bin, "-V"])
                if out:
                    v, c, bt = _parse_version_output(out)
                    info.cubeapi_version = v or info.cubeapi_version
                    info.cubeapi_commit = c or info.cubeapi_commit
                    info.cubeapi_build_time = bt or info.cubeapi_build_time
                    break

    # CubeMaster binary
    if not info.cubemaster_version:
        for path in (
            "/usr/local/services/cubetoolbox/CubeMaster/bin/cubemaster",
            "/usr/local/bin/cubemaster",
        ):
            cm_bin = shutil.which(path) or (path if os.path.exists(path) else None)
            if cm_bin:
                out = run_cmd([cm_bin, "-v"])
                if out:
                    v, c, bt = _parse_version_output(out)
                    info.cubemaster_version = v or info.cubemaster_version
                    info.cubemaster_commit = c or info.cubemaster_commit
                    info.cubemaster_build_time = bt or info.cubemaster_build_time
                    break

    # Cubelet binary
    if not info.cubelet_version:
        for path in (
            "/usr/local/services/cubetoolbox/Cubelet/bin/cubelet",
            "/usr/local/bin/cubelet",
        ):
            cl_bin = shutil.which(path) or (path if os.path.exists(path) else None)
            if cl_bin:
                out = run_cmd([cl_bin, "-v"])
                if out:
                    v, c, bt = _parse_version_output(out)
                    info.cubelet_version = v or info.cubelet_version
                    break

    # CubeShim binary
    if not info.cube_shim_version:
        for path in (
            "/usr/local/services/cubetoolbox/CubeShim/bin/containerd-shim-cube-rs",
            "/usr/local/bin/containerd-shim-cube-rs",
        ):
            sh_bin = shutil.which(path) or (path if os.path.exists(path) else None)
            if sh_bin:
                out = run_cmd([sh_bin, "-v"])
                if out:
                    v, c, bt = _parse_version_output(out)
                    info.cube_shim_version = v or info.cube_shim_version
                    break
