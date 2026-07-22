# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""TOML-driven configuration for the perf HTML report.

Layered configuration (highest → lowest precedence)::

    CLI args  >  environment variables  >  report.toml  >  built-in defaults

The TOML layer is intentionally scoped to the "minimum viable" set that
covers ~90% of tweaks people actually make when generating a run-specific
report: title / subtitle, environment & Cube field lists, and the env-card
column count. Anything more elaborate should still be done via a code change
so the report stays predictable.

Search order for the TOML file when ``CUBE_REPORT_CONFIG`` is unset:

    1. ``./report.toml`` (current working directory)
    2. ``sdk/python/tests/perf/report.toml`` (package dir)
    3. ``sdk/python/tests/report.toml``
    4. ``sdk/python/report.toml``

Missing file → empty config → identical behaviour to before. Missing keys
inside the file → each subsystem keeps its old default.

Zero-dependency: uses the stdlib ``tomllib`` (Python 3.11+). On older
Pythons TOML support is silently disabled (empty config).
"""

from __future__ import annotations

import os
from typing import Any

try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — Python < 3.11
    tomllib = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# File discovery + loading
# ---------------------------------------------------------------------------

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_TESTS_DIR = os.path.abspath(os.path.join(_PKG_DIR, ".."))
_SDK_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))


def _candidate_paths() -> list[str]:
    """Return the ordered list of files we will look at."""
    explicit = os.environ.get("CUBE_REPORT_CONFIG")
    if explicit:
        return [explicit]
    return [
        os.path.join(os.getcwd(), "report.toml"),
        os.path.join(_PKG_DIR, "report.toml"),
        os.path.join(_TESTS_DIR, "report.toml"),
        os.path.join(_SDK_ROOT, "report.toml"),
    ]


_cache: dict[str, Any] | None = None
_cache_source: str = ""


def load(force: bool = False) -> dict[str, Any]:
    """Return the loaded config dict (or ``{}``). Cached across calls."""
    global _cache, _cache_source
    if _cache is not None and not force:
        return _cache

    if tomllib is None:
        _cache = {}
        _cache_source = "<tomllib unavailable>"
        return _cache

    for path in _candidate_paths():
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                _cache = tomllib.load(f)
            _cache_source = path
            return _cache
        except (OSError, tomllib.TOMLDecodeError):
            continue

    _cache = {}
    _cache_source = ""
    return _cache


def source() -> str:
    """Return the path we actually loaded config from, or '' if none/unavailable."""
    if _cache is None:
        load()
    return _cache_source


# ---------------------------------------------------------------------------
# Typed getters
# ---------------------------------------------------------------------------

def get_str(key: str, default: str = "") -> str:
    """Get ``[section.]key`` as a string. Dotted path allowed."""
    v = _dotted(load(), key)
    return str(v) if v is not None and v != "" else default


def get_int(key: str, default: int) -> int:
    v = _dotted(load(), key)
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def get_list(key: str, default: list[Any] | None = None) -> list[Any]:
    """Get a list-typed key (returns ``default`` or ``[]``)."""
    v = _dotted(load(), key)
    if isinstance(v, list):
        return v
    return list(default) if default is not None else []


def _dotted(d: dict[str, Any], key: str) -> Any:
    """Traverse ``a.b.c`` in nested dict; return None if any hop is missing."""
    cur: Any = d
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


# ---------------------------------------------------------------------------
# High-level resolvers used by report_html.py
# ---------------------------------------------------------------------------
#
# Each resolver follows the layered precedence (env → TOML → default) so
# adding TOML support is strictly additive: existing env-var users keep
# working unchanged.


def parse_fields_from_spec(raw: Any, defaults: list[tuple[str, str]]) -> list[list[str]]:
    """Accept a TOML field spec — either a list of ``"key"`` / ``"key:label"``
    strings, a list of ``[key, label]`` two-element lists, or a comma-separated
    string — and return the same normalised ``[[key, label], ...]`` shape used
    by the HTML template.
    """
    label_map = {k: lbl for k, lbl in defaults}

    def _pair(entry: Any) -> list[str] | None:
        if isinstance(entry, (list, tuple)) and len(entry) >= 1:
            k = str(entry[0]).strip()
            lbl = str(entry[1]).strip() if len(entry) > 1 and entry[1] else label_map.get(k, k)
            return [k, lbl] if k else None
        if isinstance(entry, str):
            s = entry.strip()
            if not s:
                return None
            if ":" in s:
                k, _, lbl = s.partition(":")
                k, lbl = k.strip(), lbl.strip()
            else:
                k, lbl = s, label_map.get(s, s)
            return [k, lbl] if k else None
        return None

    if isinstance(raw, str):
        # Comma-separated string — mirrors the env-var spec.
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        out = [_pair(p) for p in parts]
    elif isinstance(raw, list):
        out = [_pair(e) for e in raw]
    else:
        return []

    return [p for p in out if p]


def resolve_env_columns(default: int = 2) -> int:
    """Number of columns for the env-info grid. 1 = stacked, 2 = side-by-side."""
    env_val = os.environ.get("CUBE_REPORT_ENV_COLUMNS")
    if env_val:
        try:
            return max(1, int(env_val))
        except ValueError:
            pass
    n = get_int("layout.env_columns", default)
    return max(1, n)


def resolve_title(cli_title: str | None, default: str) -> str:
    """Title precedence: CLI > env > TOML > default. Empty strings are ignored."""
    if cli_title:
        return cli_title
    env_title = os.environ.get("CUBE_REPORT_TITLE", "").strip()
    if env_title:
        return env_title
    toml_title = get_str("title")
    return toml_title or default


def resolve_subtitle(default: str = "") -> str:
    env_v = os.environ.get("CUBE_REPORT_SUBTITLE", "").strip()
    if env_v:
        return env_v
    return get_str("subtitle", default)


def resolve_fields_toml(
    section: str, defaults: list[tuple[str, str]]
) -> list[list[str]] | None:
    """Return the ``[<section>] fields`` list (+ ``extra``) from TOML.

    Returns None when the TOML file has no such section, so callers can
    fall back to the existing env-var / defaults path unchanged.
    ``fields`` fully replaces the defaults; ``extra`` appends.
    """
    section_cfg = _dotted(load(), section)
    if not isinstance(section_cfg, dict):
        return None

    fields_spec = section_cfg.get("fields")
    extra_spec = section_cfg.get("extra")

    if fields_spec:
        base = parse_fields_from_spec(fields_spec, defaults)
    else:
        base = [[k, lbl] for k, lbl in defaults]

    if extra_spec:
        base += parse_fields_from_spec(extra_spec, defaults)

    return base
