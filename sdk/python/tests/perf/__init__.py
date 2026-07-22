# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Standalone performance benchmark suite for the Python SDK.

Self-contained package — all dependencies (config/env/runner/report) are
included within the `perf/` directory.  No external `e2e/` import needed.

Run it via the CLI entry point (from the ``tests/`` directory)::

    python3 -m perf                                   # local backend (default)
    CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf  # remote backend

Modules:
    benchmarks  - 13 benchmark scenarios + run_all()
    report_html - self-contained HTML report with Chart.js line charts
    report      - Markdown + JSON report generation
    config      - configuration resolution & runtime tunables
    env         - environment info collection (hardware + component versions)
    runner      - PerfResult, PerfSample, measure helpers
"""

from __future__ import annotations

import os
import sys

# tests/perf/__init__.py -> tests/perf -> tests -> sdk/python
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_TESTS_DIR = os.path.abspath(os.path.join(_PKG_DIR, ".."))
_SDK_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _SDK_ROOT not in sys.path:
    sys.path.insert(0, _SDK_ROOT)


# Data-flow (connection) variables — the SDK ``Config`` knobs that decide which
# backend the suite talks to. These are the only settings a first-time user must
# provide; ``_ensure_dotenv`` scaffolds them and ``_persist_dotenv`` writes back
# whatever a run actually used, so the 2nd/3rd invocation just needs
# ``python3 -m perf`` (no re-exporting). Everything else stays in ``.env.example``.
_DATAFLOW_ENV_KEYS = (
    "CUBE_API_URL",
    "CUBE_API_KEY",
    "CUBE_TEMPLATE_ID",
    "CUBE_PROXY_NODE_IP",
    "CUBE_PROXY_PORT_HTTP",
    "CUBE_SANDBOX_DOMAIN",
)

# Run tunables (concurrency ladders, rounds, density count, ...) — no secrets,
# so they are safe to write back the same way. Whatever value a run actually
# used (real env var, or already in .env) gets persisted, so once you dial the
# concurrency ladder down to dodge a CubeMaster "no more resource" error on a
# small node, later runs keep using the smaller ladder without re-exporting.
_TUNABLE_ENV_KEYS = (
    "CUBE_PERF_ROUNDS",
    "CUBE_PERF_CONCURRENCY",
    "CUBE_CREATE_CONCURRENCY",
    "CUBE_PERF_WARMUP",
    "CUBE_PERF_SETTLE",
    "CUBE_DIRTY_SWEEP",
    "CUBE_DENSITY_COUNT",
    "CUBE_PERF_CLEANUP",
    "CUBE_CLEANUP_CMD",
    # Scenario toggles — persisted so subsequent runs reuse the same set
    # without re-exporting CUBE_RUN_IVSHMEM=1 / CUBE_SKIP_DENSITY=1 etc.
    "CUBE_RUN_IVSHMEM",
    "CUBE_RUN_VOLUME",
    "CUBE_SKIP_DENSITY",
    "CUBE_SKIP_SNAPSHOT_DIRTY",
    "CUBE_IVSHMEM_TEMPLATE_ID",
    "CUBE_IVSHMEM_ITERATIONS",
    "CUBE_EXTERNAL_SCRIPTS",
)

_DOTENV_HEADER = (
    "# CubeSandbox perf suite — auto-generated .env (data-flow + tunables + scenarios).\n"
    "# First run: all keys are commented placeholders; after a successful run the\n"
    "# values actually used (concurrency ladders, rounds, template id, …) are\n"
    "# written back here, so later runs just need: python3 -m perf\n"
    "# Copy .env.example for even more detail (report layout, customisation, …).\n"
    "\n"
)


def _dotenv_candidates() -> "list[str]":
    """Return the ``.env`` search locations (nearest first)."""
    explicit = os.environ.get("CUBE_DOTENV")
    if explicit:
        return [explicit]
    return [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(_PKG_DIR, ".env"),
        os.path.join(_TESTS_DIR, ".env"),
        os.path.join(_SDK_ROOT, ".env"),
    ]


def _dotenv_path() -> str:
    """The ``.env`` file to read/write: the first existing candidate, else the
    default ``tests/.env`` (so ``cd tests && python3 -m perf`` just works)."""
    explicit = os.environ.get("CUBE_DOTENV")
    if explicit:
        return explicit
    for p in _dotenv_candidates():
        if os.path.isfile(p):
            return p
    return os.path.join(_TESTS_DIR, ".env")


def _ensure_dotenv() -> None:
    """Scaffold a ``.env`` on first run with the most useful knobs.

    Writes data-flow keys, run tunables, scenario toggles, and external-script
    path — all as commented‑out placeholders (except keys already set in the
    environment, which are written uncommented).  Existing ``.env`` files are
    never touched.
    """
    if os.environ.get("CUBE_DOTENV"):
        return
    if any(os.path.isfile(p) for p in _dotenv_candidates()):
        return

    def _banner(title: str) -> list[str]:
        hr = "# " + "=" * 73 + "\n"
        return ["\n\n", hr, f"# {title}\n", hr]

    out: list[str] = [_DOTENV_HEADER]

    # -- Data-flow (connection) --
    out.extend(_banner("目标环境与鉴权"))
    for key in _DATAFLOW_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            out.append(f"{key}={value}\n")
        elif key == "CUBE_API_URL":
            out.append(f"# {key}=   # default http://127.0.0.1:3000 (local); set for remote\n")
        elif key == "CUBE_TEMPLATE_ID":
            out.append(f"# {key}=   # leave empty to auto-discover a READY template\n")
        else:
            out.append(f"# {key}=\n")

    # -- Scenario toggles --
    out.extend(_banner("跑哪些场景（开启 / 关闭）"))
    _scenario_placeholders = {
        "CUBE_RUN_IVSHMEM": "设为 1 启用 ivshmem 共享内存场景（需在节点 host 上运行）",
        "CUBE_RUN_VOLUME": "设为 1 启用 Volume 相关场景",
        "CUBE_SKIP_DENSITY": "设为 1 跳过部署密度测试",
        "CUBE_SKIP_SNAPSHOT_DIRTY": "设为 1 跳过「快照耗时 vs 脏页规模」测试",
        "CUBE_IVSHMEM_TEMPLATE_ID": "ivshmem 专用模板（留空回落 CUBE_TEMPLATE_ID）",
        "CUBE_IVSHMEM_ITERATIONS": "ivshmem mmap 读写迭代次数（默认 10000）",
    }
    for key, hint in _scenario_placeholders.items():
        value = os.environ.get(key)
        out.append(f"{key}={value}\n" if value else f"# {key}=   # {hint}\n")

    # -- Run tunables --
    out.extend(_banner("运行参数"))
    _tunable_placeholders = {
        "CUBE_PERF_ROUNDS": "每个场景压测轮数（默认 10）",
        "CUBE_PERF_CONCURRENCY": "轻量场景并发梯度（snapshot-create/rollback/pause-resume，默认 1,5,10）",
        "CUBE_CREATE_CONCURRENCY": "重量场景并发梯度（template-create/from-snapshot/clone，默认 1,10,20,50）",
        "CUBE_PERF_WARMUP": "计时前丢弃的预热轮数（默认 1）",
        "CUBE_PERF_SETTLE": "并发档位之间的静默秒数（默认 0）",
        "CUBE_DENSITY_COUNT": "部署密度测试最大沙箱数（默认 100）",
        "CUBE_PERF_CLEANUP": "轮次间清理残留 micro-VM：设为 0 关闭（默认开启）",
    }
    for key, hint in _tunable_placeholders.items():
        value = os.environ.get(key)
        out.append(f"{key}={value}\n" if value else f"# {key}=   # {hint}\n")

    # -- External scripts --
    out.extend(_banner("外部压测脚本（逗号分隔的 .py 路径，-c -n 约定）"))
    ext = os.environ.get("CUBE_EXTERNAL_SCRIPTS", "")
    out.append(
        f"CUBE_EXTERNAL_SCRIPTS={ext}\n"
        if ext
        else "# CUBE_EXTERNAL_SCRIPTS=/path/to/bench_xxx.py\n"
    )

    # -- Output --
    out.extend(_banner("输出"))
    html = os.environ.get("CUBE_HTML_OUTPUT", "")
    out.append(
        f"CUBE_HTML_OUTPUT={html}\n"
        if html
        else "# CUBE_HTML_OUTPUT=perf_report.html\n"
    )

    target = os.path.join(_TESTS_DIR, ".env")
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.writelines(out)
    except OSError:
        return
    sys.stderr.write(
        f"[perf] created {target}\n"
        "  set CUBE_API_URL for a remote backend, then run python3 -m perf\n"
    )


def _persist_dotenv(values: "dict[str, str]") -> None:
    """Write back the data-flow/tunable values a run actually used.

    Only non-empty keys from :data:`_DATAFLOW_ENV_KEYS` or
    :data:`_TUNABLE_ENV_KEYS` are persisted. Matching lines in the existing
    ``.env`` (commented or not) are replaced in place; missing keys are
    appended. All other lines (comments, scenario knobs the user added) are
    preserved verbatim. This lets the 2nd/3rd run reuse the values — including
    a template id that was auto-discovered, or a concurrency ladder trimmed
    down to dodge a CubeMaster "no more resource" error. Failures are silent.
    """
    allowed = _DATAFLOW_ENV_KEYS + _TUNABLE_ENV_KEYS
    values = {k: v for k, v in values.items() if k in allowed and v}
    if not values:
        return
    path = _dotenv_path()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        lines = [_DOTENV_HEADER]

    pending = dict(values)
    out: list[str] = []
    for line in lines:
        key = line.lstrip("# ").partition("=")[0].strip()
        if key in pending:
            out.append(f"{key}={pending.pop(key)}\n")
        else:
            out.append(line if line.endswith("\n") else line + "\n")
    for key, value in pending.items():  # keys not yet present in the file
        out.append(f"{key}={value}\n")

    if out == lines:  # nothing changed — avoid a pointless rewrite
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(out)
    except OSError:
        return


def _load_dotenv() -> None:
    """Populate os.environ from a ``.env`` file (zero-dependency).

    This runs before any perf submodule reads its env-driven tunables, so a
    ``.env`` file lets you configure the whole suite (API creds, concurrency,
    report customization) without exporting variables by hand. Rules:

    - Real environment variables always win; ``.env`` only fills in the gaps,
      so ``CUBE_API_KEY=... python -m perf`` still overrides the file.
    - The first ``.env`` found across these locations is used (nearest first):
      current working dir, ``tests/perf/``, ``tests/``, ``sdk/python/``.
    - Lines may be ``KEY=VALUE`` or ``export KEY=VALUE``; blank lines and
      ``#`` comments are ignored; surrounding single/double quotes are stripped.
    - ``CUBE_DOTENV`` may point at an explicit file to load instead.
    """
    path = next((p for p in _dotenv_candidates() if p and os.path.isfile(p)), None)
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # Real env vars take precedence over the .env file.
        if key and key not in os.environ:
            os.environ[key] = value


_ensure_dotenv()
_load_dotenv()
