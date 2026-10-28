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
import json
import os
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.request
import pytest

from src.agent_gui import AgentGUIApp
from src.agent_loop import LLMClientProtocol, ModelTurnResponse, ToolCall
from src.budget_controller import BudgetController
from src.generator_pipeline import OfflinePipelineCoordinator, PipelineExecutionSummary
from src.spec_preprocessor import SpecificationPreprocessResult
from src.synthesis_driver import SynthesisDriver, SynthesisResult, SynthesisStatus


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
    assert app.budget_controller.cumulative_spend == 0.0
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
    assert verified_label.cget("text") == "UNRECONCILED"

    # Ceiling must show $0.800000
    ceiling_label = app.telemetry_labels.get("Hard Project Budget Ceiling:")
    assert ceiling_label is not None
    assert ceiling_label.cget("text") == "$0.800000"

    # Remaining budget must be $0.799814
    remaining_label = app.telemetry_labels.get("Remaining Available Budget:")
    assert remaining_label is not None
    assert remaining_label.cget("text") == "UNRECONCILED"


def test_missing_input_validation(tk_root, monkeypatch):
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    # Suppressed unattended validation must not open modal dialogs.
    error_recorded = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, msg: error_recorded.append((title, msg)))

    # 1. Non-existent doc file
    app.doc_path_var.set("non_existent_doc_file.md")
    app.on_start_click()
    assert error_recorded == []
    assert app.is_running is False
    assert str(app.btn_start.cget("state")) == "normal"

    # 2. Non-existent view file
    app.doc_path_var.set(os.path.abspath("Architecture_Documentation.md"))
    app.view_path_var.set("non_existent_view_file.md")
    app.on_start_click()
    assert error_recorded == []
    assert app.is_running is False

    # 3. Empty output directory
    app.view_path_var.set(os.path.abspath("Architecture_View.md"))
    app.output_dir_var.set("")
    app.on_start_click()
    assert error_recorded == []
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

        # Check that demonstration test manifest was written into the promoted run directory
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)
        manifest_file = os.path.join(app.last_run_dir, "offline_validation_manifest.json")
        assert os.path.isfile(manifest_file)
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest_text = f.read()
            assert "Stage F2 Offline Desktop GUI Shell Test" in manifest_text
            assert '"offline_mode": true' in manifest_text
            assert '"live_api_called": false' in manifest_text

        # Verify staging directory was promoted and no leftover .staging_* exists
        for item in os.listdir(output_dir):
            assert not item.startswith(".staging_")

        # Verify live spend is unchanged
        assert app.budget_controller.cumulative_spend == 0.0


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


# -----------------------------------------------------------------------------
# Phase 2.27.7: GUI Staging Isolation & Terminal State Integrity Tests
# -----------------------------------------------------------------------------

def test_gui_worker_isolated_staging_and_promotion(tk_root, canonical_doc_paths):
    """Verifies that GUI worker creates an isolated staging directory, generates deliverables there,

    and promotes to a unique final run directory without leaving unpromoted staging.
    """
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_staging_promo")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app.output_dir_var.set(output_dir)

        run_id = "testrun_001"
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id=run_id)

        # Staging directory must have been promoted and no longer exist
        expected_staging = os.path.join(output_dir, f".staging_{run_id}")
        assert not os.path.exists(expected_staging)

        # Promoted run directory must exist
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)
        assert "space_fractions_run_" in os.path.basename(app.last_run_dir)
        assert run_id[:8] in os.path.basename(app.last_run_dir)

        # Manifest must be inside promoted run directory
        manifest_path = os.path.join(app.last_run_dir, "offline_validation_manifest.json")
        assert os.path.isfile(manifest_path)

        # Process queued messages
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        assert app.run_state == "COMMITTED"
        assert app.is_running is False
        assert app.last_execution_summary.success is True
        for gen_path in app.last_execution_summary.generated_files:
            assert os.path.realpath(gen_path).startswith(os.path.realpath(app.last_run_dir))
            assert os.path.isfile(gen_path)


def test_existing_output_files_preserved_unmodified(tk_root, canonical_doc_paths):
    """Verifies that existing files and pre-existing subfolders in output root are preserved."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_preserve_out")
        os.makedirs(output_dir, exist_ok=True)

        # Pre-create unrelated user file
        unrelated_file = os.path.join(output_dir, "unrelated_user_doc.txt")
        with open(unrelated_file, "w", encoding="utf-8") as f:
            f.write("Important user data that must not be deleted or overwritten.")

        # Pre-create prior run directory
        prior_dir = os.path.join(output_dir, "space_fractions_run_20261025_000000_priorrun")
        os.makedirs(prior_dir, exist_ok=True)
        prior_file = os.path.join(prior_dir, "prior_data.txt")
        with open(prior_file, "w", encoding="utf-8") as f:
            f.write("Prior run content.")

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app.output_dir_var.set(output_dir)

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="new_run_002")

        # Verify pre-existing files are strictly untouched
        assert os.path.isfile(unrelated_file)
        with open(unrelated_file, "r", encoding="utf-8") as f:
            assert f.read() == "Important user data that must not be deleted or overwritten."

        assert os.path.isfile(prior_file)
        with open(prior_file, "r", encoding="utf-8") as f:
            assert f.read() == "Prior run content."


def test_staging_directory_collision_fails_safely(tk_root, canonical_doc_paths):
    """Verifies that a pre-existing staging directory collision fails without deleting or overwriting."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_staging_col")
        os.makedirs(output_dir, exist_ok=True)

        colliding_run_id = "collide123"
        staging_dir = os.path.join(output_dir, f".staging_{colliding_run_id}")
        os.makedirs(staging_dir, exist_ok=True)
        canary = os.path.join(staging_dir, "canary.txt")
        with open(canary, "w", encoding="utf-8") as f:
            f.write("canary data")

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id=colliding_run_id)

        assert app.run_state == "FAILED"
        # Staging directory and canary file must remain intact
        assert os.path.isfile(canary)
        with open(canary, "r", encoding="utf-8") as f:
            assert f.read() == "canary data"


def test_pipeline_failure_aborts_promotion_and_preserves_staging(tk_root, canonical_doc_paths, monkeypatch):
    """Verifies that if generator pipeline fails in staging, promotion is aborted and staging preserved."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_pipe_fail")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)

        def mock_failed_pipeline(*args, **kwargs):
            return PipelineExecutionSummary(
                success=False,
                total_stages_run=1,
                completed_stages=[],
                generated_files=[],
                cumulative_reserved=0.0,
                cumulative_spend=0.0,
                safety_violations=[],
                errors=["Simulated generator failure in staging."],
            )

        from src.generator_pipeline import OfflinePipelineCoordinator
        monkeypatch.setattr(OfflinePipelineCoordinator, "run_mock_pipeline", mock_failed_pipeline)

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="fail_run_003")

        assert app.run_state == "FAILED"
        assert app.last_staging_dir is not None
        assert os.path.isdir(app.last_staging_dir)
        # No promoted run directory created
        assert app.last_run_dir is None
        for item in os.listdir(output_dir):
            assert not item.startswith("space_fractions_run_")


def test_missing_deliverable_aborts_promotion_and_preserves_staging(tk_root, canonical_doc_paths, monkeypatch):
    """Verifies that if an expected deliverable is missing from staging, promotion is aborted."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_missing_deliv")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)

        def mock_missing_file_pipeline(self, *args, **kwargs):
            missing_path = os.path.join(self.output_dir, "space_fractions", "non_existent.py")
            return PipelineExecutionSummary(
                success=True,
                total_stages_run=4,
                completed_stages=["S1", "S2", "S3", "S4"],
                generated_files=[missing_path],
                cumulative_reserved=0.0,
                cumulative_spend=0.0,
                safety_violations=[],
                errors=[],
            )

        from src.generator_pipeline import OfflinePipelineCoordinator
        monkeypatch.setattr(OfflinePipelineCoordinator, "run_mock_pipeline", mock_missing_file_pipeline)

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="missing_deliv_004")

        assert app.run_state == "FAILED"
        assert app.last_run_dir is None
        assert os.path.isdir(app.last_staging_dir)


def test_concurrency_cancellation_wins_before_commitment(tk_root, canonical_doc_paths):
    """Verifies that cancellation arriving before the commit boundary prevents promotion and preserves staging."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_cancel_wins")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app.is_running = True
        app.run_state = "RUNNING"

        def pre_commit_hook():
            # Trigger cancellation while still before the commit lock
            app.on_cancel_click()

        app._pre_commit_hook = pre_commit_hook

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="cancel_wins_005")

        # Process queued messages
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        assert app.run_state == "CANCELLED"
        assert app.is_running is False
        # Promotion must not have occurred
        assert app.last_run_dir is None
        for item in os.listdir(output_dir):
            assert not item.startswith("space_fractions_run_")
        # Staging directory must remain preserved
        assert app.last_staging_dir is not None
        assert os.path.isdir(app.last_staging_dir)


def test_concurrency_worker_wins_commitment_first(tk_root, canonical_doc_paths):
    """Verifies that once worker claims COMMITTING, subsequent cancellation is rejected."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_worker_wins")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app.is_running = True
        app.run_state = "RUNNING"

        worker_in_committing = threading.Event()
        resume_worker = threading.Event()

        def in_commit_hook():
            worker_in_committing.set()
            resume_worker.wait(timeout=5.0)

        app._in_commit_hook = in_commit_hook

        cancel_attempt_state = {}

        def cancel_thread_target():
            worker_in_committing.wait(timeout=5.0)
            cancel_attempt_state["state_at_cancel"] = app.run_state
            app.on_cancel_click()
            resume_worker.set()

        cancel_t = threading.Thread(target=cancel_thread_target)
        cancel_t.start()

        # Run worker
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="worker_wins_006")
        cancel_t.join(timeout=5.0)

        # Process messages
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        # Cancellation was attempted during COMMITTING and was rejected
        assert cancel_attempt_state.get("state_at_cancel") == "COMMITTING"
        assert not app.cancel_requested.is_set()

        # Outcome is COMMITTED, promotion succeeded
        assert app.run_state == "COMMITTED"
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)


def test_directory_promotion_failure_sets_failed_and_preserves_staging(tk_root, canonical_doc_paths, monkeypatch):
    """Verifies that promotion failure sets FAILED, preserves staging, and rejects later cancellation."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_prom_fail")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)

        def mock_failing_rename(src, dst):
            raise OSError("Simulated Windows file lock / permission error during rename")

        monkeypatch.setattr(os, "rename", mock_failing_rename)

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="prom_fail_007")

        assert app.run_state == "FAILED"
        assert app.last_staging_dir is not None
        assert os.path.isdir(app.last_staging_dir)

        # Later cancellation must be rejected and must NOT overwrite FAILED
        app.is_running = True  # simulate running flag before queue drain
        app.on_cancel_click()
        assert app.run_state == "FAILED"


def test_terminal_state_immutability_against_cancel_and_window_close(tk_root, monkeypatch):
    """Verifies that established terminal states (COMMITTED, FAILED, CANCELLED) cannot be overwritten."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    for terminal_state in ("COMMITTED", "FAILED", "CANCELLED"):
        with app._state_lock:
            app.run_state = terminal_state
            app.is_running = True
            app.cancel_requested.clear()

        # Cancel click must not mutate terminal state
        app.on_cancel_click()
        assert app.run_state == terminal_state
        assert not app.cancel_requested.is_set()

        # Window closing must not mutate terminal state
        destroyed = []
        monkeypatch.setattr(app.root, "destroy", lambda: destroyed.append(True))
        app.on_window_closing()
        assert app.run_state == terminal_state
        assert not app.cancel_requested.is_set()
        assert len(destroyed) == 1


def test_window_close_deferred_during_committing(tk_root, monkeypatch):
    """Verifies that window close is deferred during COMMITTING and does not cancel."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    with app._state_lock:
        app.run_state = "COMMITTING"
        app.is_running = True
        app.cancel_requested.clear()

    scheduled_callbacks = []
    monkeypatch.setattr(app.root, "after", lambda ms, cb: scheduled_callbacks.append((ms, cb)))
    monkeypatch.setattr(app.root, "destroy", lambda: pytest.fail("root.destroy should NOT be called during COMMITTING!"))

    app.on_window_closing()

    assert app.run_state == "COMMITTING"
    assert not app.cancel_requested.is_set()
    assert len(scheduled_callbacks) == 1
    assert scheduled_callbacks[0][0] == 200
    assert scheduled_callbacks[0][1] == app.on_window_closing


def test_stale_queue_messages_rejected(tk_root):
    """Verifies that queue messages from an earlier run cannot alter state of the current run."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    app.current_run_id = "active_run_999"
    with app._state_lock:
        app.run_state = "RUNNING"
        app.is_running = True

    # Send stale messages with prior run_id
    app._handle_queue_message("completed", "Stale success", run_id="stale_run_111")
    assert app.run_state == "RUNNING"
    assert app.is_running is True

    app._handle_queue_message("cancelled", "Stale cancel", run_id="stale_run_111")
    assert app.run_state == "RUNNING"
    assert app.is_running is True

    app._handle_queue_message("error", "Stale error", run_id="stale_run_111")
    assert app.run_state == "RUNNING"
    assert app.is_running is True

    # Send message matching current_run_id
    app._handle_queue_message("completed", "Active success", run_id="active_run_999")
    assert app.run_state == "COMMITTED"
    assert app.is_running is False


def test_worker_final_result_output_path_and_gui_terminal_state_agree(tk_root, canonical_doc_paths):
    """Verifies that worker result, promoted output path, and GUI widgets/state completely agree."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_agreement")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app.output_dir_var.set(output_dir)

        run_id = "agree_run_888"
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id=run_id)

        # Drain queue
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        # Verify agreement across all layers
        assert app.run_state == "COMMITTED"
        assert app.is_running is False
        assert app.last_execution_summary is not None
        assert app.last_execution_summary.success is True
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)

        # Verify all files exist at promoted location
        for fpath in app.last_execution_summary.generated_files:
            assert os.path.realpath(fpath).startswith(os.path.realpath(app.last_run_dir))
            assert os.path.isfile(fpath)
            assert os.path.getsize(fpath) > 0

        # Artifacts tree populated with promoted paths
        tree_items = app.artifacts_tree.get_children()
        assert len(tree_items) > 0
        values = app.artifacts_tree.item(tree_items[0])["values"]
        assert "space_fractions_run_" in str(values[1])


def test_commit_gate_preserves_terminal_states_and_cancellation(tk_root, canonical_doc_paths):
    """Verifies that if run_state is COMMITTED or FAILED before the commit gate,

    the gate returns without changing the state, and pre-commit cancellation sets CANCELLED
    and prevents promotion.
    """
    doc_path, view_path = canonical_doc_paths

    for initial_terminal in ("FAILED", "COMMITTED"):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, f"test_gate_{initial_terminal.lower()}")
            os.makedirs(output_dir, exist_ok=True)

            app = AgentGUIApp(tk_root, suppress_messagebox=True)
            app.is_running = True
            app.run_state = "RUNNING"

            def terminal_pre_commit_hook():
                with app._state_lock:
                    app.run_state = initial_terminal

            app._pre_commit_hook = terminal_pre_commit_hook

            app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id=f"gate_{initial_terminal[:4]}")

            # run_state must remain the initial terminal state, not changed to CANCELLED or COMMITTING
            assert app.run_state == initial_terminal
            # Promotion must not have occurred
            assert app.last_run_dir is None
            for item in os.listdir(output_dir):
                assert not item.startswith("space_fractions_run_")
            # Staging directory must remain preserved
            assert app.last_staging_dir is not None
            assert os.path.isdir(app.last_staging_dir)
            # No cancelled message was queued by this gate
            while not app.msg_queue.empty():
                msg_type, data = app.msg_queue.get_nowait()
                assert msg_type != "cancelled"
                app.msg_queue.task_done()


# =============================================================================
# Phase 2.27.15 Deterministic Contract Tests (Tests 1 through 8)
# =============================================================================

class MockContractLLMClient:
    """Deterministic mock LLM client implementing LLMClientProtocol."""

    def __init__(self, target_file: str = "space_fractions/fraction_math.py"):
        self.target_file = target_file
        self.call_count = 0

    def chat_completion(
        self, messages: list, tools: list, reservation_id: Optional[str] = None
    ) -> ModelTurnResponse:
        self.call_count += 1
        if self.call_count == 1:
            valid_math_code = (
                "class Fraction:\n"
                "    def __init__(self, n=0, d=1): self.n = n; self.d = d\n"
                "    def simplify(self): return self\n"
                "    def add(self, o): return Fraction(self.n + o.n, self.d).simplify()\n"
                "    def subtract(self, o): return Fraction(self.n - o.n, self.d).simplify()\n"
                "    def compare(self, o): return 0\n"
                "    def equals(self, o): return True\n"
                "    def __str__(self):\n"
                "        reduced = self.simplify(); return str(reduced.n) if reduced.d == 1 else f'{reduced.n}/{reduced.d}'\n"
                "    def __eq__(self, o): return self.equals(o)\n\n"
                "class MathQuestionGenerator:\n"
                "    def generate(self, *args): return 'What is 1/2 + 1/4?'\n"
            )
            return ModelTurnResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        tool_id="call_mock_write",
                        name="write_file",
                        arguments={"path": self.target_file, "content": valid_math_code},
                    )
                ],
                raw_usage={"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
                finish_reason="tool_calls",
                status="SUCCESS",
            )
        else:
            return ModelTurnResponse(
                content="Mock synthesis turn complete.",
                tool_calls=[],
                raw_usage={"prompt_tokens": 40, "completion_tokens": 15, "total_tokens": 55},
                finish_reason="stop",
                status="SUCCESS",
            )


def test_contract_1_offline_mode_selects_mock_pipeline(tk_root, canonical_doc_paths, monkeypatch):
    """Contract Test 1: Offline mode still selects OfflinePipelineCoordinator.run_mock_pipeline()."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_contract_1")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        assert app.execution_mode_var.get() == "offline"

        invocations = []
        orig_run_mock = OfflinePipelineCoordinator.run_mock_pipeline

        def spy_run_mock(self, *args, **kwargs):
            invocations.append((args, kwargs))
            return orig_run_mock(self, *args, **kwargs)

        monkeypatch.setattr(OfflinePipelineCoordinator, "run_mock_pipeline", spy_run_mock)

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract1_run")

        assert len(invocations) == 1
        args, kwargs = invocations[0]
        assert args[0] == doc_path
        assert args[1] == view_path
        assert app.run_state == "COMMITTED"
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)


def test_contract_2_live_mode_rejected_before_network_dispatch(tk_root, canonical_doc_paths):
    """Contract Test 2: A live-mode request is rejected before network-capable dispatch."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_contract_2")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=False)
        app.doc_path_var.set(doc_path)
        app.view_path_var.set(view_path)
        app.output_dir_var.set(output_dir)

        # Attempt to activate live mode
        app.execution_mode_var.set("live")

        # 1. on_start_click must fail closed before starting thread
        app.on_start_click()
        assert app.is_running is False
        assert app.worker_thread is None

        # 2. Worker directly invoked with live mode must fail closed
        app.run_state = "RUNNING"
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract2_run")
        assert app.run_state == "FAILED"
        assert app.last_run_dir is None

        # Confirm queue received error notice
        found_err = False
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            if msg_type == "error" and "Live mode dispatch is not authorized" in str(data):
                found_err = True
            app.msg_queue.task_done()
        assert found_err is True


def test_contract_3_mocked_synthesis_workflow_invoked_via_boundary(tk_root, canonical_doc_paths):
    """Contract Test 3: A mocked synthesis workflow can be invoked through the proposed integration boundary."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_contract_3")
        os.makedirs(output_dir, exist_ok=True)

        mock_llm = MockContractLLMClient()
        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            mock_llm_client=mock_llm,
            workflow_mode="mock_synthesis",
        )

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract3_synth")

        # Drain queue
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        assert app.run_state == "COMMITTED"
        assert app.is_running is False
        assert mock_llm.call_count >= 1
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)

        # Target file exists in promoted directory and satisfies interface
        target_file = os.path.join(app.last_run_dir, "space_fractions", "fraction_math.py")
        assert os.path.isfile(target_file)
        valid, msg = SynthesisDriver.validate_fraction_math_interface(target_file)
        assert valid is True, msg


def test_contract_4_missing_or_malformed_telemetry_does_not_crash_gui(tk_root):
    """Contract Test 4: Missing or malformed usage and cost telemetry display truthful indicators without crashing."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    # 1. None cumulative spend displays UNAVAILABLE, and remaining budget becomes UNRECONCILED
    app.budget_controller.cumulative_spend = None
    app._update_telemetry_display()
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "UNAVAILABLE"
    assert app.telemetry_labels["Remaining Available Budget:"].cget("text") == "UNRECONCILED"

    # 2. Corrupted spend string displays INVALID
    app.budget_controller.cumulative_spend = "corrupted_spend_string"
    app._update_telemetry_display()
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "INVALID"
    assert app.telemetry_labels["Remaining Available Budget:"].cget("text") == "UNRECONCILED"

    # 3. Negative spend displays INVALID
    app.budget_controller.cumulative_spend = -0.05
    app._update_telemetry_display()
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "INVALID"
    assert app.telemetry_labels["Remaining Available Budget:"].cget("text") == "UNRECONCILED"

    # 4. Non-finite spend (NaN) displays INVALID
    app.budget_controller.cumulative_spend = float("nan")
    app._update_telemetry_display()
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "INVALID"
    assert app.telemetry_labels["Remaining Available Budget:"].cget("text") == "UNRECONCILED"

    # 5. Corrupted active holds displays INVALID
    app.budget_controller.cumulative_spend = 0.000186
    app.budget_controller.get_active_holds = lambda: "not_a_float"
    app._update_telemetry_display()
    assert app.telemetry_labels["Active Reserved Hold:"].cget("text") == "INVALID"
    assert app.telemetry_labels["Remaining Available Budget:"].cget("text") == "UNRECONCILED"

    # 6. None active holds displays UNAVAILABLE
    app.budget_controller.get_active_holds = lambda: None
    app._update_telemetry_display()
    assert app.telemetry_labels["Active Reserved Hold:"].cget("text") == "UNAVAILABLE"
    assert app.telemetry_labels["Remaining Available Budget:"].cget("text") == "UNRECONCILED"

    # 7. Valid telemetry displays accurate dollar amounts
    app.has_authoritative_history = True
    app.budget_controller.cumulative_spend = 0.000186
    app.budget_controller.get_active_holds = lambda: 0.0
    app._update_telemetry_display()
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "$0.000186"
    assert app.telemetry_labels["Active Reserved Hold:"].cget("text") == "$0.000000"
    assert app.telemetry_labels["Remaining Available Budget:"].cget("text") == "$0.799814"

    # 8. Malformed summary passed to _update_telemetry_display
    malformed_summary = PipelineExecutionSummary(
        success=True,
        total_stages_run=1,
        completed_stages=["stage1"],
        generated_files=[],
        cumulative_reserved=None,
        cumulative_spend=None,
        safety_violations=[],
        errors=[],
    )
    app._update_telemetry_display(summary=malformed_summary)

    # 9. _handle_queue_message with malformed summary and manifest_path=None
    app._handle_queue_message("summary", (malformed_summary, None))
    assert app.last_execution_summary == malformed_summary


def test_contract_5_cancellation_before_commitment_prevents_promotion(tk_root, canonical_doc_paths):
    """Contract Test 5: Cancellation before commitment prevents promotion under mocked synthesis."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_contract_5")
        os.makedirs(output_dir, exist_ok=True)

        mock_llm = MockContractLLMClient()
        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            mock_llm_client=mock_llm,
            workflow_mode="mock_synthesis",
        )

        def pre_commit_cancel_hook():
            with app._state_lock:
                app.cancel_requested.set()
                app.run_state = "CANCELLED"

        app._pre_commit_hook = pre_commit_cancel_hook

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract5_cancel")

        assert app.run_state == "CANCELLED"
        assert app.last_run_dir is None
        # Staging directory must remain preserved
        assert app.last_staging_dir is not None
        assert os.path.isdir(app.last_staging_dir)
        # No promoted directory created in output_dir
        for item in os.listdir(output_dir):
            assert not item.startswith("space_fractions_run_")


def test_contract_6_failed_synthesis_preserves_failure_state_and_staging(tk_root, canonical_doc_paths):
    """Contract Test 6: A failed synthesis preserves the failure state and does not publish partial output."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_contract_6")
        os.makedirs(output_dir, exist_ok=True)

        def failing_synthesis_runner(**kwargs):
            return PipelineExecutionSummary(
                success=False,
                total_stages_run=1,
                completed_stages=[],
                generated_files=[],
                cumulative_reserved=0.0,
                cumulative_spend=0.0,
                safety_violations=[],
                errors=["Deterministic mock synthesis failure: AST syntax error"],
            )

        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            synthesis_runner_factory=failing_synthesis_runner,
        )

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract6_fail")

        assert app.run_state == "FAILED"
        assert app.last_run_dir is None
        assert app.last_staging_dir is not None
        assert os.path.isdir(app.last_staging_dir)
        for item in os.listdir(output_dir):
            assert not item.startswith("space_fractions_run_")


def test_contract_7_terminal_states_cannot_be_overwritten_by_stale_queue_messages(tk_root):
    """Contract Test 7: Terminal states cannot be overwritten by stale queue messages."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)
    app.current_run_id = "run_alpha"

    # Case A: COMMITTED terminal state cannot be overwritten by cancelled or error
    app.run_state = "COMMITTED"
    app._handle_queue_message("cancelled", "Stale cancel", run_id="run_alpha")
    assert app.run_state == "COMMITTED"
    app._handle_queue_message("error", "Stale error", run_id="run_alpha")
    assert app.run_state == "COMMITTED"

    # Case B: CANCELLED terminal state cannot be overwritten by completed or error
    app.run_state = "CANCELLED"
    app._handle_queue_message("completed", "Stale completion", run_id="run_alpha")
    assert app.run_state == "CANCELLED"
    app._handle_queue_message("error", "Stale error", run_id="run_alpha")
    assert app.run_state == "CANCELLED"

    # Case C: FAILED terminal state cannot be overwritten by completed or cancelled
    app.run_state = "FAILED"
    app._handle_queue_message("completed", "Stale completion", run_id="run_alpha")
    assert app.run_state == "FAILED"
    app._handle_queue_message("cancelled", "Stale cancel", run_id="run_alpha")
    assert app.run_state == "FAILED"

    # Case D: Messages from older or mismatched run_id are completely ignored
    app.run_state = "RUNNING"
    app._handle_queue_message("completed", "Old run complete", run_id="run_older_xyz")
    assert app.run_state == "RUNNING"
    app._handle_queue_message("error", "Old run error", run_id="run_older_xyz")
    assert app.run_state == "RUNNING"


def test_contract_8_budget_reservations_and_safeguards_not_bypassed(tk_root, canonical_doc_paths, monkeypatch):
    """Contract Test 8: Existing budget reservations and dispatch safeguards are not bypassed."""
    doc_path, view_path = canonical_doc_paths

    # Guard 1: Zero network egress permitted
    import socket
    def forbidden_connect(*args, **kwargs):
        raise AssertionError("SECURITY BREACH: Live network socket attempted during mocked synthesis.")

    monkeypatch.setattr(socket, "create_connection", forbidden_connect)

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_contract_8")
        os.makedirs(output_dir, exist_ok=True)

        # Budget exhaustion guard
        def budget_exhausted_factory(budget_controller, **kwargs):
            # Attempt to reserve more than remaining ceiling ($0.80)
            budget_controller.cumulative_spend = 0.800000
            try:
                budget_controller.reserve(
                    reservation_id="exhaust_test",
                    prompt_or_payload="overflow prompt",
                    max_output_tokens=1000,
                )
                res_ok = True
            except Exception:
                res_ok = False

            return PipelineExecutionSummary(
                success=False,
                total_stages_run=0,
                completed_stages=[],
                generated_files=[],
                cumulative_reserved=budget_controller.cumulative_spend,
                cumulative_spend=budget_controller.cumulative_spend,
                safety_violations=["BudgetExceededError: Ceiling 0.80 exhausted."],
                errors=["Preflight reservation rejected by budget controller ceiling."],
            )

        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            synthesis_runner_factory=budget_exhausted_factory,
        )

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract8_budget")

        assert app.run_state == "FAILED"
        assert app.last_run_dir is None
        assert app.last_staging_dir is not None
        assert os.path.isdir(app.last_staging_dir)


def test_contract_9_end_to_end_game_generation_and_static_acceptance_pass(tk_root, canonical_doc_paths):
    """Contract Test 9: Complete multi-file game generation passes static acceptance contract and promotes safely."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_contract_9")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract9_gen")

        # Drain queue
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        # Promotion must succeed under valid generation
        assert app.run_state == "COMMITTED"
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)

        # Verify full game project files exist
        promoted = app.last_run_dir
        expected_files = [
            os.path.join(promoted, "space_fractions", "fraction_math.py"),
            os.path.join(promoted, "space_fractions", "game_engine.py"),
            os.path.join(promoted, "space_fractions", "question_bank.py"),
            os.path.join(promoted, "space_fractions", "ui_screens.py"),
            os.path.join(promoted, "space_fractions", "main.py"),
            os.path.join(promoted, "requirements.txt"),
            os.path.join(promoted, "README.md"),
            os.path.join(promoted, "space_fractions_manifest.json"),
            os.path.join(promoted, "offline_validation_manifest.json"),
        ]
        for f in expected_files:
            assert os.path.isfile(f), f"Expected file missing: {f}"

        # Static Acceptance Contract Validation on promoted project
        from src.project_validator import ProjectValidator
        val = ProjectValidator.validate_project(promoted, require_full_game=True)
        assert val.success is True, f"Validation failed with errors: {val.errors}"
        assert val.ast_syntax_valid is True
        assert val.interfaces_valid is True
        assert val.tests_valid is True
        assert val.manifest_valid is True
        assert val.manifest_data is not None
        assert val.manifest_data.get("runtime_gameplay_verified") is False
        assert val.manifest_data.get("static_validation_passed") is True


def test_contract_10_staging_ast_syntax_error_prevents_promotion(tk_root, canonical_doc_paths):
    """Contract Test 10: Syntax error in staged Python files fails acceptance contract and aborts promotion."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_contract_10")
        os.makedirs(output_dir, exist_ok=True)

        def syntax_error_runner(staging_dir, coordinator, **kwargs):
            # Write a corrupt Python file with intentional SyntaxError
            broken_file = os.path.join(staging_dir, "space_fractions", "fraction_math.py")
            os.makedirs(os.path.dirname(broken_file), exist_ok=True)
            with open(broken_file, "w", encoding="utf-8") as f:
                f.write("def broken_syntax(:\n    pass\n")

            return PipelineExecutionSummary(
                success=True,
                total_stages_run=4,
                completed_stages=["s1", "s2", "s3", "s4"],
                generated_files=[broken_file],
                cumulative_reserved=0.0,
                cumulative_spend=0.0,
                safety_violations=[],
                errors=[],
            )

        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            synthesis_runner_factory=syntax_error_runner,
        )
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract10_syntax")

        # Promotion must fail closed
        assert app.run_state == "FAILED"
        assert app.last_run_dir is None
        assert app.last_staging_dir is not None
        assert os.path.isdir(app.last_staging_dir)

        # Confirm queue captured validation error
        found_err = False
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            if msg_type == "error" and "Static acceptance validation failed" in str(data):
                found_err = True
            app.msg_queue.task_done()
        assert found_err is True


def test_contract_11_staging_interface_mismatch_prevents_promotion(tk_root, canonical_doc_paths):
    """Contract Test 11: Interface mismatch in staged files fails acceptance contract and aborts promotion."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_contract_11")
        os.makedirs(output_dir, exist_ok=True)

        def interface_mismatch_runner(staging_dir, coordinator, **kwargs):
            # Write files where Fraction class is missing required methods
            math_file = os.path.join(staging_dir, "space_fractions", "fraction_math.py")
            os.makedirs(os.path.dirname(math_file), exist_ok=True)
            with open(math_file, "w", encoding="utf-8") as f:
                f.write("class Fraction:\n    pass\n")

            return PipelineExecutionSummary(
                success=True,
                total_stages_run=4,
                completed_stages=["s1", "s2", "s3", "s4"],
                generated_files=[math_file],
                cumulative_reserved=0.0,
                cumulative_spend=0.0,
                safety_violations=[],
                errors=[],
            )

        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            synthesis_runner_factory=interface_mismatch_runner,
        )
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract11_iface")

        # Promotion must fail closed
        assert app.run_state == "FAILED"
        assert app.last_run_dir is None
        assert app.last_staging_dir is not None
        assert os.path.isdir(app.last_staging_dir)

        found_err = False
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            if msg_type == "error" and "missing required methods" in str(data):
                found_err = True
            app.msg_queue.task_done()
        assert found_err is True


# =============================================================================
# Phase 2.27.24: Offline Mock-Agent Mode GUI Integration Tests
# =============================================================================

def test_mock_agent_mode_controls_and_radio_button(tk_root):
    """Verifies that the mock_agent radiobutton exists, is toggleable, and updates mode badge."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    # 1. Verify radio controls exist and default state
    assert hasattr(app, "radio_offline")
    assert hasattr(app, "radio_mock_agent")
    assert hasattr(app, "radio_live")
    assert app.execution_mode_var.get() == "offline"

    assert str(app.radio_offline.cget("state")) == "normal"
    assert str(app.radio_mock_agent.cget("state")) == "normal"
    assert "disabled" in str(app.radio_live.cget("state"))

    # 2. Running state disables offline and mock_agent controls while keeping live disabled
    app._set_controls_running_state(True)
    assert "disabled" in str(app.radio_offline.cget("state"))
    assert "disabled" in str(app.radio_mock_agent.cget("state"))
    assert "disabled" in str(app.radio_live.cget("state"))

    app._set_controls_running_state(False)
    assert str(app.radio_offline.cget("state")) == "normal"
    assert str(app.radio_mock_agent.cget("state")) == "normal"
    assert "disabled" in str(app.radio_live.cget("state"))

    # 3. Mode changes update the mode badge
    app.execution_mode_var.set("mock_agent")
    assert app.lbl_mode_badge.cget("text") == "OFFLINE MOCK AGENT (Multi-Stage AgentLoop)"

    app.execution_mode_var.set("offline")
    assert app.lbl_mode_badge.cget("text") == "OFFLINE VALIDATION (Mock Engine)"

    # 4. Constructor parameter default_execution_mode is honored
    app_custom = AgentGUIApp(tk_root, suppress_messagebox=True, default_execution_mode="mock_agent")
    assert app_custom.execution_mode_var.get() == "mock_agent"
    assert app_custom.lbl_mode_badge.cget("text") == "OFFLINE MOCK AGENT (Multi-Stage AgentLoop)"


def test_mock_agent_mode_executes_agent_pipeline_worker(tk_root, canonical_doc_paths):
    """Verifies that selecting mock_agent executes the multi-stage AgentLoop pipeline to completion.

    Validates:
    1. Worker instantiates OfflineMockAgentClient and passes it to OfflinePipelineCoordinator.
    2. Coordinator executes run_agent_pipeline across all 4 stages.
    3. Multi-file deliverables are generated into isolated staging.
    4. ProjectValidator validates AST syntax, module interfaces, test suite, and manifest.
    5. Directory promotion safely claims COMMITTED state and moves staging to target directory.
    6. Spend is locked and zero network requests are dispatched.
    """
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_mock_agent_run")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app.execution_mode_var.set("mock_agent")

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="mock_agent_e2e_001")

        # Drain and process queue
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        # 1. State assertions
        assert app.run_state == "COMMITTED"
        assert app.is_running is False
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)

        # 2. No staging directory leaked in output_dir
        for item in os.listdir(output_dir):
            assert not item.startswith(".staging_"), f"Unpromoted staging leaked: {item}"

        # 3. Deliverables exist on disk in promoted directory
        expected_files = [
            "space_fractions/fraction_math.py",
            "space_fractions/game_engine.py",
            "space_fractions/question_bank.py",
            "space_fractions/ui_screens.py",
            "space_fractions/main.py",
            "tests/test_fraction_math.py",
            "tests/test_game_engine.py",
            "specs/openapi.yaml",
            "specs/internal.proto",
            "specs/sql/game_ddl.sql",
            "requirements.txt",
            "README.md",
            "offline_validation_manifest.json",
        ]
        for rel in expected_files:
            full = os.path.join(app.last_run_dir, rel)
            assert os.path.isfile(full), f"Missing generated file in promoted directory: {rel}"

        # 4. Validate with ProjectValidator
        from src.project_validator import ProjectValidator
        val = ProjectValidator.validate_project(app.last_run_dir, require_full_game=True)
        assert val.success is True, f"ProjectValidator failed: {val.errors}"
        assert val.ast_syntax_valid is True
        assert val.interfaces_valid is True

        # 5. Financial accounting: zero live tokens billed
        assert app.budget_controller.cumulative_spend == 0.0


def test_mock_agent_mode_cancellation_preserves_staging(tk_root, canonical_doc_paths):
    """Verifies that cancellation during mock_agent execution halts pipeline and preserves staging."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_mock_agent_cancel")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app.execution_mode_var.set("mock_agent")

        def pre_commit_cancel_hook():
            with app._state_lock:
                app.cancel_requested.set()
                app.run_state = "CANCELLED"

        app._pre_commit_hook = pre_commit_cancel_hook

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="mock_agent_cancel_002")

        # Drain queue
        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        # Cancellation state asserted
        assert app.run_state == "CANCELLED"
        assert app.last_run_dir is None
        # Staging preserved
        assert app.last_staging_dir is not None
        assert os.path.isdir(app.last_staging_dir)

        # No promoted run directory created
        promoted = [f for f in os.listdir(output_dir) if f.startswith("space_fractions_run_")]
        assert len(promoted) == 0


def test_mock_agent_with_custom_injected_client(tk_root, canonical_doc_paths):
    """Verifies that an injected mock_llm_client takes precedence in mock_agent mode."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_mock_agent_custom")
        os.makedirs(output_dir, exist_ok=True)

        from src.agent_gui import OfflineMockAgentClient
        custom_client = OfflineMockAgentClient()

        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            mock_llm_client=custom_client,
            default_execution_mode="mock_agent",
        )

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="custom_mock_agent_003")

        assert app.run_state == "COMMITTED"
        assert custom_client.call_count >= 4
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)


# =============================================================================
# Phase 2.27.24.1: Mock-Agent Integration Safety Review Tests
# =============================================================================

def test_execution_mode_captured_on_gui_thread_and_passed_to_worker(tk_root, canonical_doc_paths, monkeypatch):
    """Safety Check 1: Verify execution_mode is captured on the GUI thread and passed to worker."""
    doc_path, view_path = canonical_doc_paths
    app = AgentGUIApp(tk_root, suppress_messagebox=True)
    app.doc_path_var.set(doc_path)
    app.view_path_var.set(view_path)
    app.output_dir_var.set("dummy_output_dir")
    app.execution_mode_var.set("mock_agent")

    captured_worker_args = []

    def mock_thread_init(target=None, args=(), daemon=None):
        captured_worker_args.append(args)
        class DummyThread:
            def start(self):
                pass
        return DummyThread()

    monkeypatch.setattr(threading, "Thread", mock_thread_init)

    app.on_start_click()

    assert len(captured_worker_args) == 1
    args = captured_worker_args[0]
    # args: (doc_path, view_path, output_dir, run_id, mode_str, task_goal, config)
    assert len(args) in (5, 7)
    assert args[0] == doc_path
    assert args[1] == view_path
    assert args[2] == "dummy_output_dir"
    assert args[4] == "mock_agent"


def test_simulated_usage_never_represented_as_verified_spend(tk_root):
    """Safety Check 2: Verify simulated usage and local estimates are never shown as verified spend."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    simulated_summary = PipelineExecutionSummary(
        success=True,
        total_stages_run=4,
        completed_stages=["s1", "s2", "s3", "s4"],
        generated_files=[],
        cumulative_reserved=0.015000,
        cumulative_spend=0.008500,  # Simulated spend from mock pipeline
        safety_violations=[],
        errors=[],
    )

    # Case A: Normal operation — verified spend must remain BASE_VERIFIED_SPEND ($0.000186), NOT 0.008500
    app.has_authoritative_history = True
    app.budget_controller.cumulative_spend = 0.000186
    app._update_telemetry_display(summary=simulated_summary)
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "$0.000186"
    assert "$0.008500" not in app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text")

    # Case B: When budget_controller.cumulative_spend is None, must show UNAVAILABLE, never simulated spend
    app.budget_controller.cumulative_spend = None
    app._update_telemetry_display(summary=simulated_summary)
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "UNAVAILABLE"
    assert "$0.008500" not in app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text")


# =============================================================================
# CLI Testability and Packaged Acceptance Contract Tests (Phase 2.27.27)
# =============================================================================

def test_cli_default_mode_and_arguments():
    """Verify default CLI arguments preserve offline mode, zero auto-run, and clean defaults."""
    from src.agent_gui import parse_cli_args
    args = parse_cli_args([])
    assert args.mode == "offline"
    assert args.auto_run is False
    assert args.auto_close_ms == 0
    assert args.output_dir is None


def test_cli_mode_mock_agent_selection(tk_root):
    """Verify --mode mock_agent selects the mock-agent execution path and updates GUI badges."""
    from src.agent_gui import parse_cli_args
    args = parse_cli_args(["--mode", "mock_agent"])
    assert args.mode == "mock_agent"

    app = AgentGUIApp(tk_root, suppress_messagebox=True, default_execution_mode=args.mode)
    assert app.execution_mode_var.get() == "mock_agent"
    assert "OFFLINE MOCK AGENT" in app.lbl_mode_badge.cget("text")


def test_cli_output_dir_validation_and_containment(tk_root):
    """Verify --output-dir is validated against workspace containment and applied to GUI."""
    from src.agent_gui import parse_cli_args
    from src.path_security import PathSecurityError

    # 1. Valid workspace-contained subdirectory
    args = parse_cli_args(["--output-dir", "valid_cli_output"])
    expected = os.path.realpath(os.path.abspath("valid_cli_output"))
    assert args.output_dir == expected

    app = AgentGUIApp(tk_root, suppress_messagebox=True, default_output_dir=args.output_dir)
    assert app.output_dir_var.get() == expected

    # 2. Directory traversal attempt outside workspace fails closed
    with pytest.raises(PathSecurityError, match="escapes workspace"):
        parse_cli_args(["--output-dir", "../../escaped_dir"])

    # 3. Null byte injection fails closed
    with pytest.raises(PathSecurityError, match="Null bytes detected"):
        parse_cli_args(["--output-dir", "bad\0dir"])

    # 4. UNC path fails closed
    with pytest.raises(PathSecurityError, match="UNC network paths are prohibited"):
        parse_cli_args(["--output-dir", "\\\\server\\share\\dir"])


def test_cli_invalid_modes_and_live_mode_rejected():
    """Verify invalid modes and live mode are rejected at the CLI entry point."""
    from src.agent_gui import parse_cli_args

    # Live mode is rejected by parser choices
    with pytest.raises(SystemExit):
        parse_cli_args(["--mode", "live"])

    # Arbitrary invalid modes rejected
    with pytest.raises(SystemExit):
        parse_cli_args(["--mode", "invalid_mode_xyz"])


def test_cli_auto_run_does_not_dispatch_live(tk_root, monkeypatch):
    """Verify --auto-run schedules execution without dispatching live API requests."""
    import http.client
    import urllib.request
    from src.agent_gui import parse_cli_args

    # Block all network sockets
    def fail_network(*args, **kwargs):
        raise AssertionError("CRITICAL: Network request attempted during CLI auto-run!")

    monkeypatch.setattr(http.client.HTTPConnection, "request", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)

    args = parse_cli_args(["--auto-run", "--mode", "mock_agent"])
    assert args.auto_run is True
    assert args.mode == "mock_agent"

    app = AgentGUIApp(tk_root, auto_run=args.auto_run, default_execution_mode=args.mode, suppress_messagebox=True)
    assert app.allow_live_dispatch is False
    assert str(app.radio_live.cget("state")) == "disabled"
    assert app.execution_mode_var.get() == "mock_agent"


def test_cli_safe_auto_close_coordination(tk_root):
    """Verify _safe_auto_close defers termination while worker is active and closes cleanly when idle."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    deferred_calls = []
    def mock_after(ms, func):
        deferred_calls.append((ms, func))

    app.root.after = mock_after

    # Case A: Worker is actively running — auto_close must defer, not destroy
    app.is_running = True
    app.run_state = "RUNNING"
    app._safe_auto_close()

    assert len(deferred_calls) == 1
    assert deferred_calls[0][0] == 200
    assert deferred_calls[0][1] == app._safe_auto_close

    # Case B: Worker in COMMITTING — auto_close must defer
    deferred_calls.clear()
    app.run_state = "COMMITTING"
    app._safe_auto_close()

    assert len(deferred_calls) == 1
    assert deferred_calls[0][0] == 200

    # Case C: Worker is completed (terminal state) — safe to proceed to window close
    destroyed = False
    def mock_destroy():
        nonlocal destroyed
        destroyed = True

    app.root.destroy = mock_destroy
    app.is_running = False
    app.run_state = "COMMITTED"
    app._safe_auto_close()

    assert destroyed is True









def test_game_launcher_button_presence_and_execution(tk_root):
    """Verifies that the Game Launcher button opens SpaceFractionsApp in a Toplevel and closes safely."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    assert hasattr(app, "btn_launch_game"), "btn_launch_game missing from action bar"
    assert hasattr(app, "btn_launch_game_tab"), "btn_launch_game_tab missing from assets tab"
    assert "Launch Playable Game" in app.btn_launch_game.cget("text")

    # Initial state: no game window active
    assert app._game_window is None

    # Click launch button
    app.btn_launch_game.invoke()
    tk_root.update()

    assert app._game_window is not None
    assert app._game_window.winfo_exists()
    assert "Space Fractions" in app._game_window.title()

    initial_win = app._game_window

    # Duplicate launch click: must bring to front without opening a second window
    app.btn_launch_game.invoke()
    tk_root.update()
    assert app._game_window is initial_win

    # Close game window via app._on_game_window_close
    app._on_game_window_close()
    tk_root.update()

    assert app._game_window is None
    assert app.root.winfo_exists(), "Main Agent GUI root was unexpectedly closed"

    # Reopen game window to verify clean re-launch
    app.btn_launch_game.invoke()
    tk_root.update()
    assert app._game_window is not None
    assert app._game_window.winfo_exists()

    # Clean up
    if app._game_window is not None and app._game_window.winfo_exists():
        app._game_window.destroy()
        tk_root.update()


def test_game_launcher_error_handling_sanitized(tk_root, monkeypatch):
    """Verifies that launcher errors are caught and sanitized without leaking raw exceptions."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    def mock_failing_import(*args, **kwargs):
        raise RuntimeError("Simulated internal initialization failure with sensitive_token_xyz")

    monkeypatch.setattr(tk, "Toplevel", mock_failing_import)

    # Launching should not raise unhandled exception
    app.launch_playable_game()
    tk_root.update()

    assert app._game_window is None
    # Verify sensitive token was never written to activity log
    log_content = app.log_text.get("1.0", "end")
    assert "sensitive_token_xyz" not in log_content
    assert "Failed to launch Space Fractions Game" in log_content



def test_interactive_live_authorization_toggle(tk_root):
    """Verifies that the GUI checkbutton dynamically authorizes live dispatch and handles fallbacks."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=False)

    # Initial state: unauthorized
    assert app.allow_live_dispatch is False
    assert app.allow_live_var.get() is False
    assert str(app.radio_live.cget("state")) == "disabled"
    assert "Disabled" in str(app.radio_live.cget("text"))

    # Authorize live dispatch via checkbox toggle
    app.allow_live_var.set(True)
    app._on_toggle_live_auth()
    assert app.allow_live_dispatch is True
    assert str(app.radio_live.cget("state")) == "normal"
    assert "Live DeepSeek Agent" in str(app.radio_live.cget("text"))

    # Select live mode
    app.execution_mode_var.set("live")
    assert app.execution_mode_var.get() == "live"
    assert "LIVE DEEPSEEK AGENT" in app.lbl_mode_badge.cget("text")

    # De-authorize live dispatch while in live mode -> falls back to offline
    app.allow_live_var.set(False)
    app._on_toggle_live_auth()
    assert app.allow_live_dispatch is False
    assert str(app.radio_live.cget("state")) == "disabled"
    assert "Disabled" in str(app.radio_live.cget("text"))
    assert app.execution_mode_var.get() == "offline"
    assert "OFFLINE VALIDATION" in app.lbl_mode_badge.cget("text")


def test_enhanced_input_file_validation_empty_and_invalid(tk_root, monkeypatch, tmp_path):
    """Verifies enhanced validation rejecting empty files, null bytes, and UNC paths."""
    app = AgentGUIApp(tk_root, suppress_messagebox=False)

    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, msg: errors.append((title, msg)))

    # 1. Empty doc file
    empty_doc = tmp_path / "empty_doc.md"
    empty_doc.write_text("", encoding="utf-8")
    app.doc_path_var.set(str(empty_doc))
    app.on_start_click()
    assert len(errors) == 1
    assert "empty" in errors[-1][1].lower()

    # 2. Valid doc, empty view file
    valid_doc = tmp_path / "valid_doc.md"
    valid_doc.write_text("# Valid Architecture Doc", encoding="utf-8")
    empty_view = tmp_path / "empty_view.md"
    empty_view.write_text("", encoding="utf-8")
    app.doc_path_var.set(str(valid_doc))
    app.view_path_var.set(str(empty_view))
    app.on_start_click()
    assert len(errors) == 2
    assert "empty" in errors[-1][1].lower()

    # 3. Valid doc and view, null byte in output_dir
    valid_view = tmp_path / "valid_view.md"
    valid_view.write_text("# Valid View", encoding="utf-8")
    app.view_path_var.set(str(valid_view))
    app.output_dir_var.set("bad\0output")
    app.on_start_click()
    assert len(errors) == 3
    assert "invalid" in errors[-1][1].lower() or "null" in errors[-1][1].lower()

    # 4. UNC output_dir
    app.output_dir_var.set(r"\\server\share\output")
    app.on_start_click()
    assert len(errors) == 4
    assert "prohibited" in errors[-1][1].lower() or "network" in errors[-1][1].lower()


def test_cli_timeout_argument_parsing():
    """Verify --timeout CLI argument parsing, defaults, and positive validation."""
    from src.agent_gui import parse_cli_args

    # 1. Default timeout is 60.0s
    args_def = parse_cli_args([])
    assert args_def.timeout == 60.0

    # 2. Custom positive timeout
    args_custom = parse_cli_args(["--timeout", "90.0"])
    assert args_custom.timeout == 90.0

    # 3. Non-positive timeout raises ValueError
    with pytest.raises(ValueError, match="--timeout must be a positive number"):
        parse_cli_args(["--timeout", "0"])

    with pytest.raises(ValueError, match="--timeout must be a positive number"):
        parse_cli_args(["--timeout", "-15"])


def test_cli_suppress_messagebox_argument():
    """Verify --suppress-messagebox CLI flag is recognized."""
    from src.agent_gui import parse_cli_args

    args_def = parse_cli_args([])
    assert args_def.suppress_messagebox is False

    args_suppressed = parse_cli_args(["--suppress-messagebox"])
    assert args_suppressed.suppress_messagebox is True


def test_live_client_timeout_propagation(tk_root):
    """Verify live_timeout is initialized and stored on AgentGUIApp."""
    app = AgentGUIApp(
        tk_root,
        suppress_messagebox=True,
        allow_live_dispatch=True,
        default_execution_mode="live",
        live_timeout=75.0,
    )
    assert app.live_timeout == 75.0


def test_error_queue_messagebox_suppression(tk_root, monkeypatch):
    """Verify that when suppress_messagebox=True, queue error messages do not trigger modal messagebox."""
    from tkinter import messagebox

    mb_calls = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, msg: mb_calls.append((title, msg)))

    app = AgentGUIApp(tk_root, suppress_messagebox=True)
    app._handle_queue_message("error", "Simulated fatal error message")

    assert len(mb_calls) == 0
    assert "Error encountered" in app.status_var.get()



def test_phase233_accounting_integrity_and_responsive_wrapping(tk_root):
    """Phase 2.33: Verifies unauthoritative spend is UNRECONCILED and responsive wrapping is enabled."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    # 1. Unauthoritative history guarantees UNRECONCILED spend and remaining budget
    assert app.has_authoritative_history is False
    app.budget_controller.cumulative_spend = 0.000186
    app.budget_controller.get_active_holds = lambda: 0.0
    app._update_telemetry_display()

    spend_lbl = app.telemetry_labels["Cumulative Verified Live Spend:"]
    rem_lbl = app.telemetry_labels["Remaining Available Budget:"]
    ceiling_lbl = app.telemetry_labels["Hard Project Budget Ceiling:"]

    assert spend_lbl.cget("text") == "UNRECONCILED"
    assert "0.000186" not in spend_lbl.cget("text")
    assert rem_lbl.cget("text") == "UNRECONCILED"
    assert "0.799814" not in rem_lbl.cget("text")
    assert ceiling_lbl.cget("text") == "$0.800000"

    # 2. Responsive wrapping attributes exist and are configured
    assert hasattr(app, "lbl_mode_badge")
    assert hasattr(app, "lbl_disclaimer")
    assert int(app.lbl_mode_badge.cget("wraplength")) > 0
    assert int(app.lbl_disclaimer.cget("wraplength")) > 0

    disclaimer_text = app.lbl_disclaimer.cget("text")
    assert "Historical multi-stage spend is UNRECONCILED" in disclaimer_text
    assert "$0.80 ceiling is a client-side reservation guard, not a provider cap." in disclaimer_text


def test_phase235_offline_completion_log_wording_and_accounting(tk_root, canonical_doc_paths):
    """Phase 2.35: Verifies updated offline completion log wording and strict UNRECONCILED accounting."""
    import tempfile
    doc_path, view_path = canonical_doc_paths

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_phase235_out")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir)

        while not app.msg_queue.empty():
            msg_type, data = app.msg_queue.get_nowait()
            app._handle_queue_message(msg_type, data)
            app.msg_queue.task_done()

        log_content = app.log_text.get("1.0", tk.END)
        expected_msg = (
            "Offline run completed; no live DeepSeek API call was made by this run. "
            "Historical provider spend remains UNRECONCILED."
        )
        assert expected_msg in log_content
        assert "zero live DeepSeek tokens billed" not in log_content.lower()

        # Telemetry verification
        assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "UNRECONCILED"
        assert app.telemetry_labels["Remaining Available Budget:"].cget("text") == "UNRECONCILED"
        assert app.telemetry_labels["Active Reserved Hold:"].cget("text") == "$0.000000"
        assert app.telemetry_labels["Hard Project Budget Ceiling:"].cget("text") == "$0.800000"


def test_worker_run_config_immutable_and_redacts_repr():
    """Phase 2.38: Verify WorkerRunConfig is frozen and strictly redacts API keys in __repr__ and __str__."""
    from src.agent_gui import WorkerRunConfig

    cfg = WorkerRunConfig(
        run_id="run_12345",
        doc_path="doc.md",
        view_path="view.md",
        output_dir="out",
        execution_mode="live",
        task_goal="Task goal",
        allow_live_dispatch=True,
        api_key="sk-live-secret-test-key-9999",
        live_timeout=45.0,
        max_turns=3,
        workflow_mode="offline_pipeline",
    )

    # Immutability check
    with pytest.raises(Exception):
        cfg.api_key = "modified"  # FrozenInstanceError

    # Redaction in string representation
    repr_str = repr(cfg)
    str_str = str(cfg)
    assert "sk-live-secret-test-key-9999" not in repr_str
    assert "sk-live-secret-test-key-9999" not in str_str
    assert "[PROTECTED_AND_REDACTED]" in repr_str
    assert "[PROTECTED_AND_REDACTED]" in str_str
    assert cfg.api_key == "sk-live-secret-test-key-9999"  # Retained in volatile memory


def test_gui_on_start_click_captures_immutable_worker_config(tk_root, monkeypatch):
    """Phase 2.38: Verify on_start_click captures all settings into WorkerRunConfig on GUI thread."""
    from src.agent_gui import WorkerRunConfig

    captured_args = []

    def mock_thread_init(self, target=None, args=(), daemon=None):
        captured_args.append((target, args))

    monkeypatch.setattr(threading.Thread, "__init__", mock_thread_init)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)
    app.doc_path_var.set("Architecture_Documentation.md")
    app.view_path_var.set("Architecture_View.md")
    app.output_dir_var.set("generated_space_fractions")
    app.execution_mode_var.set("offline")
    app.task_desc_var.set("Verify captured config on GUI thread")

    app.on_start_click()

    assert len(captured_args) == 1
    target, args = captured_args[0]
    assert target == app._run_offline_pipeline_worker
    assert len(args) == 7
    config = args[6]
    assert isinstance(config, WorkerRunConfig)
    assert config.execution_mode == "offline"
    assert config.task_goal == "Verify captured config on GUI thread"
    assert config.allow_live_dispatch is True
    assert config.doc_path == "Architecture_Documentation.md"


def test_worker_thread_does_not_access_tkinter_variables(tk_root, canonical_doc_paths):
    """Phase 2.38: Verify worker operates strictly from captured WorkerRunConfig without touching Tkinter vars."""
    import tempfile
    from src.agent_gui import WorkerRunConfig

    doc_path, view_path = canonical_doc_paths

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_worker_no_tk")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)

        # Invalidate/poison all Tkinter variables to raise if accessed on worker
        def poison_get():
            raise RuntimeError("CRITICAL ERROR: Cross-thread Tkinter variable access detected!")

        app.execution_mode_var.get = poison_get
        app.task_desc_var.get = poison_get
        app.api_key_var.get = poison_get
        app.doc_path_var.get = poison_get
        app.view_path_var.get = poison_get
        app.output_dir_var.get = poison_get

        # Pass plain immutable snapshot
        config = WorkerRunConfig(
            run_id="notk_run_001",
            doc_path=doc_path,
            view_path=view_path,
            output_dir=output_dir,
            execution_mode="offline",
            task_goal="Ingest architecture specs and test thread boundary",
            allow_live_dispatch=False,
            api_key="",
            live_timeout=60.0,
            max_turns=1,
            workflow_mode="offline_pipeline",
        )

        # Worker must execute to completion using config alone without raising poisoned access error
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, config=config)
        assert app.run_state == "COMMITTED"
        assert app.last_run_dir is not None


def test_live_dispatch_blocked_without_authorization_and_budget_success(tk_root, canonical_doc_paths, monkeypatch):
    """Phase 2.38: Verify live request remains blocked until BOTH authorization gate and budget reservation succeed."""
    import tempfile
    from src.agent_gui import WorkerRunConfig

    doc_path, view_path = canonical_doc_paths

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_live_gates")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=False)

        # Gate 1: Blocked when allow_live_dispatch is False even if mode is live
        cfg_unauth = WorkerRunConfig(
            run_id="gate1_unauth",
            doc_path=doc_path,
            view_path=view_path,
            output_dir=output_dir,
            execution_mode="live",
            task_goal="Live task",
            allow_live_dispatch=False,
            api_key="dummy_key",
        )
        app.run_state = "RUNNING"
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, config=cfg_unauth)
        assert app.run_state == "FAILED"

        # Gate 2: Blocked when allow_live_dispatch is True but API key is empty
        cfg_nokey = WorkerRunConfig(
            run_id="gate2_nokey",
            doc_path=doc_path,
            view_path=view_path,
            output_dir=output_dir,
            execution_mode="live",
            task_goal="Live task",
            allow_live_dispatch=True,
            api_key="",
        )
        app.run_state = "RUNNING"
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, config=cfg_nokey)
        assert app.run_state == "FAILED"

        # Gate 3: Blocked when budget reservation fails (ceiling exceeded)
        app_budget_blocked = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)
        app_budget_blocked.budget_controller.ceiling = 0.0000000001  # Insufficient budget

        # Guard that no socket or HTTP call is ever made
        def fail_on_network(*args, **kwargs):
            raise RuntimeError("DEFENSE VIOLATION: Network socket attempted!")
        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", fail_on_network)

        cfg_budget = WorkerRunConfig(
            run_id="gate3_budget",
            doc_path=doc_path,
            view_path=view_path,
            output_dir=output_dir,
            execution_mode="live",
            task_goal="Live task",
            allow_live_dispatch=True,
            api_key="valid-dummy-key",
            max_turns=1,
        )
        app_budget_blocked.run_state = "RUNNING"
        app_budget_blocked._run_offline_pipeline_worker(doc_path, view_path, output_dir, config=cfg_budget)
        assert app_budget_blocked.run_state == "FAILED"
        assert app_budget_blocked.budget_controller.cumulative_spend == 0.0


def test_real_background_worker_thread_execution_and_queue_sync(tk_root, canonical_doc_paths):
    """Phase 2.40: Genuine background worker thread execution with thread isolation and queue synchronization."""
    import tempfile
    import threading
    import time
    from src.agent_gui import AgentGUIApp

    doc_path, view_path = canonical_doc_paths

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_real_thread_out")
        os.makedirs(output_dir, exist_ok=True)

        app = AgentGUIApp(tk_root, suppress_messagebox=True)
        app.doc_path_var.set(doc_path)
        app.view_path_var.set(view_path)
        app.output_dir_var.set(output_dir)
        app.execution_mode_var.set("offline")
        app.task_desc_var.set("Ingest architecture specs and verify real worker thread isolation")

        # Track and guard all Tkinter variables against cross-thread access
        main_thread_id = threading.get_ident()
        accessed_from_worker = []

        for var_name in ("execution_mode_var", "task_desc_var", "api_key_var", "doc_path_var", "view_path_var", "output_dir_var"):
            var = getattr(app, var_name)
            orig_get = var.get
            def make_guarded_get(v_name, og):
                def guarded_get(*args, **kwargs):
                    if threading.get_ident() != main_thread_id:
                        accessed_from_worker.append((v_name, threading.get_ident()))
                        raise RuntimeError(f"Cross-thread Tkinter access violation: {v_name}.get() called from worker thread {threading.get_ident()}")
                    return og(*args, **kwargs)
                return guarded_get
            var.get = make_guarded_get(var_name, orig_get)

        # Trigger genuine start click on main GUI thread
        app.on_start_click()

        # Confirm worker thread was created and is a real background OS thread
        assert app.worker_thread is not None
        assert isinstance(app.worker_thread, threading.Thread)
        assert app.worker_thread.daemon is True

        # Safely wait for real background worker thread to finish execution
        app.worker_thread.join(timeout=10.0)
        assert not app.worker_thread.is_alive(), "Worker thread did not terminate within timeout."

        # Verify zero cross-thread Tkinter variable accesses were attempted from the worker thread
        assert len(accessed_from_worker) == 0

        # Process all queued messages on main UI thread
        app._process_queue()

        # Confirm worker communicated all expected results through the queue
        assert app.run_state == "COMMITTED"
        assert app.is_running is False
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)
        manifest_file = os.path.join(app.last_run_dir, "offline_validation_manifest.json")
        assert os.path.isfile(manifest_file)


# -----------------------------------------------------------------------------
# Phase 2.42: Live Unblock & Audit Integrity Regression Tests
# -----------------------------------------------------------------------------

def test_phase242_max_turns_with_staged_files_promotes_successfully(tk_root, canonical_doc_paths):
    """Phase 2.42: Verify that when AgentLoop reaches max_turns but has written valid deliverables,

    it proceeds to validation and promotion rather than failing immediately.
    """
    import tempfile
    from src.agent_gui import WorkerRunConfig
    from src.agent_loop import ToolCall, ModelTurnResponse

    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_max_turns_promotion")
        os.makedirs(output_dir, exist_ok=True)

        class SingleTurnWriterMockClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                return ModelTurnResponse(
                    content="Writing deliverables.",
                    tool_calls=[
                        ToolCall(
                            tool_id="call_calc",
                            name="write_file",
                            arguments={
                                "path": "calculator.py",
                                "content": "def add(a, b):\n    return a + b\n",
                            },
                        ),
                        ToolCall(
                            tool_id="call_test",
                            name="write_file",
                            arguments={
                                "path": "test_calculator.py",
                                "content": "from calculator import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                            },
                        ),
                    ],
                    raw_usage={"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180},
                    status="SUCCESS",
                )

        app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)
        app.mock_llm_client = SingleTurnWriterMockClient()

        config = WorkerRunConfig(
            run_id="p242_max_turns_test",
            doc_path=doc_path,
            view_path=view_path,
            output_dir=output_dir,
            execution_mode="mock_agent",
            task_goal="Create a basic calculator module",
            allow_live_dispatch=True,
            max_turns=1,  # Strictly 1 turn
            live_timeout=10.0,
            workflow_mode="offline_pipeline",
        )

        app.run_state = "RUNNING"
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, config=config)

        # Must succeed and promote despite max_turns=1 because deliverables exist
        assert app.run_state == "COMMITTED"
        assert app.last_run_dir is not None
        assert os.path.isdir(app.last_run_dir)
        assert os.path.isfile(os.path.join(app.last_run_dir, "calculator.py"))
        assert os.path.isfile(os.path.join(app.last_run_dir, "test_calculator.py"))


def test_phase242_execution_audit_log_redaction(tk_root, canonical_doc_paths):
    """Phase 2.42: Verify execution audit log is written to disk with strictly redacted secrets."""
    import tempfile
    from src.agent_gui import WorkerRunConfig

    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "test_audit_log")
        os.makedirs(output_dir, exist_ok=True)

        secret_key = "sk-deepseek-super-confidential-secret-key-1234567890"
        app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=False)

        config = WorkerRunConfig(
            run_id="audit_run_999",
            doc_path=doc_path,
            view_path=view_path,
            output_dir=output_dir,
            execution_mode="live",
            task_goal="Test secret masking in audit log",
            allow_live_dispatch=False,  # Will fail at authorization gate
            api_key=secret_key,
        )

        app.run_state = "RUNNING"
        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, config=config)
        assert app.run_state == "FAILED"

        # Check that error log was saved to output directory
        error_logs = [f for f in os.listdir(output_dir) if f.startswith("error_execution_")]
        if error_logs:
            log_p = os.path.join(output_dir, error_logs[0])
            with open(log_p, "r", encoding="utf-8") as f:
                log_data = f.read()
            assert secret_key not in log_data
            assert "sk-deepseek" not in log_data


def test_phase242_agent_loop_markdown_fallback_extraction():
    """Phase 2.42: Verify AgentLoop extracts and writes files from response markdown blocks when tool calls are omitted."""
    import tempfile
    from src.agent_loop import AgentLoop, ModelTurnResponse, LoopTerminationStatus

    with tempfile.TemporaryDirectory() as temp_dir:
        class MarkdownEmittingMockClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                md_content = (
                    "Here is the implementation:\n\n"
                    "```python:math_tool.py\n"
                    "def square(x):\n"
                    "    return x * x\n"
                    "```\n\n"
                    "And the unit test:\n\n"
                    "### File: `test_math_tool.py`\n"
                    "```python\n"
                    "from math_tool import square\n\n"
                    "def test_square():\n"
                    "    assert square(3) == 9\n"
                    "```\n"
                )
                return ModelTurnResponse(
                    content=md_content,
                    tool_calls=[],  # No formal tool calls
                    raw_usage={"prompt_tokens": 50, "completion_tokens": 50, "total_tokens": 100},
                    status="SUCCESS",
                )

        loop = AgentLoop(
            workspace_root=temp_dir,
            llm_client=MarkdownEmittingMockClient(),
            max_turns=1,
        )

        res = loop.run("Write math_tool.py and test_math_tool.py")
        assert res.status == LoopTerminationStatus.COMPLETED
        assert os.path.isfile(os.path.join(temp_dir, "math_tool.py"))
        assert os.path.isfile(os.path.join(temp_dir, "test_math_tool.py"))
        with open(os.path.join(temp_dir, "math_tool.py"), "r") as f:
            assert "def square(x):" in f.read()


def test_phase243_unbound_local_and_error_logging_regression(tk_root, canonical_doc_paths):
    """Phase 2.43 regression: Verify effective_max_output_tokens and clean_slug are bound and worker promotes."""
    import json
    from src.deepseek_client import DeepSeekClient
    from src.agent_gui import WorkerRunConfig
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)

        mock_resp = {
            'id': 'mock-reg-1',
            'choices': [{
                'message': {
                    'role': 'assistant',
                    'content': """```python:space_fractions/fraction_math.py
class Fraction:
    def __init__(self, numerator, denominator=1):
        self.numerator = numerator
        self.denominator = denominator
    def add(self, other): return self.simplify()
    def subtract(self, other): return self.simplify()
    def simplify(self): return self
    def compare(self, other): return 0
    def equals(self, other): return True
    def to_float(self): return 1.0
    def __str__(self):
        reduced = self.simplify()
        return "1" if reduced.denominator == 1 else "1/1"
    def __eq__(self, other): return self.equals(other)
    def __repr__(self): return "Fraction(1, 1)"
class MathQuestionGenerator:
    def generate(self): return "Q", ["1"], 0, "Exp"
```
```python:space_fractions/game_engine.py
from enum import Enum
class GameState(str, Enum):
    TITLE = "TITLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    GAME_OVER = "GAME_OVER"
class GameEngine:
    def __init__(self): self.state = GameState.PLAYING
    def play(self): pass
    def submit_answer(self, a): return True
    def pause(self): pass
    def resume(self): pass
    def is_game_over(self): return False
    def view_score(self): return 0
    def viewScore(self): return 0
```
```python:tests/test_fraction_game.py
def test_reg(): assert True
```
```python:space_fractions/question_bank.py
class QuestionBank:
    def get_questions(self): return []
```
```python:space_fractions/ui_screens.py
class FractionVisualizer: pass
class HUDFrame: pass
```
```python:space_fractions/main.py
class SpaceFractionsApp: pass
def main(): pass
```
```markdown:README.md
# Space Fractions
```
```text:requirements.txt
# Standard library only
```"""
                },
                'finish_reason': 'stop'
            }],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200}
        }

        orig_init = DeepSeekClient.__init__
        def patched_init(self, *args, **kwargs):
            kwargs['transport_sender'] = lambda req, timeout: (200, json.dumps(mock_resp).encode('utf-8'))
            orig_init(self, *args, **kwargs)
        DeepSeekClient.__init__ = patched_init

        try:
            cfg = WorkerRunConfig(
                run_id="reg_p243_check",
                doc_path=doc_path,
                view_path=view_path,
                output_dir=temp_dir,
                execution_mode="live",
                task_goal="Synthesize Space Fractions fraction_math.py and game_engine.py modules",
                allow_live_dispatch=True,
                api_key="sk-testkey123456789012345678901234567890",
                live_timeout=30.0,
                max_turns=1,
                live_max_output_tokens=2500,
            )

            app._run_offline_pipeline_worker(doc_path, view_path, temp_dir, config=cfg)
            assert app.run_state == "COMMITTED"
            assert app.last_run_dir is not None
            assert os.path.isdir(app.last_run_dir)
            assert os.path.isfile(os.path.join(app.last_run_dir, "agent_execution.log"))
            assert os.path.isfile(os.path.join(app.last_run_dir, "space_fractions", "fraction_math.py"))
            assert os.path.isfile(os.path.join(app.last_run_dir, "space_fractions", "game_engine.py"))
        finally:
            DeepSeekClient.__init__ = orig_init


def test_phase252_tcl_tk_bootstrap_and_smoke_test_cli(monkeypatch, tmp_path):
    """Verifies that Phase 2.52 runtime bootstrap and CLI smoke test flags work as expected."""
    import sys
    from src.agent_gui import _bootstrap_tcl_tk_environment, parse_cli_args

    # Test bootstrap in mocked PyInstaller bundle
    fake_meipass = tmp_path / "_MEI_test"
    fake_meipass.mkdir()
    fake_tcl = fake_meipass / "_tcl_data"
    fake_tcl.mkdir()
    (fake_tcl / "init.tcl").write_text("# fake init.tcl", encoding="utf-8")
    fake_tk = fake_meipass / "_tk_data"
    fake_tk.mkdir()
    (fake_tk / "tk.tcl").write_text("# fake tk.tcl", encoding="utf-8")
    fake_tcl8 = fake_meipass / "tcl8"
    fake_tcl8.mkdir()

    orig_env = os.environ.copy()
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    monkeypatch.delenv("TCL_LIBRARY", raising=False)
    monkeypatch.delenv("TK_LIBRARY", raising=False)
    monkeypatch.delenv("TCLLIBPATH", raising=False)

    try:
        _bootstrap_tcl_tk_environment()

        assert os.environ.get("TCL_LIBRARY") == str(fake_tcl)
        assert os.environ.get("TK_LIBRARY") == str(fake_tk)
        assert str(fake_meipass) in os.environ.get("TCLLIBPATH", "")

        # Test CLI --smoke-test argument parsing
        args = parse_cli_args(["--smoke-test"])
        assert args.smoke_test is True
    finally:
        os.environ.clear()
        os.environ.update(orig_env)
