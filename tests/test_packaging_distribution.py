"""Verification for the single supported Windows release executable."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.host_integration

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "CodeAgent.exe"
MANIFEST = ROOT / "dist" / "BUILD_MANIFEST.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_release_executable_matches_manifest():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert EXE.is_file()
    assert 10 * 1024 * 1024 < EXE.stat().st_size < 50 * 1024 * 1024
    with EXE.open("rb") as stream:
        assert stream.read(2) == b"MZ"
    assert data["executable"]["path"] == "dist/CodeAgent.exe"
    assert data["executable"]["size_bytes"] == EXE.stat().st_size
    assert data["executable"]["sha256"] == _sha256(EXE)


def test_distribution_contains_no_secrets_or_legacy_binaries():
    root_files = [path for path in (ROOT / "dist").iterdir() if path.is_file()]
    assert {path.name for path in root_files} == {"CodeAgent.exe", "BUILD_MANIFEST.json", "README.md"}
    all_files = [path for path in (ROOT / "dist").rglob("*") if path.is_file()]
    assert not any(path.name == ".env" or path.suffix == ".key" for path in all_files)


def test_release_smoke_test():
    result = subprocess.run(
        [str(EXE), "--smoke-test"],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stderr
    assert "ALL 5 SMOKE TEST STAGES PASSED CLEANLY" in result.stdout
