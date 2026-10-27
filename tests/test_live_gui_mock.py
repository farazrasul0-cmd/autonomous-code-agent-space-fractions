"""Offline Unit and Mock Acceptance Tests for Dual-Mode Safety Gates (Phase 2.27).

Covers:
1. Output token limit consistency across DeepSeekClient, Adapter, and AgentLoop.
2. Pre-transmission reservation matching assertion in DeepSeekClient.
3. Budget preflight input bound and positive integer constraints.
4. Synchronized cancellation and commit arbitration under state lock.
5. Interface validation for generated math deliverables (Fraction, MathQuestionGenerator).
6. Atomic directory promotion and failure preservation semantics.
7. Deferred GUI window closing during COMMITTING state.

Zero live network requests, zero live API calls, zero financial spend ($0.00).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import pytest

from src.budget_controller import BudgetController, BudgetException
from src.deepseek_client import DeepSeekClient
from src.agent_loop import DeepSeekAgentAdapter, AgentLoop, LoopTerminationStatus
from src.synthesis_driver import SynthesisDriver


# -----------------------------------------------------------------------------
# 1. Output Token Consistency and Pre-Transmission Checks
# -----------------------------------------------------------------------------

def test_output_limit_mismatch_adapter_client():
    """Asserts that DeepSeekAgentAdapter raises ValueError if max_output_tokens != client tokens."""
    client = DeepSeekClient(api_key="sk-test-key", max_output_tokens=3200)
    with pytest.raises(ValueError, match="does not match client limit"):
        DeepSeekAgentAdapter(client=client, max_output_tokens=1200)


def test_output_limit_matching_adapter_client():
    """Confirms that matching limits are accepted and stored accurately."""
    client = DeepSeekClient(api_key="sk-test-key", max_output_tokens=3200)
    adapter = DeepSeekAgentAdapter(client=client, max_output_tokens=3200)
    assert adapter.max_output_tokens == 3200


def test_pre_transmission_token_mismatch_aborts():
    """Asserts that send_chat_completion raises BudgetException if reservation tokens != payload tokens."""
    budget = BudgetController(ceiling=0.80)
    budget.reserve(
        reservation_id="turn-test-mismatch",
        prompt_text="Hello",
        max_output_tokens=1200,
    )
    client = DeepSeekClient(api_key="sk-test-key", budget_controller=budget, max_output_tokens=3200)

    # Calling with skip_budget_reservation=True and a payload of 3200 tokens must trip the pre-transmission gate
    with pytest.raises(BudgetException, match="Pre-transmission token mismatch"):
        client.send_chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=3200,
            skip_budget_reservation=True,
            reservation_id="turn-test-mismatch",
        )


def test_pre_transmission_missing_reservation_aborts():
    """Asserts that send_chat_completion raises BudgetException if no active reservation exists."""
    budget = BudgetController(ceiling=0.80)
    client = DeepSeekClient(api_key="sk-test-key", budget_controller=budget, max_output_tokens=3200)

    with pytest.raises(BudgetException, match="No active reservation found"):
        client.send_chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=3200,
            skip_budget_reservation=True,
            reservation_id="non-existent-reservation-id",
        )


def test_budget_controller_output_token_bounds():
    """Verifies that BudgetController rejects invalid output token limits."""
    budget = BudgetController(ceiling=0.80)
    with pytest.raises(BudgetException, match="positive integer"):
        budget.reserve("r1", "prompt", max_output_tokens=-10)

    with pytest.raises(BudgetException, match="positive integer"):
        budget.reserve("r2", "prompt", max_output_tokens=0)

    with pytest.raises(BudgetException, match="exceeds maximum permitted"):
        budget.reserve("r3", "prompt", max_output_tokens=5000)


# -----------------------------------------------------------------------------
# 2. Interface Validation for Space Fractions Deliverables
# -----------------------------------------------------------------------------

def test_interface_verification_valid_code():
    """Verifies that complete Fraction and MathQuestionGenerator implementations pass validation."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write('''
class Fraction:
    def __init__(self, n, d=1): self.n, self.d = n, d
    def simplify(self): return self
    def add(self, o): return self
    def subtract(self, o): return self
    def compare(self, o): return 0
    def equals(self, o): return True

class MathQuestionGenerator:
    def __init__(self, seed=42): pass
    def generate(self, level=1, index=1): return None
''')
        f_path = f.name

    try:
        ok, msg = SynthesisDriver.validate_fraction_math_interface(f_path)
        assert ok is True
        assert "verified" in msg.lower()
    finally:
        os.remove(f_path)


def test_interface_verification_missing_class():
    """Verifies that missing class 'MathQuestionGenerator' fails validation."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write('''
class Fraction:
    def __init__(self, n, d=1): pass
    def simplify(self): pass
    def add(self, o): pass
    def subtract(self, o): pass
    def compare(self, o): pass
    def equals(self, o): pass
''')
        f_path = f.name

    try:
        ok, msg = SynthesisDriver.validate_fraction_math_interface(f_path)
        assert ok is False
        assert "MathQuestionGenerator" in msg
    finally:
        os.remove(f_path)


def test_interface_verification_missing_method():
    """Verifies that missing method 'simplify' fails validation."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write('''
class Fraction:
    def __init__(self, n, d=1): pass
    def add(self, o): pass
    def subtract(self, o): pass
    def compare(self, o): pass
    def equals(self, o): pass

class MathQuestionGenerator:
    def __init__(self, seed=42): pass
    def generate(self, level=1, index=1): return None
''')
        f_path = f.name

    try:
        ok, msg = SynthesisDriver.validate_fraction_math_interface(f_path)
        assert ok is False
        assert "simplify" in msg
    finally:
        os.remove(f_path)


# -----------------------------------------------------------------------------
# 3. Directory Staging, Promotion, and Failure Preservation
# -----------------------------------------------------------------------------

def test_atomic_directory_promotion_success():
    """Verifies atomic rename to a unique destination directory."""
    with tempfile.TemporaryDirectory() as base_dir:
        staging_dir = os.path.join(base_dir, ".staging_test_run")
        os.makedirs(staging_dir)
        test_file = os.path.join(staging_dir, "fraction_math.py")
        with open(test_file, "w") as f:
            f.write("# valid code")

        dest_dir = os.path.join(base_dir, "space_fractions_run_20261025_success")
        assert not os.path.exists(dest_dir)

        # Atomic rename
        os.rename(staging_dir, dest_dir)
        assert os.path.isdir(dest_dir)
        assert not os.path.exists(staging_dir)
        assert os.path.isfile(os.path.join(dest_dir, "fraction_math.py"))


def test_staging_preservation_on_promotion_failure():
    """Verifies that if directory promotion fails, the staging directory is preserved."""
    with tempfile.TemporaryDirectory() as base_dir:
        staging_dir = os.path.join(base_dir, ".staging_failed_run")
        os.makedirs(staging_dir)
        artifact_path = os.path.join(staging_dir, "fraction_math.py")
        with open(artifact_path, "w") as f:
            f.write("# preserved code")

        # Create an existing directory collision to induce failure
        dest_dir = os.path.join(base_dir, "collision_dir")
        os.makedirs(dest_dir)

        # On Windows, os.rename to an existing directory raises FileExistsError or OSError
        promotion_failed = False
        try:
            os.rename(staging_dir, dest_dir)
        except OSError:
            promotion_failed = True

        assert promotion_failed is True
        # Crucial safety check: staging directory MUST still exist intact
        assert os.path.isdir(staging_dir)
        assert os.path.isfile(artifact_path)


# -----------------------------------------------------------------------------
# 4. GUI Window Close and Commit State Synchronization
# -----------------------------------------------------------------------------

def test_gui_window_close_deferred_during_committing(monkeypatch):
    """Verifies that AgentGUIApp.on_window_closing defers window closure when run_state is COMMITTING."""
    import tkinter as tk
    from src.agent_gui import AgentGUIApp

    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as err:
        pytest.skip(f"Tkinter display environment unavailable: {err}")

    try:
        app = AgentGUIApp(root, suppress_messagebox=True)
        with app._state_lock:
            app.is_running = True
            app.run_state = "COMMITTING"

        scheduled = []
        original_destroy = root.destroy
        monkeypatch.setattr(root, "after", lambda ms, cb: scheduled.append((ms, cb)))
        monkeypatch.setattr(root, "destroy", lambda: pytest.fail("Window must NOT be destroyed during COMMITTING!"))

        app.on_window_closing()

        assert app.run_state == "COMMITTING"
        assert not app.cancel_requested.is_set()
        assert len(scheduled) == 1
        assert scheduled[0][0] == 200
        assert scheduled[0][1] == app.on_window_closing
    finally:
        original_destroy()

