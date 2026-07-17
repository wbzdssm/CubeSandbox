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
    timestamp: str = ""
    # Component versions
    cubeapi_version: str = ""
    cubeapi_commit: str = ""
    cubeapi_build_time: str = ""
    cubeapi_go_version: str = ""


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

        # Memory type (DDR)
        mem_type = run_cmd(["sh", "-c", "sudo dmidecode -t memory 2>/dev/null | grep -m1 'Type:' | awk '{print $2, $3}' || echo ''"])
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
    except Exception:
        info.template_image = "N/A"
        info.template_instance_type = "N/A"
        info.template_status = "N/A"

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

    return info
