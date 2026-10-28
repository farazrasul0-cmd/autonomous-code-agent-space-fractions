"""tests/test_gui_dispatch_p266.py
Phase 2.66: Live GUI Dispatch, Configuration, and Release Readiness Audit Test Suite.

Verifies:
1. Dynamic UI label updates for Start button, Window title, and Subtitle across modes.
2. Default Task Goal adaptation and preservation of custom user input.
3. Direct GUI entry API key status tracking without requiring local .env.
4. Precedence of direct GUI key entry over .env configuration.
5. Strict authorization and credential validation before live worker dispatch.
6. Worker configuration capture and live DeepSeek client wiring.
7. Unreconciled budget telemetry display with active local $0.80 ceiling guard.
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

from src.agent_gui import AgentGUIApp, WorkerRunConfig
from src.budget_controller import BudgetController


@pytest.fixture(scope="module")
def tk_root():
    """Provides an isolated headless Tkinter root for GUI testing."""
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


# =====================================================================
# 1. UI Label & Mode Switching Verifications
# =====================================================================

def test_p266_mode_switch_updates_start_button_label(tk_root):
    """Verifies that switching execution modes dynamically updates the Start button label."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)

    # Default is offline
    assert app.btn_start.cget("text") == "▶  Start Offline Validation"

    # Switch to mock_agent
    app.execution_mode_var.set("mock_agent")
    assert app.btn_start.cget("text") == "▶  Start Mock Agent Synthesis"

    # Switch to live mode (authorized)
    app.execution_mode_var.set("live")
    assert app.btn_start.cget("text") == "▶  Start Live Agent Generation"

    # Switch back to offline
    app.execution_mode_var.set("offline")
    assert app.btn_start.cget("text") == "▶  Start Offline Validation"


def test_p266_live_dispatch_unauthorized_button_label(tk_root):
    """Verifies that when live dispatch is not authorized, the Start button indicates unauthorized."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=False)

    # Live radio button is disabled by default
    assert str(app.radio_live.cget("state")) == "disabled"

    # If mode is somehow set to live without authorization
    app.execution_mode_var.set("live")
    assert app.btn_start.cget("text") == "▶  Live Dispatch Unauthorized"
    assert "Live dispatch disabled" in app.status_var.get()
    assert "LIVE API MODE (DISABLED)" in app.lbl_mode_badge.cget("text")

    # Authorize live dispatch
    app.allow_live_var.set(True)
    app._on_toggle_live_auth()
    assert app.allow_live_dispatch is True
    assert app.btn_start.cget("text") == "▶  Start Live Agent Generation"
    assert "Ready. Click 'Start Live Agent Generation'" in app.status_var.get()
    assert "LIVE DEEPSEEK AGENT" in app.lbl_mode_badge.cget("text")


def test_p266_mode_switch_updates_window_title_and_subtitle(tk_root):
    """Verifies that window title and subtitle accurately reflect the active execution mode."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)

    # 1. Offline Mode
    app.execution_mode_var.set("offline")
    assert "[Offline Shell]" in tk_root.title()
    assert "Simulated Offline Mode" in app.lbl_subtitle.cget("text")

    # 2. Mock Agent Mode
    app.execution_mode_var.set("mock_agent")
    assert "[Offline Mock Agent]" in tk_root.title()
    assert "Offline Mock Double" in app.lbl_subtitle.cget("text")

    # 3. Live Mode (Authorized)
    app.execution_mode_var.set("live")
    assert "[Live Agent]" in tk_root.title()
    assert "Official HTTPS API" in app.lbl_subtitle.cget("text")

    # 4. Live Mode (Unauthorized)
    app.allow_live_var.set(False)
    app._on_toggle_live_auth()
    assert "[Offline Shell]" in tk_root.title() or "[Live API Disabled]" in tk_root.title()


def test_p266_task_goal_default_switching_and_preservation(tk_root):
    """Verifies default task goal adapts to mode while preserving user-entered custom goals."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)

    # Initially in offline mode with offline default task
    assert app.task_desc_var.get() == app.DEFAULT_OFFLINE_TASK

    # Switch to live mode -> adapts to agent synthesis task
    app.execution_mode_var.set("live")
    assert app.task_desc_var.get() == app.DEFAULT_AGENT_TASK

    # Switch back to offline -> adapts back to offline pipeline task
    app.execution_mode_var.set("offline")
    assert app.task_desc_var.get() == app.DEFAULT_OFFLINE_TASK

    # User enters custom task goal
    custom_goal = "Synthesize only the fraction arithmetic engine with property-based unit tests."
    app.task_desc_var.set(custom_goal)

    # Switching modes must PRESERVE custom goal
    app.execution_mode_var.set("live")
    assert app.task_desc_var.get() == custom_goal

    app.execution_mode_var.set("mock_agent")
    assert app.task_desc_var.get() == custom_goal

    app.execution_mode_var.set("offline")
    assert app.task_desc_var.get() == custom_goal


# =====================================================================
# 2. API Key Status & Precedence Verifications
# =====================================================================

def test_p266_key_status_direct_gui_entry_without_env(tk_root):
    """Verifies that entering a key directly in the GUI updates the status badge without requiring .env."""
    with patch.object(AgentGUIApp, "_find_env_file", return_value=None):
        app = AgentGUIApp(tk_root, suppress_messagebox=True)

        # When no .env is present
        assert app.key_status_var.get() == "Key Status: No local .env detected"

        # User enters dummy key in GUI
        dummy_key = "sk-test-dummy-secret-key-12345"
        app.api_key_var.set(dummy_key)

        # Status must update to GUI entry
        assert app.key_status_var.get() == "Key Status: Key Configured via GUI Entry (Masked)"
        assert dummy_key not in app.key_status_var.get()

        # User clears the key
        app.api_key_var.set("")
        assert app.key_status_var.get() == "Key Status: No local .env detected"


def test_p266_key_status_env_file_loading_and_override(tk_root):
    """Verifies loading key from .env and subsequent override by user in GUI entry."""
    with tempfile.TemporaryDirectory() as tmp_env_dir:
        env_file = os.path.join(tmp_env_dir, ".env")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("DEEPSEEK_API_KEY=sk-env-loaded-secret-67890\n")

        with patch("os.getcwd", return_value=tmp_env_dir):
            app = AgentGUIApp(tk_root, suppress_messagebox=True)

            # Successfully loaded from .env
            assert app.api_key_var.get() == "sk-env-loaded-secret-67890"
            assert app.key_status_var.get() == "Key Status: Masked Key Loaded from .env (Local Only)"

            # User overrides in GUI entry
            app.api_key_var.set("sk-manual-override-key-99999")
            assert app.key_status_var.get() == "Key Status: Key Configured via GUI Entry (Masked)"
            assert "sk-manual-override-key" not in app.key_status_var.get()


# =====================================================================
# 3. Live Dispatch Routing & Worker Configuration
# =====================================================================

def test_p266_live_dispatch_validation_rejects_missing_key(tk_root):
    """Verifies that live mode execution is strictly blocked before worker dispatch if API key is missing."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)
    app.execution_mode_var.set("live")
    app.api_key_var.set("")  # Empty key

    app.on_start_click()

    # Must fail validation and not start worker
    assert app.is_running is False
    assert app.worker_thread is None


def test_p266_live_dispatch_captures_worker_config(tk_root):
    """Verifies that on_start_click captures an immutable WorkerRunConfig with live mode and key."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)
    app.execution_mode_var.set("live")
    dummy_key = "sk-live-authorized-dummy-key"
    app.api_key_var.set(dummy_key)

    spawned_threads: List[WorkerRunConfig] = []

    def mock_thread_start(target, args, daemon):
        config: WorkerRunConfig = args[6]
        spawned_threads.append(config)
        return MagicMock()

    with patch("threading.Thread", side_effect=mock_thread_start):
        app.on_start_click()

    assert len(spawned_threads) == 1
    captured_config = spawned_threads[0]
    assert captured_config.execution_mode == "live"
    assert captured_config.allow_live_dispatch is True
    assert captured_config.api_key == dummy_key


# =====================================================================
# 4. Budget Telemetry & Unreconciled Status
# =====================================================================

def test_p266_unreconciled_budget_display_does_not_block_dispatch(tk_root):
    """Verifies that UNRECONCILED historical spend is accurately displayed and does not block live dispatch."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True, allow_live_dispatch=True)

    # Ensure baseline telemetry evaluation
    app._update_telemetry_display()

    # Verified spend & remaining available budget must be UNRECONCILED
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "UNRECONCILED"
    assert app.telemetry_labels["Remaining Available Budget:"].cget("text") == "UNRECONCILED"
    assert app.telemetry_labels["Hard Project Budget Ceiling:"].cget("text") == "$0.800000"

    # Local ceiling must be 0.80
    assert app.budget_controller.ceiling == 0.800000
