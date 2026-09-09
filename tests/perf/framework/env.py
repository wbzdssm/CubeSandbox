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
    machine_type: str = ""      # "腾讯云 BMSA5" / "Tencent Cloud BMSA5" etc.
    ip_address: str = ""        # primary non-loopback IPv4
    os_name: str = ""
    os_distro: str = ""         # "Ubuntu 22.04 LTS" / "TencentOS Server 3.1"
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
    gcc_version: str = ""       # `gcc --version` first line (e.g. "gcc 11.4.0")
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
    # Component versions (existing, kept for backward compat with report.py)
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
    # Release manifest (single source of truth on installed hosts:
    # /usr/local/services/cubetoolbox/release-manifest.json)
    release_version: str = ""          # e.g. "v1.0.0" — git tag of the release
    release_built_at: str = ""
    release_built_by: str = ""          # "github-actions" | "manual"
    release_git_commit: str = ""
    release_manifest_path: str = ""     # path we actually read, for debugging
    # Extra component versions (declared in the release manifest)
    cubemastercli_version: str = ""
    cubecli_version: str = ""
    network_agent_version: str = ""
    cube_agent_version: str = ""        # guest-side Rust agent
    cube_runtime_version: str = ""      # CubeShim/cube-runtime
    cube_egress_version: str = ""
    cube_proxy_version: str = ""
    cube_lifecycle_manager_version: str = ""
    # Guest image + kernel (declared)
    guest_agent_version: str = ""       # cube-agent version baked into the guest image
    guest_image_digest: str = ""
    guest_image_base: str = ""
    kernel_digest: str = ""
    kernel_pvm_version: str = ""
    kernel_pvm_digest: str = ""
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


def _detect_primary_ipv4() -> str:
    """Return the primary non-loopback IPv4 address of this host.

    Uses a UDP socket "trick" (connect to a public IP, read the local end of
    the ephemeral socket) so it works without a route to the internet — the
    UDP connect is stateless.  Returns "" on any failure so callers can render
    "-" instead of raising.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # 8.8.8.8 is a canonical "somewhere non-local" endpoint; we never
            # send anything, we just need the kernel to pick a source IP.
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    # Fallback: `hostname -I` first non-loopback token (Linux only).
    out = run_cmd(["sh", "-c", "hostname -I 2>/dev/null | tr ' ' '\\n' | grep -v '^127\\.' | head -n1"])
    return out or ""


def _detect_machine_type() -> str:
    """Best-effort detection of the machine model / cloud instance type.

    Prefers Tencent Cloud metadata (matches the fleet this perf suite targets),
    falls back to DMI product name (bare-metal / other clouds), then to a
    virt-what hint.  Returns "" when nothing can be determined.
    """
    # 1) Tencent Cloud CVM metadata: instance-name is the human label
    #    (e.g. "SA5.MEDIUM8"). 169.254.0.23 is TC's IMDS.
    for path in ("instance/instance-type", "instance/family"):
        out = run_cmd([
            "sh", "-c",
            f"curl -sS --max-time 1 http://metadata.tencentyun.com/latest/meta-data/{path}",
        ])
        if out and not out.startswith("<") and "not found" not in out.lower():
            return f"腾讯云 {out}"

    # 2) DMI product name (bare-metal servers / VMware / KVM / etc.)
    for path in ("/sys/class/dmi/id/product_name", "/sys/class/dmi/id/product_family"):
        out = run_cmd(["sh", "-c", f"cat {path} 2>/dev/null"])
        if out and out.lower() not in ("none", "to be filled by o.e.m.", "not specified", "system product name"):
            return out

    # 3) virt-what — coarse hypervisor hint if all else fails.
    out = run_cmd(["sh", "-c", "sudo -n virt-what 2>/dev/null | head -n1"])
    return out or ""


def _read_os_release_pretty() -> str:
    """Return PRETTY_NAME from /etc/os-release (e.g. "Ubuntu 22.04.4 LTS").

    Fallbacks: /etc/lsb-release DISTRIB_DESCRIPTION, then "".
    """
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        return line.partition("=")[2].strip().strip('"').strip("'")
        except OSError:
            continue
    try:
        with open("/etc/lsb-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("DISTRIB_DESCRIPTION="):
                    return line.partition("=")[2].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


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

    # Primary non-loopback IPv4 (best-effort, silent on failure).
    info.ip_address = _detect_primary_ipv4()
    # Machine model / cloud vendor label (e.g. "腾讯云 BMSA5").
    info.machine_type = _detect_machine_type()

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
        info.memory_total_gb = round(int(mem_kb) / (1024 * 1024)) if mem_kb.isdigit() else 0

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

        # OS distribution (PRETTY_NAME from /etc/os-release, e.g.
        # "Ubuntu 22.04.4 LTS" or "TencentOS Server 3.1"). This is the
        # user-facing OS name that people actually recognise, unlike
        # platform.system() which just says "Linux".
        info.os_distro = _read_os_release_pretty()

        # GCC version — useful for reproducing build-time perf differences.
        gcc_out = run_cmd(["sh", "-c", "gcc --version 2>/dev/null | head -n1"])
        if gcc_out:
            # `gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0` → keep the last token
            # (the semantic version) prefixed with `gcc `.
            parts = gcc_out.split()
            info.gcc_version = f"gcc {parts[-1]}" if parts else gcc_out

    # --- SDK / API ---
    info.api_url = cfg.api_url
    info.template_id = cfg.template_id or ""

    # --- Template metadata ---
    _template_image: str | None = None
    try:
        # Prefer the detail endpoint (GET /templates/{id}) for most fields.
        tmpl = Template.get(cfg.template_id, config=cfg)
        _template_image = tmpl.image_info or None
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
        info.template_instance_type = "N/A"
        info.template_status = "N/A"
        info.template_spec = "N/A"

    # imageInfo is missing from the detail endpoint; fall back to the list endpoint.
    if not _template_image:
        try:
            all_tmpls = Template.list(config=cfg)
            for t in all_tmpls:
                if t.template_id == cfg.template_id and t.image_info:
                    _template_image = t.image_info
                    break
        except Exception:
            pass
    info.template_image = _template_image or "N/A"

    # --- Component versions ---
    # Precedence: release-manifest.json (single source of truth on installed
    # hosts) > CubeAPI /cluster/versions (control-plane declared matrix) >
    # local binaries (`-V`/`-v` output). Each step only fills gaps; the
    # release manifest is authoritative when present.
    _collect_release_manifest(info)
    _collect_cluster_versions(info, cfg)
    _collect_local_versions(info)

    return info


# ===========================================================================
# Version collection
# ===========================================================================
#
# CubeSandbox exposes three complementary version sources:
#
#  1. release-manifest.json — written by
#     `deploy/one-click/build-release-bundle.sh` at install time. Lists every
#     component's declared version + commit + build_time + sha256 digest, plus
#     guest-image and kernel metadata. Located at
#     `/usr/local/services/cubetoolbox/release-manifest.json` (override via
#     the `CUBE_RELEASE_MANIFEST` env var). This is the single source of truth
#     for what was *installed*; we read it first.
#
#  2. CubeAPI `/cluster/versions` — control-plane view of what's actually
#     *running* across nodes. Fields are JSON-serialised in camelCase
#     (`controlPlane`, `buildTime`, `nodeID`) — a common footgun.
#
#  3. Local `-V`/`-v` binaries — final fallback when neither of the above
#     is reachable (e.g. running perf against a remote API from a workstation
#     that happens to also have the tools installed).
#
# `/health` used to be listed as a version source but it only returns
# `{status, sandboxes}` (see `CubeAPI/src/handlers/health.rs`); do NOT probe
# it for version info.


DEFAULT_RELEASE_MANIFEST = "/usr/local/services/cubetoolbox/release-manifest.json"

# Mapping from a component name in release-manifest.json to the EnvInfo
# attribute prefix we want to populate.  For each entry we set
# `<prefix>_version`, and (when present in the manifest) also
# `<prefix>_commit` / `<prefix>_build_time` if the dataclass declares them.
_MANIFEST_COMPONENT_MAP: dict[str, str] = {
    "cube-api": "cubeapi",
    "cubemaster": "cubemaster",
    "cubelet": "cubelet",
    "containerd-shim-cube-rs": "cube_shim",
    "cubemastercli": "cubemastercli",
    "cubecli": "cubecli",
    "network-agent": "network_agent",
    "cube-agent": "cube_agent",
    "cube-runtime": "cube_runtime",
    "cube-egress": "cube_egress",
    "cube-lifecycle-manager": "cube_lifecycle_manager",
}


def _collect_release_manifest(info: EnvInfo) -> None:
    """Populate component versions from ``release-manifest.json`` if present.

    The manifest is authoritative for *declared* versions and is the same
    file that `cubelet`/`cubemaster` consume, so what we record here matches
    what the running cluster is supposed to be.  Missing gracefully: on any
    error we simply leave fields empty and let the other collectors fill in.
    """
    import json

    path = os.environ.get("CUBE_RELEASE_MANIFEST", DEFAULT_RELEASE_MANIFEST)
    if not path or not os.path.isfile(path):
        return

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return

    info.release_manifest_path = path
    info.release_version = str(data.get("release_version", "") or "")
    info.release_built_at = str(data.get("built_at", "") or "")
    info.release_built_by = str(data.get("built_by", "") or "")
    info.release_git_commit = str(data.get("git_commit", "") or "")

    components = data.get("components", {})
    if isinstance(components, dict):
        for comp_name, prefix in _MANIFEST_COMPONENT_MAP.items():
            comp = components.get(comp_name)
            if not isinstance(comp, dict):
                continue
            _set_component_fields(info, prefix, comp)

    guest = data.get("guest_image", {})
    if isinstance(guest, dict):
        # Prefer manifest values; keep any pre-existing values otherwise.
        info.guest_image_version = str(guest.get("version", "") or info.guest_image_version)
        info.guest_image_digest = str(guest.get("digest_sha256", "") or "")
        info.guest_image_base = str(guest.get("base_image", "") or "")
        info.guest_agent_version = str(guest.get("agent_version", "") or "")

    kernel = data.get("kernel", {})
    if isinstance(kernel, dict):
        info.kernel_version_node = str(kernel.get("version", "") or info.kernel_version_node)
        info.kernel_digest = str(kernel.get("vmlinux_digest_sha256", "") or "")
        info.kernel_pvm_version = str(kernel.get("pvm_version", "") or "")
        info.kernel_pvm_digest = str(kernel.get("vmlinux_pvm_digest_sha256", "") or "")


def _set_component_fields(info: EnvInfo, prefix: str, comp: dict) -> None:
    """Set ``<prefix>_{version,commit,build_time}`` from a manifest entry.

    Silently skips attributes that don't exist on the dataclass so we don't
    have to declare every commit/build_time triplet up-front.
    """
    version = str(comp.get("version", "") or "")
    commit = str(comp.get("commit", "") or "")
    build_time = str(comp.get("build_time", "") or "")

    for attr, value in (
        (f"{prefix}_version", version),
        (f"{prefix}_commit", commit),
        (f"{prefix}_build_time", build_time),
    ):
        if value and hasattr(info, attr) and not getattr(info, attr):
            setattr(info, attr, value)


def _collect_cluster_versions(info: EnvInfo, cfg: Config) -> None:
    """Populate versions via CubeAPI ``/cluster/versions`` (running-state view).

    The endpoint is defined by ``CubeAPI/src/models/mod.rs::VersionMatrixView``
    and serialises to **camelCase** (``controlPlane`` / ``buildTime`` /
    ``nodeID`` / ``declaredVersion``) — we accept snake_case fallbacks in case
    the field-renaming changes.
    """
    try:
        import httpx
    except ImportError:
        return

    headers = {}
    api_key = os.environ.get("CUBE_API_KEY") or os.environ.get("E2B_API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        resp = httpx.get(f"{cfg.api_url}/cluster/versions", headers=headers, timeout=10)
    except (httpx.HTTPError, OSError):
        return
    if resp.status_code != 200:
        return
    try:
        data = resp.json()
    except ValueError:
        return
    if not isinstance(data, dict):
        return

    cp = data.get("controlPlane") or data.get("control_plane") or {}
    if isinstance(cp, dict):
        if not info.cubemaster_version:
            info.cubemaster_version = str(cp.get("version", "") or "")
        if not info.cubemaster_commit:
            info.cubemaster_commit = str(cp.get("commit", "") or "")
        if not info.cubemaster_build_time:
            info.cubemaster_build_time = str(
                cp.get("buildTime", "") or cp.get("build_time", "") or ""
            )

    nodes = data.get("nodes", [])
    if isinstance(nodes, list) and nodes:
        first_node = nodes[0]
        if isinstance(first_node, dict):
            for c in first_node.get("components", []) or []:
                if not isinstance(c, dict):
                    continue
                name = str(c.get("component", "") or "")
                ver = str(c.get("version", "") or "")
                if not ver:
                    continue
                # Same component-name → attr-prefix mapping we use for the
                # manifest, so /cluster/versions naturally fills gaps.
                prefix = _MANIFEST_COMPONENT_MAP.get(name)
                if prefix:
                    attr = f"{prefix}_version"
                    if hasattr(info, attr) and not getattr(info, attr):
                        setattr(info, attr, ver)
                # Special-cased legacy fields that don't follow the pattern.
                if name == "guest-image" and not info.guest_image_version:
                    info.guest_image_version = ver
                elif name == "kernel" and not info.kernel_version_node:
                    info.kernel_version_node = ver


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


# Local-binary probe table: (attr_prefix, [candidate paths], version flag).
# The prefix is joined with `_version`/`_commit`/`_build_time` so it matches
# the dataclass field names and the manifest map.  Kept as a plain list so
# it's obvious which components are covered by the fallback path.
_LOCAL_BINARY_PROBES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("cubeapi", (
        "/usr/local/services/cubetoolbox/CubeAPI/bin/cube-api",
        "/usr/local/bin/cube-api",
    ), "-V"),
    ("cubemaster", (
        "/usr/local/services/cubetoolbox/CubeMaster/bin/cubemaster",
        "/usr/local/bin/cubemaster",
    ), "-v"),
    ("cubemastercli", (
        "/usr/local/services/cubetoolbox/CubeMaster/bin/cubemastercli",
        "/usr/local/bin/cubemastercli",
    ), "-v"),
    ("cubecli", (
        "/usr/local/services/cubetoolbox/CubeMaster/bin/cubecli",
        "/usr/local/bin/cubecli",
    ), "-v"),
    ("cubelet", (
        "/usr/local/services/cubetoolbox/Cubelet/bin/cubelet",
        "/usr/local/bin/cubelet",
    ), "-v"),
    ("cube_shim", (
        "/usr/local/services/cubetoolbox/CubeShim/bin/containerd-shim-cube-rs",
        "/usr/local/bin/containerd-shim-cube-rs",
    ), "-v"),
    ("cube_runtime", (
        "/usr/local/services/cubetoolbox/CubeShim/bin/cube-runtime",
        "/usr/local/bin/cube-runtime",
    ), "-V"),
    ("network_agent", (
        "/usr/local/services/cubetoolbox/network-agent/bin/network-agent",
        "/usr/local/bin/network-agent",
    ), "-v"),
)


def _collect_local_versions(info: EnvInfo) -> None:
    """Fill remaining version fields from locally installed binaries.

    Iterates ``_LOCAL_BINARY_PROBES``; only touches fields that the earlier
    sources (release manifest, ``/cluster/versions``) left empty, so the
    authoritative-source precedence is preserved.
    """
    import shutil

    for prefix, paths, flag in _LOCAL_BINARY_PROBES:
        # Skip if we already have a version from a higher-priority source.
        attr_v = f"{prefix}_version"
        if getattr(info, attr_v, "") or not hasattr(info, attr_v):
            continue
        for path in paths:
            binary = shutil.which(path) or (path if os.path.exists(path) else None)
            if not binary:
                continue
            out = run_cmd([binary, flag])
            if not out:
                continue
            v, c, bt = _parse_version_output(out)
            if v:
                setattr(info, attr_v, v)
                if c and hasattr(info, f"{prefix}_commit") and not getattr(info, f"{prefix}_commit"):
                    setattr(info, f"{prefix}_commit", c)
                if bt and hasattr(info, f"{prefix}_build_time") and not getattr(info, f"{prefix}_build_time"):
                    setattr(info, f"{prefix}_build_time", bt)
                break
