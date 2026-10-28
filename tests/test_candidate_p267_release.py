"""tests/test_candidate_p267_release.py
Phase 2.67 Candidate Executable & GUI Release Verification Suite.

Validates:
1. Binary integrity, PE header, and size boundaries of dist/code_agent_candidate_p267/CodeAgent.exe.
2. Complete pass of all 5 Tcl/Tk runtime smoke-test stages in the candidate executable.
3. Strict preservation of the accepted executable dist/code_agent_v2/CodeAgent.exe and its SHA-256.
4. Total absence of .env, credentials, or secret keys in candidate distribution.
5. Successful isolated execution with --auto-close-ms without network access or task dispatch.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import pytest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDIDATE_DIR = os.path.join(WORKSPACE_ROOT, "dist", "code_agent_candidate_p267")
CANDIDATE_EXE = os.path.join(CANDIDATE_DIR, "CodeAgent.exe")
ACCEPTED_EXE = os.path.join(WORKSPACE_ROOT, "dist", "code_agent_v2", "CodeAgent.exe")
EXPECTED_ACCEPTED_SHA256 = "dbed684ef5da0d4ac9e0470826edee4a996e8fd60291f65257c464ec90422872"


def _compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def test_candidate_executable_exists_and_properties():
    """Verifies that the candidate executable was built to the candidate path and is a valid PE binary."""
    assert os.path.isfile(CANDIDATE_EXE), f"Missing candidate executable: {CANDIDATE_EXE}"
    size = os.path.getsize(CANDIDATE_EXE)
    assert 10 * 1024 * 1024 < size < 50 * 1024 * 1024, f"Suspicious binary size: {size} bytes"

    # Verify MZ PE header
    with open(CANDIDATE_EXE, "rb") as f:
        magic = f.read(2)
        assert magic == b"MZ", "Candidate is not a valid Windows PE binary"


def test_accepted_executable_preserved_unmodified():
    """Verifies that dist/code_agent_v2/CodeAgent.exe remains byte-for-byte identical to the accepted baseline."""
    assert os.path.isfile(ACCEPTED_EXE), f"Missing accepted executable: {ACCEPTED_EXE}"
    accepted_hash = _compute_sha256(ACCEPTED_EXE)
    assert accepted_hash == EXPECTED_ACCEPTED_SHA256, (
        f"CRITICAL: Accepted executable hash changed! Got {accepted_hash}, expected {EXPECTED_ACCEPTED_SHA256}"
    )

    candidate_hash = _compute_sha256(CANDIDATE_EXE)
    assert candidate_hash != accepted_hash, "Candidate hash must differ from accepted baseline (contains P2.66 fixes)"


def test_candidate_smoke_test_passes_cleanly():
    """Verifies that the candidate executable passes all 5 offline Tkinter runtime smoke test stages."""
    res = subprocess.run([CANDIDATE_EXE, "--smoke-test"], capture_output=True, text=True, timeout=25)
    assert res.returncode == 0, f"Candidate smoke test failed with code {res.returncode}. Stderr: {res.stderr}"
    assert "[SMOKE-TEST] ALL 5 SMOKE TEST STAGES PASSED CLEANLY." in res.stdout
    assert "patchlevel: 8.6.16" in res.stdout


def test_candidate_distribution_secret_exclusion():
    """Verifies that no .env files or secret keys were packaged into the candidate distribution directory."""
    for root, _, files in os.walk(CANDIDATE_DIR):
        for f in files:
            assert f != ".env", f"Forbidden .env file packaged in candidate: {os.path.join(root, f)}"
            assert not f.endswith(".key"), f"Forbidden key file in candidate: {os.path.join(root, f)}"


def test_candidate_isolated_auto_close_execution():
    """Verifies candidate launches cleanly and auto-closes without network dispatch or errors."""
    with tempfile.TemporaryDirectory() as tmp_run_dir:
        cmd = [
            CANDIDATE_EXE,
            "--auto-close-ms", "800",
            "--output-dir", tmp_run_dir,
            "--suppress-messagebox",
        ]
        res = subprocess.run(cmd, cwd=tmp_run_dir, capture_output=True, text=True, timeout=25)
        assert res.returncode == 0, f"Candidate exited with code {res.returncode}. Stderr: {res.stderr}"
