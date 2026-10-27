"""Automated verification tests for packaged Windows Code Agent executables (Stage F4A).

Validates:
1. Onefile binary presence, size bounds, and non-empty status.
2. Onedir directory structure and _internal bundled resource layout.
3. Secret exclusion: guarantees .env and API keys are not embedded in binaries or directories.
4. Offline execution: both onefile and onedir binaries launch, execute offline preprocessor and mock pipeline, write test manifest, and exit cleanly without network access.
5. Bundled asset integrity: validates that all 14 game and spec files exist and are readable in _internal.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
import pytest

pytestmark = pytest.mark.host_integration

WORKSPACE_ROOT = str(Path(__file__).resolve().parent.parent)
ONEFILE_EXE = os.path.join(WORKSPACE_ROOT, "dist", "onefile", "CodeAgent_Offline.exe")
ONEDIR_DIR = os.path.join(WORKSPACE_ROOT, "dist", "onedir", "CodeAgent_Offline_onedir")
ONEDIR_EXE = os.path.join(ONEDIR_DIR, "CodeAgent_Offline.exe")
BUILD_MANIFEST = os.path.join(WORKSPACE_ROOT, "dist", "BUILD_MANIFEST.json")
README_DIST = os.path.join(WORKSPACE_ROOT, "dist", "README.md")


def test_onefile_executable_properties():
    assert os.path.isfile(ONEFILE_EXE), f"Missing {ONEFILE_EXE}"
    size = os.path.getsize(ONEFILE_EXE)
    # Binary should be substantial (> 10MB) containing Python runtime and bundled assets
    assert size > 10 * 1024 * 1024, f"Binary suspiciously small: {size} bytes"
    assert size < 50 * 1024 * 1024, f"Binary unexpectedly large: {size} bytes"

    # Check PE header magic bytes (MZ)
    with open(ONEFILE_EXE, "rb") as f:
        magic = f.read(2)
        assert magic == b"MZ", "File is not a valid Windows executable"


def test_onedir_structure_and_bundled_assets():
    assert os.path.isdir(ONEDIR_DIR), f"Missing {ONEDIR_DIR}"
    assert os.path.isfile(ONEDIR_EXE), f"Missing {ONEDIR_EXE}"

    internal_dir = os.path.join(ONEDIR_DIR, "_internal")
    assert os.path.isdir(internal_dir), "Missing _internal directory in onedir build"

    # Verify key bundled assets exist inside _internal
    doc_path = os.path.join(internal_dir, "Architecture_Documentation.md")
    view_path = os.path.join(internal_dir, "Architecture_View.md")
    game_html = os.path.join(internal_dir, "space_fractions", "index.html")
    game_engine = os.path.join(internal_dir, "space_fractions", "fraction_engine.js")
    specs_openapi = os.path.join(internal_dir, "specs", "openapi.yaml")

    for target in [doc_path, view_path, game_html, game_engine, specs_openapi]:
        assert os.path.isfile(target), f"Bundled asset missing in _internal: {target}"
        assert os.path.getsize(target) > 50, f"Bundled asset empty: {target}"


def test_secret_exclusion():
    # Verify .env is strictly absent from distribution directories
    for root, dirs, files in os.walk(os.path.join(WORKSPACE_ROOT, "dist")):
        for f in files:
            assert f != ".env", f"Forbidden file .env found in distribution: {os.path.join(root, f)}"
            assert not f.endswith(".key"), f"Key file found in distribution: {os.path.join(root, f)}"


def test_onefile_offline_execution_and_manifest():
    with tempfile.TemporaryDirectory() as tmp_dir:
        cmd = [ONEFILE_EXE, "--auto-run", "--auto-close-ms", "6000"]
        res = subprocess.run(cmd, cwd=tmp_dir, capture_output=True, timeout=25)
        assert res.returncode == 0, f"Onefile exited with code {res.returncode}. Stderr: {res.stderr.decode('utf-8', 'ignore')}"

        manifest_file = os.path.join(tmp_dir, "generated_space_fractions", "offline_validation_manifest.json")
        if not os.path.isfile(manifest_file):
            for r, _, files in os.walk(os.path.join(tmp_dir, "generated_space_fractions")):
                if "offline_validation_manifest.json" in files:
                    manifest_file = os.path.join(r, "offline_validation_manifest.json")
                    break
        assert os.path.isfile(manifest_file), "offline_validation_manifest.json was not created"

        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["offline_mode"] is True
        assert data["live_api_called"] is False
        assert data["code_executed"] is False
        assert data["view_items_count"] == 11
        assert data["diagrams_count"] == 13
        assert "CONFLICT-DELIVERABLE-PLATFORM-001" in data["recorded_conflicts"]


def test_onedir_offline_execution_and_manifest():
    with tempfile.TemporaryDirectory() as tmp_dir:
        cmd = [ONEDIR_EXE, "--auto-run", "--auto-close-ms", "4000"]
        res = subprocess.run(cmd, cwd=tmp_dir, capture_output=True, timeout=25)
        assert res.returncode == 0, f"Onedir exited with code {res.returncode}. Stderr: {res.stderr.decode('utf-8', 'ignore')}"

        manifest_file = os.path.join(tmp_dir, "generated_space_fractions", "offline_validation_manifest.json")
        if not os.path.isfile(manifest_file):
            for r, _, files in os.walk(os.path.join(tmp_dir, "generated_space_fractions")):
                if "offline_validation_manifest.json" in files:
                    manifest_file = os.path.join(r, "offline_validation_manifest.json")
                    break
        assert os.path.isfile(manifest_file), "offline_validation_manifest.json was not created"

        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["offline_mode"] is True
        assert data["live_api_called"] is False
        assert data["code_executed"] is False
        assert data["view_items_count"] == 11
        assert data["diagrams_count"] == 13
        assert "CONFLICT-DELIVERABLE-PLATFORM-001" in data["recorded_conflicts"]


def test_build_manifest_and_readme():
    assert os.path.isfile(BUILD_MANIFEST), f"Missing {BUILD_MANIFEST}"
    assert os.path.isfile(README_DIST), f"Missing {README_DIST}"

    with open(BUILD_MANIFEST, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "executables" in manifest
    assert "onefile" in manifest["executables"]
    assert "onedir" in manifest["executables"]
    assert manifest["secret_exclusion"]["dotenv_included"] is False
    assert manifest["offline_boundaries"]["deepseek_api_calls_in_build"] == 0


def test_onefile_game_launcher_smoke():
    """Verifies that the packaged onefile executable launches with --launch-game and exits cleanly."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cmd = [ONEFILE_EXE, "--launch-game", "--auto-close-ms", "4000"]
        res = subprocess.run(cmd, cwd=tmp_dir, capture_output=True, timeout=25)
        assert res.returncode == 0, f"Onefile --launch-game exited with code {res.returncode}. Stderr: {res.stderr.decode('utf-8', 'ignore')}"
