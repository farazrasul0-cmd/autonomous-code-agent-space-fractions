"""Path Security & Workspace Sandboxing Module.

Enforces workspace boundary checks via normalized absolute paths and os.path.commonpath().
Guards against sibling-prefix escapes, directory traversal, absolute/UNC paths, and symlink escapes.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional


class PathSecurityError(PermissionError):
    """Raised when a path breaches the designated workspace boundaries."""
    pass


def validate_workspace_path(workspace_root: str, relative_path: str) -> str:
    """Validates that relative_path strictly resolves within workspace_root.
    
    Uses os.path.commonpath() instead of string prefix matching to prevent sibling-prefix
    attacks (e.g. C:\\workspace_other matching prefix C:\\workspace).
    
    Handles non-existent paths safely by verifying nearest existing ancestor directories.
    
    NOTE ON RESIDUAL RISKS:
    This function provides rigorous application-level path validation. However, on multi-process
    systems without OS-level file handles (such as openat2 with RESOLVE_BENEATH on Linux),
    a residual Time-of-Check to Time-of-Use (TOCTOU) race condition exists if an external concurrent
    process modifies an intermediate directory into a symlink between validation and disk I/O.
    """
    if not workspace_root:
        raise ValueError("Workspace root cannot be empty.")
    if not relative_path or not relative_path.strip():
        raise ValueError("Relative path cannot be empty.")

    # Guard against null byte injections
    if "\0" in relative_path or "\0" in workspace_root:
        raise PathSecurityError("Null bytes detected in path.")

    # Reject UNC paths and network shares
    if relative_path.startswith(("\\\\", "//")):
        raise PathSecurityError(f"UNC network paths are prohibited: '{relative_path}'")

    # Reject absolute paths (POSIX absolute '/' or Windows drive letters 'C:')
    if (
        os.path.isabs(relative_path)
        or relative_path.startswith(("/", "\\"))
        or (len(relative_path) >= 2 and relative_path[1] == ":")
    ):
        raise PathSecurityError(f"Absolute paths are prohibited: '{relative_path}'")

    # Canonicalize the workspace root (resolving symlinks in the root itself)
    norm_workspace = os.path.realpath(os.path.abspath(workspace_root))

    # Resolve candidate target path
    candidate_path = os.path.abspath(os.path.join(norm_workspace, relative_path))

    # 1. Primary commonpath check on un-symlinked candidate
    try:
        common = os.path.commonpath([norm_workspace, candidate_path])
        if common != norm_workspace:
            raise PathSecurityError(
                f"Path traversal detected: '{relative_path}' escapes workspace '{norm_workspace}'"
            )
    except ValueError as err:
        # Occurs on Windows if paths are on different drives
        raise PathSecurityError(f"Cross-drive path detected: '{relative_path}'. Error: {err}")

    # 2. Check existing path or nearest existing ancestor for symlinks pointing outside
    curr = candidate_path
    while curr and not os.path.exists(curr) and curr != norm_workspace:
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent

    if os.path.exists(curr):
        real_ancestor = os.path.realpath(curr)
        try:
            ancestor_common = os.path.commonpath([norm_workspace, real_ancestor])
            if ancestor_common != norm_workspace:
                raise PathSecurityError(
                    f"Symlink traversal detected: ancestor '{curr}' resolves to '{real_ancestor}' "
                    f"outside workspace '{norm_workspace}'"
                )
        except ValueError:
            raise PathSecurityError(f"Cross-drive ancestor symlink detected for '{relative_path}'")

    # 3. If target itself already exists and is a symlink, verify its real target
    if os.path.islink(candidate_path) or os.path.exists(candidate_path):
        real_target = os.path.realpath(candidate_path)
        try:
            target_common = os.path.commonpath([norm_workspace, real_target])
            if target_common != norm_workspace:
                raise PathSecurityError(
                    f"Symlink escape detected: target resolves to '{real_target}' "
                    f"outside workspace '{norm_workspace}'"
                )
        except ValueError:
            raise PathSecurityError(f"Cross-drive symlink target detected for '{relative_path}'")

    return candidate_path


def safe_read_file(workspace_root: str, relative_path: str, encoding: str = "utf-8") -> str:
    """Reads a file safely from the designated workspace."""
    verified_path = validate_workspace_path(workspace_root, relative_path)
    if not os.path.isfile(verified_path):
        raise FileNotFoundError(f"File not found: '{relative_path}'")
    with open(verified_path, "r", encoding=encoding) as f:
        return f.read()


def safe_write_file(
    workspace_root: str, relative_path: str, content: str, encoding: str = "utf-8"
) -> str:
    """Writes a file safely into the designated workspace using atomic replacement."""
    verified_path = validate_workspace_path(workspace_root, relative_path)
    target_dir = os.path.dirname(verified_path)

    # Ensure intermediate directories exist
    os.makedirs(target_dir, exist_ok=True)

    # Atomic write pattern using temp file in the same target directory
    temp_fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix=".tmp_atomic_")
    try:
        with open(temp_fd, "w", encoding=encoding) as f:
            f.write(content)
        # Atomic replace on Windows/POSIX
        os.replace(temp_path, verified_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    return verified_path
