# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from framework.assertions import assert_command_ok
from framework.capabilities import FILESYSTEM

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.sdk_compat,
    pytest.mark.filesystem,
    pytest.mark.p0,
    pytest.mark.requires_capability(FILESYSTEM),
]


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

def test_remove_file(sdk_sandbox):
    path = "/tmp/sdk-compat-remove.txt"
    sdk_sandbox.write_file(path, "data")
    assert sdk_sandbox.file_exists(path)

    sdk_sandbox.remove_file(path)

    assert not sdk_sandbox.file_exists(path)


def test_remove_missing_file_is_silent(sdk_sandbox):
    sdk_sandbox.remove_file("/tmp/sdk-compat-remove-missing.txt")


def test_remove_directory(sdk_sandbox, sdk_e2e_config):
    directory = "/tmp/sdk-compat-remove-dir"
    result = sdk_sandbox.run_command(
        f"mkdir -p {directory}", timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)
    assert sdk_sandbox.file_exists(directory)

    sdk_sandbox.remove_file(directory)

    assert not sdk_sandbox.file_exists(directory)


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

def test_list_empty_directory(sdk_sandbox, sdk_e2e_config):
    path = "/tmp/sdk-compat-list-empty"
    result = sdk_sandbox.run_command(
        f"mkdir -p {path}", timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)

    entries = sdk_sandbox.list_dir(path)
    assert isinstance(entries, list)
    real_entries = [e for e in entries if e not in (".", "..")]
    assert real_entries == []


def test_list_nonempty_directory(sdk_sandbox, sdk_e2e_config):
    path = "/tmp/sdk-compat-list-nonempty"
    result = sdk_sandbox.run_command(
        f"mkdir -p {path} && touch {path}/a.txt {path}/b.txt",
        timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)

    entries = sdk_sandbox.list_dir(path)
    real_entries = [e for e in entries if e not in (".", "..")]
    assert sorted(real_entries) == ["a.txt", "b.txt"]


# ---------------------------------------------------------------------------
# make_dir
# ---------------------------------------------------------------------------

def test_make_dir(sdk_sandbox):
    path = "/tmp/sdk-compat-makedir"
    sdk_sandbox.make_dir(path)
    assert sdk_sandbox.file_exists(path)


def test_make_dir_nested(sdk_sandbox, sdk_e2e_config):
    parent = "/tmp/sdk-compat-makedir-nested"
    result = sdk_sandbox.run_command(
        f"mkdir -p {parent}", timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)

    leaf = f"{parent}/sub"
    sdk_sandbox.make_dir(leaf)
    assert sdk_sandbox.file_exists(leaf)


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------

def test_rename_file(sdk_sandbox):
    old_path = "/tmp/sdk-compat-rename-old.txt"
    new_path = "/tmp/sdk-compat-rename-new.txt"
    sdk_sandbox.write_file(old_path, "data")

    sdk_sandbox.rename_file(old_path, new_path)

    assert not sdk_sandbox.file_exists(old_path)
    assert sdk_sandbox.file_exists(new_path)
    assert sdk_sandbox.read_file(new_path) == "data"


def test_rename_directory(sdk_sandbox, sdk_e2e_config):
    old_dir = "/tmp/sdk-compat-rename-old-dir"
    new_dir = "/tmp/sdk-compat-rename-new-dir"
    file_inside = "inner.txt"
    result = sdk_sandbox.run_command(
        f"mkdir -p {old_dir} && touch {old_dir}/{file_inside}",
        timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)

    sdk_sandbox.rename_file(old_dir, new_dir)

    assert not sdk_sandbox.file_exists(old_dir)
    assert sdk_sandbox.file_exists(new_dir)
    assert sdk_sandbox.file_exists(f"{new_dir}/{file_inside}")


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------

def test_exists_for_existing_file(sdk_sandbox):
    path = "/tmp/sdk-compat-exists-true.txt"
    sdk_sandbox.write_file(path, "x")
    assert sdk_sandbox.file_exists(path)


def test_exists_for_missing_file(sdk_sandbox):
    assert not sdk_sandbox.file_exists("/tmp/sdk-compat-exists-missing.txt")


def test_exists_for_root_directory(sdk_sandbox):
    assert sdk_sandbox.file_exists("/")


# ---------------------------------------------------------------------------
# user parameter — backward-compatible default (user omitted)
# ---------------------------------------------------------------------------

def test_operations_without_user_parameter_still_work(sdk_sandbox, sdk_e2e_config):
    """Calling filesystem methods without user= uses the default (root)."""
    path = "/tmp/sdk-compat-no-user.txt"
    rename_path = "/tmp/sdk-compat-no-user-renamed.txt"

    # These should all succeed without an explicit user parameter.
    sdk_sandbox.write_file(path, "hello")
    assert sdk_sandbox.read_file(path) == "hello"
    assert sdk_sandbox.file_exists(path)

    sdk_sandbox.rename_file(path, rename_path)
    assert sdk_sandbox.file_exists(rename_path)

    sdk_sandbox.remove_file(rename_path)
    assert not sdk_sandbox.file_exists(rename_path)


# ---------------------------------------------------------------------------
# user parameter — explicit non‑root user (write + read roundtrip)
# ---------------------------------------------------------------------------

def test_write_and_read_file_as_explicit_user(sdk_sandbox):
    path = "/tmp/sdk-compat-user-file.txt"
    content = "owned-by-alice"
    sdk_sandbox.write_file(path, content, user="alice")

    assert sdk_sandbox.read_file(path, user="alice") == content


def test_remove_file_as_explicit_user(sdk_sandbox):
    path = "/tmp/sdk-compat-user-remove.txt"
    sdk_sandbox.write_file(path, "tmp", user="alice")
    assert sdk_sandbox.file_exists(path, user="alice")

    sdk_sandbox.remove_file(path, user="alice")

    assert not sdk_sandbox.file_exists(path, user="alice")


def test_rename_file_as_explicit_user(sdk_sandbox):
    old_path = "/tmp/sdk-compat-user-rename-old.txt"
    new_path = "/tmp/sdk-compat-user-rename-new.txt"
    sdk_sandbox.write_file(old_path, "data", user="alice")

    sdk_sandbox.rename_file(old_path, new_path, user="alice")

    assert sdk_sandbox.file_exists(new_path, user="alice")
    assert sdk_sandbox.read_file(new_path, user="alice") == "data"
    assert not sdk_sandbox.file_exists(old_path, user="alice")


def test_list_dir_as_explicit_user(sdk_sandbox, sdk_e2e_config):
    path = "/tmp/sdk-compat-user-list"
    sdk_sandbox.make_dir(path, user="alice")
    sdk_sandbox.write_file(f"{path}/f.txt", "x", user="alice")

    entries = sdk_sandbox.list_dir(path, user="alice")
    real_entries = [e for e in entries if e not in (".", "..")]
    assert "f.txt" in real_entries


def test_make_dir_and_exists_as_explicit_user(sdk_sandbox):
    path = "/tmp/sdk-compat-user-makedir"
    sdk_sandbox.make_dir(path, user="alice")
    assert sdk_sandbox.file_exists(path, user="alice")
