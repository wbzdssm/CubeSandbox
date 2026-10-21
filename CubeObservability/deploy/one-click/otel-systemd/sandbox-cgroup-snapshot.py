#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Tencent. All rights reserved.
"""Take a node-local CubeSandbox cgroup and metadata snapshot.

This is a diagnostic tool, not a metrics collector.  Sandbox inventory and the
persisted cgroup assignment baseline come from cubecli; cgroup files are then
read directly from the host.
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_CUBECLI = "/usr/local/services/cubetoolbox/Cubelet/bin/cubecli"
DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"
POOL_PREFIXES = (
    "/cube_sandbox/sandbox/",
    "/cube_sandbox_v2/sandbox/",
)
BASELINE_FIELDS = {
    "cpu_usage_total_ns": "CPUUsageTotalNS",
    "cpu_user_total_ns": "CPUUserTotalNS",
    "cpu_system_total_ns": "CPUSystemTotalNS",
    "cpu_throttled_total_ns": "CPUThrottledTotalNS",
    "cpu_periods_total": "CPUPeriodsTotal",
    "cpu_throttled_periods_total": "CPUThrottledPeriodsTotal",
    "memory_failures_total": "MemoryFailuresTotal",
}


class SnapshotError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read CubeBox inventory with cubecli and snapshot each sandbox's "
            "host cgroup. With no sandbox ID, all local CubeBoxes are included."
        )
    )
    parser.add_argument(
        "sandbox_ids",
        nargs="*",
        metavar="SANDBOX_ID",
        help="one or more full or unambiguous sandbox IDs",
    )
    parser.add_argument(
        "--cubecli",
        default=os.environ.get("CUBECLI_BIN", DEFAULT_CUBECLI),
        help="cubecli binary (default: %(default)s; env: CUBECLI_BIN)",
    )
    parser.add_argument(
        "--cgroup-root",
        default=DEFAULT_CGROUP_ROOT,
        help="cgroup mount root (default: %(default)s)",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json",
        action="store_true",
        help="write one pretty-printed JSON document",
    )
    output.add_argument(
        "--ndjson",
        action="store_true",
        help="write one compact JSON object per sandbox",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="timeout in seconds for each cubecli command (default: %(default)s)",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return args


def resolve_cubecli(value: str) -> str:
    candidate = Path(value)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    if "/" not in value:
        found = shutil.which(value)
        if found:
            return found
    if value == DEFAULT_CUBECLI:
        found = shutil.which("cubecli")
        if found:
            return found
    raise SnapshotError(
        f"cubecli is not executable at {value!r}; set CUBECLI_BIN or use --cubecli"
    )


def run_cubecli(cubecli: str, timeout: float, args: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            [cubecli, *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError(
            f"cubecli {' '.join(args)} timed out after {timeout:g}s"
        ) from exc
    except OSError as exc:
        raise SnapshotError(f"cannot run cubecli: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SnapshotError(
            f"cubecli {' '.join(args)} failed ({completed.returncode}): {detail}"
        )
    return completed.stdout


def list_sandbox_ids(cubecli: str, timeout: float) -> List[str]:
    output = run_cubecli(
        cubecli, timeout, ("cubebox", "ls", "--all", "--quiet", "--no-trunc")
    )
    sandbox_ids = [line.strip() for line in output.splitlines() if line.strip()]
    invalid = [value for value in sandbox_ids if any(ch.isspace() for ch in value)]
    if invalid:
        raise SnapshotError(
            "unexpected non-quiet cubecli output while listing sandboxes: "
            + repr(invalid[0])
        )
    return list(dict.fromkeys(sandbox_ids))


def inspect_sandbox(cubecli: str, timeout: float, sandbox_id: str) -> Dict[str, Any]:
    output = run_cubecli(cubecli, timeout, ("cubebox", "inspect", sandbox_id))
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise SnapshotError(
            f"cubecli inspect {sandbox_id!r} returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SnapshotError(
            f"cubecli inspect {sandbox_id!r} did not return a JSON object"
        )
    return value


def first(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def sandbox_state(metadata: Dict[str, Any]) -> str:
    outer = metadata.get("status")
    if not isinstance(outer, dict):
        return "unknown"
    status = first(outer, "Status", "status")
    if not isinstance(status, dict):
        status = outer
    if first(status, "PausingAt", "pausing_at"):
        return "pausing"
    if first(status, "PausedAt", "paused_at"):
        return "paused"
    if first(status, "FinishedAt", "finished_at"):
        return "exited"
    if first(status, "StartedAt", "started_at"):
        return "running"
    if first(status, "CreatedAt", "created_at"):
        return "created"
    return "unknown"


def validate_cgroup_path(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith(POOL_PREFIXES):
        raise SnapshotError(
            f"unexpected CubeSandbox cgroup path {value!r}; refusing to read it"
        )
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise SnapshotError(f"unsafe cgroup path {value!r}")
    return value


def read_text(path: Path, required: bool = True) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if not required:
            return None
        raise SnapshotError(f"required cgroup file does not exist: {path}")
    except PermissionError as exc:
        raise SnapshotError(f"permission denied reading cgroup file: {path}") from exc
    except OSError as exc:
        raise SnapshotError(f"cannot read cgroup file {path}: {exc}") from exc


def read_uint(path: Path, required: bool = True) -> Optional[int]:
    raw = read_text(path, required=required)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise SnapshotError(f"invalid integer in cgroup file {path}: {raw!r}") from exc
    if value < 0:
        raise SnapshotError(f"unexpected negative integer in cgroup file {path}: {raw}")
    return value


def read_key_values(path: Path, required: bool = True) -> Dict[str, int]:
    raw = read_text(path, required=required)
    if raw is None:
        return {}
    result: Dict[str, int] = {}
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            result[fields[0]] = int(fields[1])
        except ValueError as exc:
            raise SnapshotError(f"invalid cgroup key/value line in {path}: {line!r}") from exc
    return result


def require_key(values: Dict[str, int], key: str, source: Path) -> int:
    if key not in values:
        raise SnapshotError(f"required key {key!r} is missing from {source}")
    return values[key]


def safe_group_dir(mountpoint: Path, cgroup_path: str) -> Path:
    result = mountpoint.joinpath(cgroup_path.lstrip("/"))
    try:
        result.relative_to(mountpoint)
    except ValueError as exc:
        raise SnapshotError(f"cgroup path escapes mountpoint: {cgroup_path!r}") from exc
    return result


def mountinfo_v1_mounts() -> Dict[str, List[Path]]:
    result: Dict[str, List[Path]] = {}
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator + 3 >= len(fields) or fields[separator + 1] != "cgroup":
            continue
        mountpoint = Path(
            re.sub(
                r"\\([0-7]{3})",
                lambda match: chr(int(match.group(1), 8)),
                fields[4],
            )
        )
        options = set(fields[separator + 3].split(","))
        for option in options:
            if option in {"cpu", "cpuacct", "memory", "pids", "cpuset", "blkio"}:
                result.setdefault(option, []).append(mountpoint)
    return result


def v1_group_dir(
    root: Path,
    controller: str,
    cgroup_path: str,
    mounts: Dict[str, List[Path]],
    required: bool = True,
) -> Optional[Path]:
    mountpoints: List[Path] = []
    if root == Path(DEFAULT_CGROUP_ROOT):
        mountpoints.extend(mounts.get(controller, []))
    direct_names = (
        controller,
        "cpu,cpuacct" if controller in {"cpu", "cpuacct"} else "",
        "cpuacct,cpu" if controller in {"cpu", "cpuacct"} else "",
    )
    for name in direct_names:
        if name:
            mountpoints.append(root / name)
    try:
        for child in root.iterdir():
            if controller in child.name.split(","):
                mountpoints.append(child)
    except OSError:
        pass
    unique_mounts = list(dict.fromkeys(mountpoints))
    for mountpoint in unique_mounts:
        group = safe_group_dir(mountpoint, cgroup_path)
        if group.is_dir():
            return group
    if not required:
        return None
    checked = ", ".join(str(safe_group_dir(item, cgroup_path)) for item in unique_mounts)
    raise SnapshotError(
        f"cannot find cgroup v1 {controller!r} directory"
        + (f"; checked {checked}" if checked else "")
    )


def read_cpuset(group: Optional[Path], version: int) -> Dict[str, Optional[str]]:
    if group is None:
        return {"cpus": None, "mems": None}
    cpu_names = (
        ("cpuset.cpus.effective", "cpuset.cpus")
        if version == 2
        else ("cpuset.cpus",)
    )
    mem_names = (
        ("cpuset.mems.effective", "cpuset.mems")
        if version == 2
        else ("cpuset.mems",)
    )

    def read_first(names: Iterable[str]) -> Optional[str]:
        for name in names:
            value = read_text(group / name, required=False)
            if value:
                return value
        return None

    return {"cpus": read_first(cpu_names), "mems": read_first(mem_names)}


def read_pids(group: Optional[Path]) -> Dict[str, Optional[int]]:
    if group is None:
        return {"current": None, "limit": None}
    current = read_uint(group / "pids.current", required=False)
    if current is None:
        raw = read_text(group / "cgroup.procs", required=False)
        current = len(raw.splitlines()) if raw else 0
    raw_limit = read_text(group / "pids.max", required=False)
    limit = None if raw_limit in {None, "max"} else int(raw_limit)
    return {"current": current, "limit": limit}


def parse_v2_io(group: Path) -> Dict[str, Optional[int]]:
    raw = read_text(group / "io.stat", required=False)
    if not raw:
        return {"read_bytes_total": None, "write_bytes_total": None}
    read_bytes = 0
    write_bytes = 0
    for line in raw.splitlines():
        for field in line.split()[1:]:
            if "=" not in field:
                continue
            key, value = field.split("=", 1)
            if key == "rbytes":
                read_bytes += int(value)
            elif key == "wbytes":
                write_bytes += int(value)
    return {"read_bytes_total": read_bytes, "write_bytes_total": write_bytes}


def parse_v1_io(group: Optional[Path]) -> Dict[str, Optional[int]]:
    if group is None:
        return {"read_bytes_total": None, "write_bytes_total": None}
    raw = None
    for name in (
        "blkio.throttle.io_service_bytes_recursive",
        "blkio.io_service_bytes_recursive",
    ):
        raw = read_text(group / name, required=False)
        if raw:
            break
    if not raw:
        return {"read_bytes_total": None, "write_bytes_total": None}
    read_bytes = 0
    write_bytes = 0
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[0] == "Total":
            continue
        if fields[1].lower() == "read":
            read_bytes += int(fields[2])
        elif fields[1].lower() == "write":
            write_bytes += int(fields[2])
    return {"read_bytes_total": read_bytes, "write_bytes_total": write_bytes}


def read_v2(root: Path, cgroup_path: str) -> Dict[str, Any]:
    group = safe_group_dir(root, cgroup_path)
    if not group.is_dir():
        raise SnapshotError(f"cgroup v2 directory does not exist: {group}")
    cpu_source = group / "cpu.stat"
    cpu_stat = read_key_values(cpu_source)
    cpu_max = read_text(group / "cpu.max")
    assert cpu_max is not None
    max_fields = cpu_max.split()
    if len(max_fields) != 2:
        raise SnapshotError(f"invalid cgroup v2 cpu.max in {group}: {cpu_max!r}")
    period_us = int(max_fields[1])
    quota_us = None if max_fields[0] == "max" else int(max_fields[0])
    if period_us <= 0:
        raise SnapshotError(f"invalid cgroup v2 CPU period in {group}: {period_us}")

    memory_max = read_text(group / "memory.max")
    assert memory_max is not None
    memory_stat = read_key_values(group / "memory.stat", required=False)
    memory_events = read_key_values(group / "memory.events")
    return {
        "version": 2,
        "cpu": {
            "usage_total_ns": require_key(cpu_stat, "usage_usec", cpu_source) * 1000,
            "user_total_ns": require_key(cpu_stat, "user_usec", cpu_source) * 1000,
            "system_total_ns": require_key(cpu_stat, "system_usec", cpu_source) * 1000,
            "throttled_total_ns": require_key(cpu_stat, "throttled_usec", cpu_source)
            * 1000,
            "periods_total": require_key(cpu_stat, "nr_periods", cpu_source),
            "throttled_periods_total": require_key(
                cpu_stat, "nr_throttled", cpu_source
            ),
            "quota_us": quota_us,
            "period_us": period_us,
            "limit_cores": None if quota_us is None else quota_us / period_us,
        },
        "memory": {
            "current_bytes": read_uint(group / "memory.current"),
            "limit_bytes": None if memory_max == "max" else int(memory_max),
            "cache_bytes": memory_stat.get("file"),
            "failures_total": require_key(memory_events, "max", group / "memory.events"),
            "oom_total": memory_events.get("oom"),
            "oom_kill_total": memory_events.get("oom_kill"),
        },
        "pids": read_pids(group),
        "cpuset": read_cpuset(group, 2),
        "io": parse_v2_io(group),
        "resolved_paths": {"unified": str(group)},
    }


def read_v1_cpu_usage(acct_group: Path) -> Tuple[int, int, int]:
    total = read_uint(acct_group / "cpuacct.usage")
    assert total is not None
    user = read_uint(acct_group / "cpuacct.usage_user", required=False)
    system = read_uint(acct_group / "cpuacct.usage_sys", required=False)
    if user is not None and system is not None:
        return total, user, system

    usage_all = read_text(acct_group / "cpuacct.usage_all", required=False)
    if usage_all:
        all_user = 0
        all_system = 0
        for line in usage_all.splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 3:
                all_user += int(fields[1])
                all_system += int(fields[2])
        return total, all_user, all_system

    stat = read_key_values(acct_group / "cpuacct.stat")
    ticks = os.sysconf("SC_CLK_TCK")
    return (
        total,
        int(stat.get("user", 0) * 1_000_000_000 / ticks),
        int(stat.get("system", 0) * 1_000_000_000 / ticks),
    )


def read_v1(root: Path, cgroup_path: str) -> Dict[str, Any]:
    mounts = mountinfo_v1_mounts() if root == Path(DEFAULT_CGROUP_ROOT) else {}
    cpu_group = v1_group_dir(root, "cpu", cgroup_path, mounts)
    acct_group = v1_group_dir(root, "cpuacct", cgroup_path, mounts)
    memory_group = v1_group_dir(root, "memory", cgroup_path, mounts)
    assert cpu_group is not None and acct_group is not None and memory_group is not None

    total, user, system = read_v1_cpu_usage(acct_group)
    cpu_stat = read_key_values(cpu_group / "cpu.stat")
    quota_raw = read_text(cpu_group / "cpu.cfs_quota_us")
    assert quota_raw is not None
    period_us = read_uint(cpu_group / "cpu.cfs_period_us")
    assert period_us is not None
    if period_us <= 0:
        raise SnapshotError(f"invalid cgroup v1 CPU period in {cpu_group}: {period_us}")
    quota_us = None if quota_raw == "-1" else int(quota_raw)

    memory_stat = read_key_values(memory_group / "memory.stat", required=False)
    memory_limit = read_uint(memory_group / "memory.limit_in_bytes")
    assert memory_limit is not None
    v1_unlimited = (2**63 - 1) & ~(os.sysconf("SC_PAGE_SIZE") - 1)
    if memory_limit == v1_unlimited:
        memory_limit = None

    pids_group = v1_group_dir(
        root, "pids", cgroup_path, mounts, required=False
    )
    cpuset_group = v1_group_dir(
        root, "cpuset", cgroup_path, mounts, required=False
    )
    blkio_group = v1_group_dir(
        root, "blkio", cgroup_path, mounts, required=False
    )
    return {
        "version": 1,
        "cpu": {
            "usage_total_ns": total,
            "user_total_ns": user,
            "system_total_ns": system,
            "throttled_total_ns": require_key(
                cpu_stat, "throttled_time", cpu_group / "cpu.stat"
            ),
            "periods_total": require_key(cpu_stat, "nr_periods", cpu_group / "cpu.stat"),
            "throttled_periods_total": require_key(
                cpu_stat, "nr_throttled", cpu_group / "cpu.stat"
            ),
            "quota_us": quota_us,
            "period_us": period_us,
            "limit_cores": None if quota_us is None else quota_us / period_us,
        },
        "memory": {
            "current_bytes": read_uint(memory_group / "memory.usage_in_bytes"),
            "limit_bytes": memory_limit,
            "cache_bytes": memory_stat.get("total_cache", memory_stat.get("cache")),
            "failures_total": read_uint(memory_group / "memory.failcnt"),
            "oom_total": memory_stat.get("total_oom", memory_stat.get("oom")),
            "oom_kill_total": memory_stat.get(
                "total_oom_kill", memory_stat.get("oom_kill")
            ),
        },
        "pids": read_pids(pids_group or cpu_group),
        "cpuset": read_cpuset(cpuset_group, 1),
        "io": parse_v1_io(blkio_group),
        "resolved_paths": {
            "cpu": str(cpu_group),
            "cpuacct": str(acct_group),
            "memory": str(memory_group),
            "pids": str(pids_group) if pids_group else None,
            "cpuset": str(cpuset_group) if cpuset_group else None,
            "blkio": str(blkio_group) if blkio_group else None,
        },
    }


def normalize_counters(
    metadata: Dict[str, Any],
    cgroup_path: str,
    snapshot: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    raw_values = {
        "cpu_usage_total_ns": snapshot["cpu"]["usage_total_ns"],
        "cpu_user_total_ns": snapshot["cpu"]["user_total_ns"],
        "cpu_system_total_ns": snapshot["cpu"]["system_total_ns"],
        "cpu_throttled_total_ns": snapshot["cpu"]["throttled_total_ns"],
        "cpu_periods_total": snapshot["cpu"]["periods_total"],
        "cpu_throttled_periods_total": snapshot["cpu"][
            "throttled_periods_total"
        ],
        "memory_failures_total": snapshot["memory"]["failures_total"],
    }
    snapshot["cpu"].update(
        {
            "raw_usage_total_ns": raw_values["cpu_usage_total_ns"],
            "raw_user_total_ns": raw_values["cpu_user_total_ns"],
            "raw_system_total_ns": raw_values["cpu_system_total_ns"],
            "raw_throttled_total_ns": raw_values["cpu_throttled_total_ns"],
            "raw_periods_total": raw_values["cpu_periods_total"],
            "raw_throttled_periods_total": raw_values[
                "cpu_throttled_periods_total"
            ],
            "usage_total_ns": None,
            "user_total_ns": None,
            "system_total_ns": None,
            "throttled_total_ns": None,
            "periods_total": None,
            "throttled_periods_total": None,
        }
    )
    snapshot["memory"].update(
        {
            "raw_failures_total": raw_values["memory_failures_total"],
            "failures_total": None,
        }
    )
    baseline = metadata.get("host_metrics_baseline")
    missing_at_assignment = bool(
        metadata.get("host_metrics_baseline_missing_at_assignment")
    )
    baseline_result: Dict[str, Any] = {
        "status": "unavailable",
        "persisted": baseline if isinstance(baseline, dict) else None,
    }
    if missing_at_assignment:
        baseline_result["status"] = "missing_at_assignment"
        warnings.append(
            "The cgroup assignment baseline was not captured; cumulative counters "
            "cannot be attributed to this sandbox."
        )
        return baseline_result, warnings
    if not isinstance(baseline, dict):
        baseline_result["status"] = "not_initialized"
        warnings.append(
            "No persisted counter baseline exists yet; Cubelet will initialize it "
            "from a sample before exporting normalized counters."
        )
        return baseline_result, warnings
    baseline_path = baseline.get("cgroup_path")
    if baseline_path != cgroup_path:
        baseline_result["status"] = "path_mismatch"
        warnings.append(
            f"Persisted baseline path {baseline_path!r} does not match {cgroup_path!r}."
        )
        return baseline_result, warnings

    normalized: Dict[str, int] = {}
    for json_key, baseline_key in BASELINE_FIELDS.items():
        current = raw_values[json_key]
        base = int(baseline.get(json_key, baseline.get(baseline_key, 0)))
        if current < base:
            baseline_result["status"] = "counter_regressed"
            warnings.append(
                f"{json_key} regressed below its persisted baseline ({current} < {base}); "
                "Cubelet rejects the sample too."
            )
            return baseline_result, warnings
        normalized[json_key] = current - base

    baseline_result["status"] = "applied"
    baseline_result["normalized"] = normalized
    snapshot["cpu"].update(
        {
            "usage_total_ns": normalized["cpu_usage_total_ns"],
            "user_total_ns": normalized["cpu_user_total_ns"],
            "system_total_ns": normalized["cpu_system_total_ns"],
            "throttled_total_ns": normalized["cpu_throttled_total_ns"],
            "periods_total": normalized["cpu_periods_total"],
            "throttled_periods_total": normalized[
                "cpu_throttled_periods_total"
            ],
        }
    )
    snapshot["memory"].update(
        {
            "failures_total": normalized["memory_failures_total"],
        }
    )
    return baseline_result, warnings


def collect_one(
    cubecli: str,
    timeout: float,
    root: Path,
    requested_id: str,
) -> Dict[str, Any]:
    metadata = inspect_sandbox(cubecli, timeout, requested_id)
    sandbox_id = first(metadata, "ID", "id") or requested_id
    cgroup_path = validate_cgroup_path(
        first(metadata, "CGroupPath", "cgroup_path")
    )
    is_v2 = (root / "cgroup.controllers").is_file()
    snapshot = read_v2(root, cgroup_path) if is_v2 else read_v1(root, cgroup_path)
    baseline, warnings = normalize_counters(metadata, cgroup_path, snapshot)
    return {
        "sandbox_id": sandbox_id,
        "state": sandbox_state(metadata),
        "sampled_at": dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "metadata": {
            "name": first(metadata, "name", "Name"),
            "namespace": first(metadata, "namespace", "Namespace"),
            "app_id": first(metadata, "app_id", "AppID"),
            "ip": first(metadata, "IP", "ip"),
            "numa_node": first(metadata, "numa_node", "NumaNode"),
            "instance_type": first(metadata, "instance_type", "InstanceType"),
        },
        "cgroup_path": cgroup_path,
        "cgroup": snapshot,
        "counter_baseline": baseline,
        "warnings": warnings,
    }


def human_bytes(value: Optional[int], missing: str = "unknown") -> str:
    if value is None:
        return missing
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{value} B"


def human_seconds(nanoseconds: Optional[int]) -> str:
    if nanoseconds is None:
        return "unknown"
    return f"{nanoseconds / 1_000_000_000:.3f}s"


def print_human(record: Dict[str, Any]) -> None:
    if "error" in record:
        print(f"sandbox_id  {record['sandbox_id']}")
        print(f"error       {record['error']}")
        print()
        return
    cgroup = record["cgroup"]
    cpu = cgroup["cpu"]
    memory = cgroup["memory"]
    io = cgroup["io"]
    raw_usage = cpu.get("raw_usage_total_ns")
    baseline = record["counter_baseline"]["status"]
    limit = (
        "unlimited"
        if cpu["limit_cores"] is None
        else f"{cpu['limit_cores']:.3f} cores"
    )
    print(f"sandbox_id  {record['sandbox_id']}")
    print(f"state       {record['state']}")
    print(f"cgroup      v{cgroup['version']} {record['cgroup_path']}")
    print(
        "cpu         "
        f"usage={human_seconds(cpu['usage_total_ns'])} "
        + (
            f"raw={human_seconds(raw_usage)} "
            if raw_usage is not None
            else ""
        )
        + f"limit={limit} throttled={human_seconds(cpu['throttled_total_ns'])}"
    )
    print(
        "memory      "
        f"current={human_bytes(memory['current_bytes'])} "
        f"limit={human_bytes(memory['limit_bytes'], 'unlimited')} "
        f"cache={human_bytes(memory['cache_bytes'])} "
        f"failures={memory['failures_total'] if memory['failures_total'] is not None else 'unknown'}"
    )
    print(
        "processes   "
        f"{cgroup['pids']['current']} "
        f"cpus={cgroup['cpuset']['cpus'] or 'unknown'} "
        f"numa_mems={cgroup['cpuset']['mems'] or 'unknown'}"
    )
    print(
        "io          "
        f"read={human_bytes(io['read_bytes_total'])} "
        f"write={human_bytes(io['write_bytes_total'])}"
    )
    print(f"baseline    {baseline}")
    for warning in record["warnings"]:
        print(f"warning     {warning}")
    print()


def main() -> int:
    args = parse_args()
    try:
        cubecli = resolve_cubecli(args.cubecli)
        sandbox_ids = (
            list(dict.fromkeys(args.sandbox_ids))
            if args.sandbox_ids
            else list_sandbox_ids(cubecli, args.timeout)
        )
    except SnapshotError as exc:
        print(f"sandbox-cgroup-snapshot: {exc}", file=sys.stderr)
        return 2

    root = Path(args.cgroup_root)
    records: List[Dict[str, Any]] = []
    failed = False
    for sandbox_id in sandbox_ids:
        try:
            records.append(
                collect_one(cubecli, args.timeout, root, sandbox_id)
            )
        except (SnapshotError, ValueError, OSError) as exc:
            failed = True
            records.append({"sandbox_id": sandbox_id, "error": str(exc)})

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "count": len(records),
                    "sandboxes": records,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    elif args.ndjson:
        for record in records:
            print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    elif not records:
        print("No local CubeBoxes found.")
    else:
        for record in records:
            print_human(record)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
