"""Unit tests for AgentGUIApp desktop interface and offline integration.

Tests:
1. GUI initialization, layout, and control existence.
2. Masked API key entry security (show="*") and local .env loading.
3. Input validation: missing doc/view files and invalid output paths.
4. Duplicate-start prevention and state recovery.
5. Accurate distinction between offline mock mode and live API spend.
6. Cancel event signaling and worker thread coordination.
7. End-to-end offline integration: preprocessor execution, conflict display, and pipeline run.
8. Guarantee of zero HTTP network requests and zero subprocess executions.
"""

import http.client
import os
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox
import urllib.request
import pytest

from src.agent_gui import AgentGUIApp
from src.spec_preprocessor import SpecificationPreprocessResult


@pytest.fixture(scope="module")
def tk_root():
    """Provides a hidden Tk root window for headless-safe testing."""
    try:
        root = tk.Tk()
        root.withdraw()  # Hide GUI window during automated test execution
        yield root
        root.destroy()
    except tk.TclError as err:
        pytest.skip(f"Tkinter display environment unavailable: {err}")


@pytest.fixture
def canonical_doc_paths():
    doc_path = os.path.abspath("Architecture_Documentation.md")
    view_path = os.path.abspath("Architecture_View.md")
    assert os.path.isfile(doc_path), f"Missing {doc_path}"
    assert os.path.isfile(view_path), f"Missing {view_path}"
    return doc_path, view_path


def test_gui_initialization_and_controls(tk_root):
    app = AgentGUIApp(tk_root)

    # Window title and configuration
    assert "DeepSeek Autonomous Code Agent" in tk_root.title()
    assert "Offline Shell" in tk_root.title()

    # Form inputs exist
    assert hasattr(app, "api_key_entry")
    assert hasattr(app, "doc_entry")
    assert hasattr(app, "view_entry")
    assert hasattr(app, "output_entry")
    assert hasattr(app, "task_entry")

    # Action controls exist
    assert hasattr(app, "btn_start")
    assert hasattr(app, "btn_cancel")
    assert hasattr(app, "progress_bar")
    assert hasattr(app, "status_var")

    # Initial state
    assert app.is_running is False
    assert str(app.btn_start.cget("state")) == "normal"
    assert "disabled" in str(app.btn_cancel.cget("state"))

    # Budget defaults
    assert app.budget_controller.cumulative_spend == 0.000186
    assert app.budget_controller.ceiling == 0.800000


def test_masked_key_field_security(tk_root):
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    # Verify input masking
    assert app.api_key_entry.cget("show") == "*"

    # Set dummy secret in variable
    dummy_key = "sk-test-secret-value-12345"
    app.api_key_var.set(dummy_key)

    # Ensure secret is not exposed in cleartext labels or window title
    assert dummy_key not in tk_root.title()
    assert dummy_key not in app.key_status_var.get()
    assert dummy_key not in app.status_var.get()


def test_offline_status_vs_live_api_distinction(tk_root):
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    # Telemetry cards must accurately show Gate E verified spend ($0.000186), not zero or invented numbers
    verified_label = app.telemetry_labels.get("Cumulative Verified Live Spend:")
    assert verified_label is not None
    assert verified_label.cget("text") == "$0.000186"

    # Ceiling must show $0.800000
    ceiling_label = app.telemetry_labels.get("Hard Project Budget Ceiling:")
    assert ceiling_label is not None
    assert ceiling_label.cget("text") == "$0.800000"

    # Remaining budget must be $0.799814
    remaining_label = app.telemetry_labels.get("Remaining Available Budget:")
    assert remaining_label is not None
    assert remaining_label.cget("text") == "$0.799814"


def test_missing_input_validation(tk_root, monkeypatch):
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    # Intercept messagebox.showerror to prevent modal dialog hang
    error_recorded = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, msg: error_recorded.append((title, msg)))

    # 1. Non-existent doc file
    app.doc_path_var.set("non_existent_doc_file.md")
    app.on_start_click()
    assert len(error_recorded) == 1
    assert "Invalid Input" in error_recorded[0][0]
    assert app.is_running is False
    assert str(app.btn_start.cget("state")) == "normal"

    # 2. Non-existent view file
    app.doc_path_var.set(os.path.abspath("Architecture_Documentation.md"))
    app.view_path_var.set("non_existent_view_file.md")
    app.on_start_click()
    assert len(error_recorded) == 2
    assert "Invalid Input" in error_recorded[1][0]
    assert app.is_running is False

    # 3. Empty output directory
    app.view_path_var.set(os.path.abspath("Architecture_View.md"))
    app.output_dir_var.set("")
    app.on_start_click()
    assert len(error_recorded) == 3
    assert app.is_running is False


def test_duplicate_start_prevention(tk_root):
    app = AgentGUIApp(tk_root)

    # Set running state
    app.is_running = True
    initial_thread = app.worker_thread

    # Triggering start while running should return immediately
    app.on_start_click()
    assert app.worker_thread == initial_thread


def test_cancel_button_signaling(tk_root):
    app = AgentGUIApp(tk_root)

    # Not running: cancel does nothing
    app.is_running = False
    app.on_cancel_click()
    assert not app.cancel_requested.is_set()

    # Running: cancel sets the event
    app.is_running = True
    app.on_cancel_click()
    assert app.cancel_requested.is_set()
    assert "Cancelling" in app.status_var.get()


def test_worker_offline_execution_and_queue_handling(tk_root, canonical_doc_paths):
    doc_path, view_path = canonical_doc_paths

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_gui_out")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app.output_dir_var.set(output_dir)

        # Directly invoke the worker method to avoid thread timing nondeterminism
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir)

        # Process all queued messages on main thread
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        # Check results
        assert app.is_running is False
        assert app.last_preprocess_result is not None
        assert len(app.last_preprocess_result.view_items) == 11
        assert len(app.last_preprocess_result.diagrams) == 13
        assert len(app.last_preprocess_result.recorded_conflicts) >= 1

        # Check conflict in details text
        details_content = app.details_text.get("1.0", tk.END)
        assert "CONFLICT-DELIVERABLE-PLATFORM-001" in details_content
        assert "Native Windows Desktop" in details_content

        # Check artifacts treeview
        tree_children = app.artifacts_tree.get_children()
        assert len(tree_children) >= 1

        # Check that demonstration test manifest was written into output directory
        manifest_file = os.path.join(output_dir, "offline_validation_manifest.json")
        assert os.path.isfile(manifest_file)
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest_text = f.read()
            assert "Stage F2 Offline Desktop GUI Shell Test" in manifest_text
            assert '"offline_mode": true' in manifest_text
            assert '"live_api_called": false' in manifest_text

        # Verify live spend is unchanged
        assert app.budget_controller.cumulative_spend == 0.000186


def test_no_http_or_subprocess_calls_in_gui(tk_root, canonical_doc_paths, monkeypatch):
    doc_path, view_path = canonical_doc_paths

    def prohibited_call(*args, **kwargs):
        raise AssertionError("Prohibited call attempted during GUI operation!")

    monkeypatch.setattr(urllib.request, "urlopen", prohibited_call)
    monkeypatch.setattr(http.client.HTTPConnection, "request", prohibited_call)
    monkeypatch.setattr(subprocess, "Popen", prohibited_call)
    monkeypatch.setattr(subprocess, "run", prohibited_call)

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_safe_out")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir)

        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        assert app.last_execution_summary.success is True


def test_deepseek_gui_wiring_and_mode_controls(tk_root):
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    # Mode radiobuttons exist
    assert hasattr(app, "radio_offline")
    assert hasattr(app, "radio_live")
    assert app.execution_mode_var.get() == "offline"

    # Live radiobutton is permanently disabled pending Stage F5D authorization
    assert "disabled" in str(app.radio_live.cget("state"))
    assert str(app.radio_offline.cget("state")) == "normal"

    # Test client instantiation via GUI helper
    dummy_key = "sk-deepseek-gui-test-key-555"
    app.api_key_var.set(dummy_key)
    client = app.get_deepseek_client()

    assert client.model == "deepseek-v4-pro"
    assert client.budget_controller is app.budget_controller
    # Secret must never be in repr or str
    assert dummy_key not in repr(client)
    assert dummy_key not in str(client)
    assert "[PROTECTED_AND_REDACTED]" in repr(client)

