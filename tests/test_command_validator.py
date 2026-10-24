"""Unit tests for CommandValidator.

Validates shell=False execution, argument parsing, allowlist checks,
rejection of shell chaining/redirection, timeout termination, and secret masking.
"""

import os
import sys
import tempfile
import pytest
from src.command_validator import (
    CommandValidationError,
    CommandValidator,
    redact_secrets,
)


@pytest.fixture
def workspace_env():
    """Sets up a temporary isolated workspace with a dummy python file."""
    with tempfile.TemporaryDirectory(prefix="cmd_ws_") as ws_dir:
        test_file = os.path.join(ws_dir, "sample.py")
        with open(test_file, "w") as f:
            f.write("def add(a, b): return a + b\n")
        yield os.path.realpath(ws_dir)


def test_allowed_compile_command(workspace_env):
    """Validates that python -m py_compile on a workspace file is permitted and runs."""
    res = CommandValidator.execute_command(
        f"python -m py_compile sample.py",
        workspace_root=workspace_env,
    )
    assert res.return_code == 0
    assert not res.timed_out


def test_reject_arbitrary_binary(workspace_env):
    """Validates that arbitrary binaries (cmd, powershell, curl, bash) are rejected."""
    with pytest.raises(CommandValidationError) as exc:
        CommandValidator.validate_command(["cmd.exe", "/c", "dir"], workspace_env)
    assert "not authorized" in str(exc.value)

    with pytest.raises(CommandValidationError) as exc:
        CommandValidator.validate_command(["powershell", "Get-Process"], workspace_env)
    assert "not authorized" in str(exc.value)


def test_reject_python_dash_c(workspace_env):
    """Validates that inline python execution (-c) is blocked."""
    # Test -c without metacharacters
    with pytest.raises(CommandValidationError) as exc:
        CommandValidator.validate_command(["python", "-c", "print(1)"], workspace_env)
    assert "Direct script execution" in str(exc.value) or "allowed modules" in str(exc.value)

    # Test -c with metacharacters
    with pytest.raises(CommandValidationError) as exc2:
        CommandValidator.validate_command(["python", "-c", "import os; os.system('calc')"], workspace_env)
    assert "Forbidden shell metacharacter" in str(exc2.value) or "Direct script execution" in str(exc2.value)


def test_reject_shell_chaining_and_redirection(workspace_env):
    """Validates that shell metacharacters (&, |, ;, >, <) are rejected."""
    with pytest.raises(CommandValidationError) as exc:
        CommandValidator.validate_command(["python", "-m", "py_compile", "sample.py", "&&", "dir"], workspace_env)
    assert "Forbidden shell metacharacter" in str(exc.value)

    with pytest.raises(CommandValidationError) as exc:
        CommandValidator.validate_command(["python", "-m", "py_compile", "sample.py", ">", "out.txt"], workspace_env)
    assert "Forbidden shell metacharacter" in str(exc.value)


def test_reject_path_escaping_workspace(workspace_env):
    """Validates that commands targeting files outside workspace are blocked."""
    with pytest.raises(CommandValidationError) as exc:
        CommandValidator.validate_command(
            ["python", "-m", "py_compile", "../../outside_exploit.py"],
            workspace_env,
        )
    assert "breaches workspace security" in str(exc.value)


def test_timeout_and_process_termination(workspace_env):
    """Validates that timed-out processes are killed and flagged."""
    infinite_file = os.path.join(workspace_env, "infinite.py")
    with open(infinite_file, "w") as f:
        f.write("import time\nwhile True: time.sleep(0.5)\n")

    # Run with a short 1.0 second timeout
    res = CommandValidator.execute_command(
        f"python -m py_compile infinite.py",
        workspace_root=workspace_env,
        timeout_seconds=1.0,
    )
    # py_compile compiles it immediately, so let's test with unittest running an infinite test:
    test_infinite = os.path.join(workspace_env, "test_inf.py")
    with open(test_infinite, "w") as f:
        f.write("import unittest, time\nclass T(unittest.TestCase):\n  def test_x(self):\n    time.sleep(5)\n")

    res = CommandValidator.execute_command(
        f"python -m unittest test_inf.py",
        workspace_root=workspace_env,
        timeout_seconds=1.0,
    )
    assert res.timed_out is True
    assert res.return_code != 0


def test_secret_redaction():
    """Validates that API keys and Bearer tokens are redacted from tool output."""
    raw_output = "Error connecting to DeepSeek with key sk-1234567890abcdef1234567890: access denied"
    scrubbed = redact_secrets(raw_output)
    assert "sk-1234567890" not in scrubbed
    assert "[REDACTED_SECRET]" in scrubbed
