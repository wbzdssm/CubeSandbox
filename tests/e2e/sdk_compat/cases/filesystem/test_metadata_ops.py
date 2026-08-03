# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""E2E coverage for filesystem metadata RPCs added in cubesandbox SDK:

- list / stat / exists / remove / rename / make_dir
- explicit ``user`` parameter on every call
"""

from __future__ import annotations

import pytest

from framework.assertions import assert_command_ok
from framework.capabilities import FILESYSTEM

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.sdk_compat,
    pytest.mark.filesystem,
    pytest.mark.p1,
    pytest.mark.requires_capability(FILESYSTEM),
]


def _require_cubesandbox(sdk_backend: str) -> None:
    if sdk_backend != "cubesandbox":
        pytest.skip(
            f"filesystem metadata ops require cubesandbox backend, got {sdk_backend!r}"
        )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_directory_entries(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    dir_path = "/tmp/sdk-compat-list-dir"
    sdk_sandbox.run_command(
        f"mkdir -p {dir_path} && touch {dir_path}/a.txt {dir_path}/b.txt",
        timeout=sdk_e2e_config.command_timeout,
    )

    entries = sdk_sandbox.raw_sandbox.files.list(dir_path)

    names = {e.get("name", "") for e in entries}
    assert "a.txt" in names
    assert "b.txt" in names


def test_list_with_explicit_user(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    dir_path = "/tmp/sdk-compat-list-user"
    sdk_sandbox.run_command(
        f"mkdir -p {dir_path} && touch {dir_path}/f1",
        timeout=sdk_e2e_config.command_timeout,
    )

    # user="root" must be accepted and produce the same result
    entries = sdk_sandbox.raw_sandbox.files.list(dir_path, user="root")

    assert isinstance(entries, list)
    assert any(e.get("name") == "f1" for e in entries)


# ---------------------------------------------------------------------------
# stat
# ---------------------------------------------------------------------------


def test_stat_returns_file_metadata(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    path = "/tmp/sdk-compat-stat.txt"
    sdk_sandbox.write_file(path, "hello")

    entry = sdk_sandbox.raw_sandbox.files.stat(path)

    assert isinstance(entry, dict)
    # envd returns at minimum a "type" field (e.g. "file" / "directory")
    assert "type" in entry or "name" in entry


def test_stat_with_explicit_user(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    path = "/tmp/sdk-compat-stat-user.txt"
    sdk_sandbox.write_file(path, "user-stat")

    entry = sdk_sandbox.raw_sandbox.files.stat(path, user="root")

    assert isinstance(entry, dict)


def test_stat_missing_path_raises(
    sdk_sandbox, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    with pytest.raises(Exception, match=r"(?i)not.found|404|no such"):
        sdk_sandbox.raw_sandbox.files.stat("/tmp/sdk-compat-stat-missing-xyz")


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


def test_exists_reports_presence_correctly(
    sdk_sandbox, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    path = "/tmp/sdk-compat-exists.txt"
    sdk_sandbox.write_file(path, "hi")

    files = sdk_sandbox.raw_sandbox.files

    assert files.exists(path) is True
    assert files.exists("/tmp/sdk-compat-nonexistent-xyz") is False


def test_exists_with_explicit_user(
    sdk_sandbox, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    path = "/tmp/sdk-compat-exists-user.txt"
    sdk_sandbox.write_file(path, "user-exists")

    files = sdk_sandbox.raw_sandbox.files

    assert files.exists(path, user="root") is True
    assert files.exists("/tmp/sdk-compat-user-missing", user="root") is False


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_deletes_file(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    path = "/tmp/sdk-compat-remove.txt"
    sdk_sandbox.write_file(path, "delete me")

    sdk_sandbox.raw_sandbox.files.remove(path)

    result = sdk_sandbox.run_command(
        f"test -f {path} && echo exists || echo gone",
        timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)
    assert "gone" in result.stdout


def test_remove_with_explicit_user(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    path = "/tmp/sdk-compat-remove-user.txt"
    sdk_sandbox.write_file(path, "user-remove")

    sdk_sandbox.raw_sandbox.files.remove(path, user="root")

    result = sdk_sandbox.run_command(
        f"test -f {path} && echo exists || echo gone",
        timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)
    assert "gone" in result.stdout


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


def test_rename_moves_file(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    old_path = "/tmp/sdk-compat-rename-old.txt"
    new_path = "/tmp/sdk-compat-rename-new.txt"
    sdk_sandbox.write_file(old_path, "before rename")

    sdk_sandbox.raw_sandbox.files.rename(old_path, new_path)

    assert sdk_sandbox.read_file(new_path) == "before rename"
    result = sdk_sandbox.run_command(
        f"test -f {old_path} && echo exists || echo gone",
        timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)
    assert "gone" in result.stdout


def test_rename_with_explicit_user(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    old_path = "/tmp/sdk-compat-rename-user-old.txt"
    new_path = "/tmp/sdk-compat-rename-user-new.txt"
    sdk_sandbox.write_file(old_path, "user-rename")

    _ = sdk_sandbox.raw_sandbox.files.rename(old_path, new_path, user="root")

    assert sdk_sandbox.read_file(new_path) == "user-rename"


# ---------------------------------------------------------------------------
# make_dir
# ---------------------------------------------------------------------------


def test_make_dir_creates_directory(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    dir_path = "/tmp/sdk-compat-makedir"

    sdk_sandbox.raw_sandbox.files.make_dir(dir_path)

    result = sdk_sandbox.run_command(
        f"test -d {dir_path} && echo is_dir || echo not_dir",
        timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)
    assert "is_dir" in result.stdout


def test_make_dir_with_explicit_user(
    sdk_sandbox, sdk_e2e_config, sdk_backend,
):
    _require_cubesandbox(sdk_backend)
    dir_path = "/tmp/sdk-compat-makedir-user"

    entry = sdk_sandbox.raw_sandbox.files.make_dir(dir_path, user="root")

    assert isinstance(entry, dict)
    result = sdk_sandbox.run_command(
        f"test -d {dir_path} && echo is_dir || echo not_dir",
        timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)
    assert "is_dir" in result.stdout
