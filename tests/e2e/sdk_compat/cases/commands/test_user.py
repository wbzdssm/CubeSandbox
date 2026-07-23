# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from framework.assertions import assert_command_ok
from framework.capabilities import COMMANDS

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.sdk_compat,
    pytest.mark.commands,
    pytest.mark.p0,
    pytest.mark.requires_capability(COMMANDS),
]


def test_command_runs_as_root_by_default(sdk_sandbox, sdk_e2e_config):
    """Root is the default user when user= is omitted."""
    result = sdk_sandbox.run_command(
        "id -un", timeout=sdk_e2e_config.command_timeout,
    )
    assert_command_ok(result)
    assert result.stdout.strip() == "root"


def test_command_runs_as_explicit_root(sdk_sandbox, sdk_e2e_config):
    result = sdk_sandbox.run_command(
        "id -un", timeout=sdk_e2e_config.command_timeout, user="root",
    )
    assert_command_ok(result)
    assert result.stdout.strip() == "root"


def test_command_runs_as_nobody(sdk_sandbox, sdk_e2e_config):
    """The non-root user 'nobody' should be accepted."""
    result = sdk_sandbox.run_command(
        "id -un", timeout=sdk_e2e_config.command_timeout, user="nobody",
    )
    assert_command_ok(result)
    assert result.stdout.strip() == "nobody"


def test_command_user_isolation_cannot_write_root_file(
    sdk_sandbox, sdk_e2e_config,
):
    """nobody should not be able to write to a root-owned file."""
    root_path = "/tmp/sdk-compat-user-isolation-root.txt"
    # Arrange: root creates a file that is not world-writable.
    result = sdk_sandbox.run_command(
        f"echo root-data > {root_path} && chmod 600 {root_path}",
        timeout=sdk_e2e_config.command_timeout,
        user="root",
    )
    assert_command_ok(result)

    # Assert: nobody cannot overwrite it.
    result = sdk_sandbox.run_command(
        f"echo nobody-data > {root_path} 2>&1; true",
        timeout=sdk_e2e_config.command_timeout,
        user="nobody",
    )
    # The command should fail (Permission denied)
    assert "denied" in result.stderr.lower() or "permission" in result.stderr.lower(), (
        f"Expected permission error, got stderr: {result.stderr!r}"
    )


def test_command_user_isolation_reads_own_file(sdk_sandbox, sdk_e2e_config):
    """A non-root user can create and read its own files in /tmp."""
    nobody_path = "/tmp/sdk-compat-user-isolation-nobody.txt"
    # Arrange: create a file as nobody in a directory nobody can write to.
    sdk_sandbox.run_command(
        f"echo nobody-data > {nobody_path}",
        timeout=sdk_e2e_config.command_timeout,
        user="nobody",
    )

    result = sdk_sandbox.run_command(
        f"cat {nobody_path}", timeout=sdk_e2e_config.command_timeout, user="nobody",
    )
    assert_command_ok(result)
    assert result.stdout.strip() == "nobody-data"


def test_command_user_default_env_vars_present(sdk_sandbox, sdk_e2e_config):
    """HOME and USER should reflect the requesting user."""
    for user_name in ("root", "nobody"):
        result = sdk_sandbox.run_command(
            "echo HOME=$HOME USER=$USER",
            timeout=sdk_e2e_config.command_timeout,
            user=user_name,
        )
        assert_command_ok(result)
        assert user_name in result.stdout, (
            f"Expected {user_name!r} in env for {user_name}, got: {result.stdout!r}"
        )
