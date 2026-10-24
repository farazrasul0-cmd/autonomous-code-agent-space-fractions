"""Unit tests for Path Security & Workspace Sandboxing.

Validates os.path.commonpath checks against sibling directory escapes,
dot-dot traversal, absolute/UNC paths, symlink escapes, and valid nested writes.
"""

import os
import tempfile
import pytest
from src.path_security import (
    PathSecurityError,
    safe_read_file,
    safe_write_file,
    validate_workspace_path,
)


@pytest.fixture
def workspace_env():
    """Sets up a temporary isolated workspace."""
    with tempfile.TemporaryDirectory(prefix="agent_ws_") as ws_dir:
        yield os.path.realpath(ws_dir)


def test_valid_nested_path(workspace_env):
    """Validates that legitimate nested relative paths resolve safely inside workspace."""
    path = validate_workspace_path(workspace_env, "src/components/game.py")
    assert path.startswith(workspace_env)
    assert os.path.commonpath([workspace_env, path]) == workspace_env


def test_sibling_directory_prefix_escape(workspace_env):
    """CRITICAL SECURITY CHECK: Validates rejection of sibling directory attacks.
    
    If workspace is /tmp/agent_ws_123, an attacker trying to access
    /tmp/agent_ws_123_sibling would bypass naive string.startswith() checks.
    os.path.commonpath() must detect and block this.
    """
    sibling_attempt = "../" + os.path.basename(workspace_env) + "_sibling/secret.txt"
    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, sibling_attempt)


def test_dot_dot_traversal(workspace_env):
    """Validates that directory traversal attempts with '..' are blocked."""
    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, "../../Windows/System32/cmd.exe")

    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, "sub/../../outside.txt")


def test_absolute_and_unc_paths(workspace_env):
    """Validates that POSIX absolute, Windows drive absolute, and UNC paths are blocked."""
    # POSIX absolute
    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, "/etc/passwd")

    # Windows drive absolute
    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, "C:\\Windows\\System32\\calc.exe")

    # Windows UNC network share
    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, "\\\\192.168.1.1\\share\\exploit.exe")


def test_null_byte_rejection(workspace_env):
    """Validates rejection of paths containing null byte poison characters."""
    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, "valid_name.txt\0.exe")


def test_safe_write_and_read(workspace_env):
    """Validates safe atomic write and read within workspace."""
    content = "print('Space Fractions Engine Active')"
    rel_path = "modules/engine.py"

    written_path = safe_write_file(workspace_env, rel_path, content)
    assert os.path.isfile(written_path)

    read_back = safe_read_file(workspace_env, rel_path)
    assert read_back == content


def test_symlink_escape_detection(workspace_env):
    """Validates detection and blocking of symlinks pointing outside the workspace.
    
    If host OS privileges restrict symlink creation (e.g. Windows without Dev Mode),
    the test logs an explicit skip with reason.
    """
    outside_dir = tempfile.mkdtemp(prefix="agent_outside_")
    outside_file = os.path.join(outside_dir, "outside_secret.txt")
    with open(outside_file, "w") as f:
        f.write("top_secret")

    symlink_path = os.path.join(workspace_env, "symlink_to_outside")
    try:
        os.symlink(outside_dir, symlink_path, target_is_directory=True)
    except (OSError, NotImplementedError) as err:
        pytest.skip(f"Host Windows environment does not permit unprivileged symlinks: {err}")

    # Now attempt to write/read through the symlink
    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, "symlink_to_outside/outside_secret.txt")

    with pytest.raises(PathSecurityError):
        safe_read_file(workspace_env, "symlink_to_outside/outside_secret.txt")


def test_symlink_escape_detection_mocked(workspace_env, monkeypatch):
    """Deterministically validates symlink escape detection using simulated realpath resolution.
    
    Ensures validation logic is verified regardless of host OS symlink creation permissions.
    """
    outside_target = "C:\\Restricted\\Secrets\\passwords.txt"
    fake_link = os.path.join(workspace_env, "fake_link.txt")
    
    # Simulate that fake_link exists and resolves to outside_target
    orig_exists = os.path.exists
    orig_realpath = os.path.realpath
    orig_islink = os.path.islink

    def mock_exists(p):
        if os.path.abspath(p) == os.path.abspath(fake_link):
            return True
        return orig_exists(p)

    def mock_islink(p):
        if os.path.abspath(p) == os.path.abspath(fake_link):
            return True
        return orig_islink(p)

    def mock_realpath(p):
        if os.path.abspath(p) == os.path.abspath(fake_link):
            return outside_target
        return orig_realpath(p)

    monkeypatch.setattr(os.path, "exists", mock_exists)
    monkeypatch.setattr(os.path, "islink", mock_islink)
    monkeypatch.setattr(os.path, "realpath", mock_realpath)

    with pytest.raises(PathSecurityError) as exc:
        validate_workspace_path(workspace_env, "fake_link.txt")
    assert "Symlink escape detected" in str(exc.value) or "outside workspace" in str(exc.value)
