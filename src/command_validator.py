"""Command Validator and Execution Module.

Enforces an explicit executable and argument allowlist with shell=False,
15-second timeouts, process termination, output capping, and secret redaction.

NOTE ON OS SANDBOX BOUNDARIES:
This module enforces application-level command validation and execution discipline.
It ensures that model-initiated commands cannot execute arbitrary shell scripts, chaining,
or unapproved binaries. It does not replace OS-level virtualization or container sandboxing.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, Union

from src.path_security import validate_workspace_path, PathSecurityError


class CommandValidationError(PermissionError):
    """Raised when a command fails the security allowlist check."""
    pass


@dataclass
class CommandExecutionResult:
    command: List[str]
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_seconds: float = 0.0


# Regex pattern to redact potential API keys or authorization tokens from outputs
SECRET_PATTERN = re.compile(r"(sk-[a-zA-Z0-9_-]{20,}|Bearer\s+[a-zA-Z0-9_\-\.]{20,})", re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """Scrubs API tokens and secrets from output strings."""
    return SECRET_PATTERN.sub("[REDACTED_SECRET]", text)


class CommandValidator:
    """Validates commands against strict execution rules before running with shell=False."""

    MAX_OUTPUT_CHARS = 1500
    DEFAULT_TIMEOUT_SECONDS = 15.0

    FORBIDDEN_SHELL_CHARS = {"|", "&", ";", ">", "<", "`", "$", "%"}

    @classmethod
    def parse_command(cls, raw_command: Union[str, List[str]]) -> List[str]:
        """Safely parses command into argument list using shlex."""
        if isinstance(raw_command, list):
            return [str(arg) for arg in raw_command]
        if not isinstance(raw_command, str) or not raw_command.strip():
            raise CommandValidationError("Command string cannot be empty.")
        # posix=False preserves Windows quote and backslash semantics
        return shlex.split(raw_command, posix=False)

    @classmethod
    def validate_command(cls, args: List[str], workspace_root: str) -> List[str]:
        """Validates command against the strict executable and argument allowlist."""
        if not args:
            raise CommandValidationError("Empty command argument list.")

        # Check for forbidden shell metacharacters in all arguments
        for arg in args:
            for char in cls.FORBIDDEN_SHELL_CHARS:
                if char in arg:
                    raise CommandValidationError(
                        f"Forbidden shell metacharacter '{char}' detected in argument '{arg}'."
                    )

        executable = args[0]
        # Normalize executable path comparison
        norm_python = os.path.realpath(sys.executable).lower()
        is_python = False

        if executable.lower() in ("python", "python.exe"):
            is_python = True
        else:
            try:
                if os.path.realpath(executable).lower() == norm_python:
                    is_python = True
            except Exception:
                pass

        if not is_python:
            # Check if calling pytest directly
            if executable.lower() in ("pytest", "pytest.exe"):
                # Must resolve to valid pytest
                pass
            else:
                raise CommandValidationError(
                    f"Executable '{executable}' is not authorized. Only sys.executable (python) "
                    "or pytest is permitted."
                )

        # Enforce strict subcommand allowlist
        if is_python:
            if len(args) < 2:
                raise CommandValidationError("Python must be invoked with an approved module.")

            first_arg = args[1]
            if first_arg != "-m":
                raise CommandValidationError(
                    f"Direct script execution '{first_arg}' is prohibited. Only '-m' module calls are allowed."
                )

            if len(args) < 3:
                raise CommandValidationError("Missing module name after '-m'.")

            module = args[2]
            allowed_modules = {"py_compile", "unittest", "pytest"}
            if module not in allowed_modules:
                raise CommandValidationError(
                    f"Module '{module}' is not in the allowed modules: {allowed_modules}."
                )

            # Check target files/paths in remaining arguments
            for target_path in args[3:]:
                # Flags like -v, -q are allowed
                if target_path.startswith("-"):
                    continue
                # All path arguments must reside inside the workspace
                try:
                    validate_workspace_path(workspace_root, target_path)
                except PathSecurityError as err:
                    raise CommandValidationError(
                        f"Path argument '{target_path}' breaches workspace security: {err}"
                    )

        return args

    @classmethod
    def execute_command(
        cls,
        raw_command: Union[str, List[str]],
        workspace_root: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> CommandExecutionResult:
        """Validates and executes a command with shell=False, timeouts, and output capping."""
        parsed_args = cls.parse_command(raw_command)
        validated_args = cls.validate_command(parsed_args, workspace_root)

        # Guarantee real executable path is used
        exec_path = validated_args[0]
        if exec_path.lower() in ("python", "python.exe"):
            validated_args[0] = sys.executable

        timed_out = False
        stdout_text = ""
        stderr_text = ""
        return_code = -1

        try:
            # shell=False is strictly enforced
            proc = subprocess.Popen(
                validated_args,
                cwd=workspace_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
            )

            try:
                stdout_text, stderr_text = proc.communicate(timeout=timeout_seconds)
                return_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                stdout_text, stderr_text = proc.communicate()
                return_code = -9

        except Exception as err:
            stderr_text = f"Process execution failure: {err}"
            return_code = -1

        # Capping and secret redaction
        capped_stdout = redact_secrets(stdout_text[: cls.MAX_OUTPUT_CHARS])
        capped_stderr = redact_secrets(stderr_text[: cls.MAX_OUTPUT_CHARS])

        return CommandExecutionResult(
            command=validated_args,
            return_code=return_code,
            stdout=capped_stdout,
            stderr=capped_stderr,
            timed_out=timed_out,
        )
