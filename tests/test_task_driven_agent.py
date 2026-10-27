"""Unit tests for Minimal Task-Driven Agent Integration (Phase 2.27.43).

Verifies:
1. Task goal capture, trimming, and propagation to AgentLoop.
2. Calculator-specific mock tool calls and generation.
3. Output directory slug reflects the task goal (not hardcoded space_fractions_run_).
4. Manifest truthfulness: OFFLINE_DETERMINISTIC_MOCK, live_api_called=false, code_executed=false.
5. Generic static acceptance validation (path containment, non-empty files, AST syntax parsing).
6. Preservation of legacy Space Fractions demonstration behavior.
7. Cancellation preventing promotion of partial output.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import tkinter as tk
import pytest

from src.agent_gui import AgentGUIApp, OfflineMockAgentClient
from src.project_validator import ProjectValidator


@pytest.fixture
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


@pytest.fixture
def canonical_doc_paths():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc = os.path.join(repo_root, "Architecture_Documentation.md")
    view = os.path.join(repo_root, "Architecture_View.md")
    return doc, view


def test_calculator_task_reaches_agent_and_promotes_slug_dir(tk_root, canonical_doc_paths, tmp_path):
    """Verifies that a calculator task drives AgentLoop, writes calculator files, and promotes a slug dir."""
    doc_path, view_path = canonical_doc_paths
    output_dir = str(tmp_path)
    task_goal = "Build a Python command-line calculator with arithmetic operations"

    app = AgentGUIApp(tk_root, suppress_messagebox=True, default_task=task_goal)
    assert app.task_desc_var.get() == task_goal

    app.doc_path_var.set(doc_path)
    app.view_path_var.set(view_path)
    app.output_dir_var.set(output_dir)
    app.execution_mode_var.set("mock_agent")

    run_id = "calc_test_001"
    app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id=run_id, task_goal=task_goal)

    # 1. Output directory should be promoted with task slug
    assert app.last_run_dir is not None
    assert os.path.isdir(app.last_run_dir)
    dir_name = os.path.basename(app.last_run_dir)
    assert "calculator" in dir_name or "build_a_python_comma" in dir_name
    assert not dir_name.startswith("space_fractions_run_")

    # 2. Calculator files must exist and be non-empty
    calc_py = os.path.join(app.last_run_dir, "calculator.py")
    test_calc_py = os.path.join(app.last_run_dir, "tests", "test_calculator.py")
    readme_md = os.path.join(app.last_run_dir, "README.md")
    manifest_json = os.path.join(app.last_run_dir, "offline_validation_manifest.json")

    assert os.path.isfile(calc_py) and os.path.getsize(calc_py) > 0
    assert os.path.isfile(test_calc_py) and os.path.getsize(test_calc_py) > 0
    assert os.path.isfile(readme_md) and os.path.getsize(readme_md) > 0
    assert os.path.isfile(manifest_json) and os.path.getsize(manifest_json) > 0

    # 3. Manifest truthfulness checks
    with open(manifest_json, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    assert manifest_data["mode"] == "OFFLINE_DETERMINISTIC_MOCK"
    assert manifest_data["live_api_called"] is False
    assert manifest_data["code_executed"] is False
    assert manifest_data["static_validation_passed"] is True
    assert manifest_data["runtime_gameplay_verified"] is False
    assert manifest_data["task_goal"] == task_goal
    assert "calculator.py" in manifest_data["generated_files"]

    # 4. AST static syntax verification via ProjectValidator
    val_res = ProjectValidator.validate_generic_project(app.last_run_dir)
    assert val_res.success is True
    assert val_res.ast_syntax_valid is True
    assert val_res.tests_valid is False  # Honest contract: tests not executed
    assert "SyntaxVerified: calculator.py" in val_res.verified_interfaces


def test_generic_project_validator_rejects_syntax_error(tmp_path):
    """Verifies that validate_generic_project catches AST syntax errors without executing code."""
    proj_dir = tmp_path / "bad_syntax_proj"
    proj_dir.mkdir()

    bad_py = proj_dir / "broken.py"
    bad_py.write_text("def broken_func(:\n    pass\n", encoding="utf-8")

    res = ProjectValidator.validate_generic_project(str(proj_dir))
    assert res.success is False
    assert res.ast_syntax_valid is False
    assert any("Syntax error" in err or "SyntaxError" in err for err in res.errors)


def test_generic_project_validator_rejects_empty_file(tmp_path):
    """Verifies that validate_generic_project catches empty files."""
    proj_dir = tmp_path / "empty_file_proj"
    proj_dir.mkdir()

    empty_file = proj_dir / "empty.py"
    empty_file.write_text("", encoding="utf-8")

    res = ProjectValidator.validate_generic_project(str(proj_dir))
    assert res.success is False
    assert any("empty (0 bytes)" in err for err in res.errors)


def test_cancellation_during_generic_task_does_not_promote(tk_root, canonical_doc_paths, tmp_path):
    """Verifies that cancelling a generic task aborts promotion and leaves no promoted directory."""
    doc_path, view_path = canonical_doc_paths
    output_dir = str(tmp_path)
    task_goal = "Calculator project that will be cancelled"

    app = AgentGUIApp(tk_root, suppress_messagebox=True, default_task=task_goal)
    app.doc_path_var.set(doc_path)
    app.view_path_var.set(view_path)
    app.output_dir_var.set(output_dir)

    # Signal cancellation before worker starts
    app.cancel_requested.set()

    run_id = "calc_cancel_001"
    app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id=run_id, task_goal=task_goal)

    assert app.run_state == "CANCELLED"
    assert app.last_run_dir is None
    # No promoted directory should exist
    promoted_dirs = [d for d in os.listdir(output_dir) if not d.startswith(".staging_")]
    assert len(promoted_dirs) == 0


def test_legacy_space_fractions_demo_preserved(tk_root, canonical_doc_paths, tmp_path):
    """Verifies that the default task goal preserves legacy Space Fractions behavior."""
    doc_path, view_path = canonical_doc_paths
    output_dir = str(tmp_path)

    app = AgentGUIApp(tk_root, suppress_messagebox=True)
    # Default task goal mentions architecture specs
    assert "Ingest architecture specs" in app.task_desc_var.get()

    app.doc_path_var.set(doc_path)
    app.view_path_var.set(view_path)
    app.output_dir_var.set(output_dir)
    app.execution_mode_var.set("offline")

    run_id = "legacy_demo_001"
    app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id=run_id)

    assert app.last_run_dir is not None
    assert os.path.basename(app.last_run_dir).startswith("space_fractions_run_")
    manifest_p = os.path.join(app.last_run_dir, "offline_validation_manifest.json")
    assert os.path.isfile(manifest_p)

    with open(manifest_p, "r", encoding="utf-8") as f:
        m = json.load(f)
    assert m["validation_run"] == "Stage F2 Offline Desktop GUI Shell Test"
