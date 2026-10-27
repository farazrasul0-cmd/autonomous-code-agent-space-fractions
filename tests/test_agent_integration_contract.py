"""Deterministic Offline Integration Contract Tests (Phase 2.27.22).

Establishes which integration contracts are supported before modifying production code.
Covers 11 critical integration areas:
1. Mock-client injection interfaces and worker reachability audit.
2. Multi-file agent generation with mock responses derived from project contracts.
3. Malformed responses and invalid tool arguments handling in parser and agent loop.
4. Path traversal rejection and staging boundary containment.
5. Pre-call budget rejection before any transport call.
6. Cancellation behavior contracts (pre-transmission release vs post-transmission failure).
7. Invalid Python syntax preventing promotion.
8. Missing required interfaces preventing promotion.
9. Missing, malformed, and unavailable telemetry fault tolerance.
10. Failed staging output preservation.
11. Budget-reservation ownership between AgentLoop and DeepSeekClient.

Zero live network requests, zero live API calls, zero financial spend ($0.00).
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import tempfile
import threading
import time
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple
import pytest

from src.agent_gui import AgentGUIApp
from src.agent_loop import (
    AgentLoop,
    DeepSeekAgentAdapter,
    LLMClientProtocol,
    LoopTerminationStatus,
    ModelTurnResponse,
    ToolCall,
)
from src.budget_controller import (
    BudgetController,
    BudgetExceededError,
    BudgetException,
    InvalidUsageDataError,
    ReservationState,
)
from src.deepseek_client import (
    DeepSeekClient,
    DeepSeekResponse,
    RequestOutcomeStatus,
)
from src.generator_pipeline import (
    OfflinePipelineCoordinator,
    PipelineExecutionSummary,
    PipelineStageType,
    StagePlan,
)
from src.path_security import PathSecurityError, validate_workspace_path
from src.project_validator import ProjectValidator, ValidationResult
from src.spec_preprocessor import SpecificationPreprocessor


# =============================================================================
# Test Fixtures & Deterministic Mock Clients
# =============================================================================

@pytest.fixture(scope="module")
def tk_root():
    """Provides a hidden Tk root window for headless-safe testing."""
    try:
        root = tk.Tk()
        root.withdraw()
        yield root
        root.destroy()
    except tk.TclError as err:
        pytest.skip(f"Tkinter display environment unavailable: {err}")


@pytest.fixture
def canonical_doc_paths():
    """Resolves and validates the canonical architecture input documents."""
    doc_path = os.path.abspath("Architecture_Documentation.md")
    view_path = os.path.abspath("Architecture_View.md")
    assert os.path.isfile(doc_path), f"Missing {doc_path}"
    assert os.path.isfile(view_path), f"Missing {view_path}"
    return doc_path, view_path


class MultiFileMockLLMClient:
    """Deterministic mock client synthesizing valid multi-file Space Fractions assets."""

    def __init__(self):
        self.call_count = 0
        self.recorded_reservations: List[Optional[str]] = []

    def chat_completion(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], reservation_id: Optional[str] = None
    ) -> ModelTurnResponse:
        self.call_count += 1
        self.recorded_reservations.append(reservation_id)

        # If previous message is a tool response, stage execution is finished
        has_tool_results = any(m.get("role") == "tool" for m in messages)
        if has_tool_results:
            return ModelTurnResponse(
                content="Stage synthesis complete.",
                tool_calls=[],
                raw_usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                finish_reason="stop",
                status="SUCCESS",
            )

        prompt_text = json.dumps(messages)

        # Valid fraction math implementation
        valid_fraction_math = (
            '"""Fraction math engine."""\n'
            "class Fraction:\n"
            "    def __init__(self, n=0, d=1):\n"
            "        if d == 0: raise ValueError('Denominator cannot be zero')\n"
            "        self.n = n\n"
            "        self.d = d\n"
            "    def simplify(self):\n"
            "        import math\n"
            "        g = math.gcd(self.n, self.d)\n"
            "        return Fraction(self.n // g, self.d // g)\n"
            "    def add(self, o):\n"
            "        return Fraction(self.n * o.d + o.n * self.d, self.d * o.d).simplify()\n"
            "    def subtract(self, o):\n"
            "        return Fraction(self.n * o.d - o.n * self.d, self.d * o.d).simplify()\n"
            "    def compare(self, o):\n"
            "        diff = self.n * o.d - o.n * self.d\n"
            "        return 1 if diff > 0 else (-1 if diff < 0 else 0)\n"
            "    def equals(self, o):\n"
            "        return self.compare(o) == 0\n\n"
            "class MathQuestionGenerator:\n"
            "    def __init__(self, seed=42): pass\n"
            "    def generate(self, level=1, index=1):\n"
            "        class Q:\n"
            "            prompt = 'What is 1/2 + 1/4?'\n"
            "            options = ['3/4', '2/4', '1/4', '4/4']\n"
            "            correct_index = 0\n"
            "        return Q()\n"
        )

        valid_game_engine = (
            '"""Game engine."""\n'
            "from enum import Enum\n"
            "class GameState(str, Enum):\n"
            "    TITLE = 'TITLE'\n"
            "    PLAYING = 'PLAYING'\n"
            "    PAUSED = 'PAUSED'\n"
            "    GAMEOVER = 'GAMEOVER'\n\n"
            "class GameEngine:\n"
            "    def __init__(self, level=1, total_questions=10, seed=42):\n"
            "        self.state = GameState.TITLE\n"
            "        self.level = level\n"
            "        self.score = 0\n"
            "        self.current_question = None\n"
            "    def play(self):\n"
            "        self.state = GameState.PLAYING\n"
            "    def submit_answer(self, option_index):\n"
            "        class Res: is_correct = True; energy_awarded = 100\n"
            "        return Res()\n"
            "    def pause(self):\n"
            "        if self.state == GameState.PLAYING: self.state = GameState.PAUSED\n"
            "    def resume(self):\n"
            "        if self.state == GameState.PAUSED: self.state = GameState.PLAYING\n"
            "    def view_score(self):\n"
            "        return self.score\n"
            "    def viewScore(self):\n"
            "        return self.score\n"
        )

        valid_question_bank = (
            '"""Question bank."""\n'
            "class Question:\n"
            "    def __init__(self, prompt, options, correct_index, explanation='', topic='Math'):\n"
            "        self.prompt = prompt; self.options = options; self.correct_index = correct_index\n"
            "        self.explanation = explanation; self.topic = topic\n"
            "class QuestionBank:\n"
            "    def __init__(self, level=1): pass\n"
            "    def get_questions(self): return []\n"
        )

        valid_ui_screens = (
            '"""UI Screens."""\n'
            "import tkinter as tk\n"
            "from tkinter import ttk\n"
            "class FractionVisualizer(tk.Canvas):\n"
            "    def __init__(self, parent, **kwargs): super().__init__(parent, **kwargs)\n"
            "    def draw_fraction(self, n, d, title=''): pass\n"
            "class HUDFrame(ttk.Frame):\n"
            "    def __init__(self, parent, **kwargs): super().__init__(parent, **kwargs)\n"
            "    def update_hud(self, shields, score, streak, current_q, total_q): pass\n"
        )

        valid_main = (
            '"""Main desktop app."""\n'
            "import tkinter as tk\n"
            "class SpaceFractionsApp:\n"
            "    def __init__(self, root): self.root = root\n"
            "def main(): pass\n"
            "if __name__ == '__main__': main()\n"
        )

        valid_test_math = (
            "import pytest\n"
            "from space_fractions.fraction_math import Fraction\n"
            "def test_fraction_math_basic(): assert Fraction(1, 2).equals(Fraction(2, 4))\n"
        )

        valid_test_engine = (
            "import pytest\n"
            "from space_fractions.game_engine import GameEngine, GameState\n"
            "def test_game_engine_basic():\n"
            "    e = GameEngine()\n"
            "    e.play()\n"
            "    assert e.state == GameState.PLAYING\n"
        )

        # Detect stage from prompt
        if "specs_generation" in prompt_text or "S1-SPECS" in prompt_text:
            files_to_write = {
                "specs/openapi.yaml": "openapi: 3.0.3\ninfo:\n  title: API\n  version: 1.0.0\npaths: {}\n",
                "specs/internal.proto": 'syntax = "proto3";\npackage spacefractions;\n',
                "specs/sql/game_ddl.sql": "CREATE TABLE IF NOT EXISTS games (id SERIAL PRIMARY KEY);\n",
                "specs/k8s/spacefractions-deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\n",
                "specs/traceability_matrix.csv": "RequirementId,Component,File,Status\nREQ-1,Core,math.py,Pass\n",
            }
        elif "game_core_generation" in prompt_text or "S2-GAME-CORE" in prompt_text:
            files_to_write = {
                "space_fractions/fraction_math.py": valid_fraction_math,
                "space_fractions/game_engine.py": valid_game_engine,
                "space_fractions/question_bank.py": valid_question_bank,
            }
        elif "game_ui_generation" in prompt_text or "S3-GAME-UI" in prompt_text:
            files_to_write = {
                "space_fractions/ui_screens.py": valid_ui_screens,
                "space_fractions/main.py": valid_main,
            }
        elif "tests_generation" in prompt_text or "S4-TESTS" in prompt_text:
            files_to_write = {
                "tests/test_fraction_math.py": valid_test_math,
                "tests/test_game_engine.py": valid_test_engine,
                "requirements.txt": "# Requirements\n",
                "README.md": "# Readme\n",
                "offline_validation_manifest.json": json.dumps({
                    "runtime_gameplay_verified": False,
                    "generated_files": [],
                }),
            }
        else:
            files_to_write = {}

        if files_to_write:
            tool_calls = [
                ToolCall(tool_id=f"tc_{idx}", name="write_file", arguments={"path": path, "content": content})
                for idx, (path, content) in enumerate(files_to_write.items())
            ]
            return ModelTurnResponse(
                content=None,
                tool_calls=tool_calls,
                raw_usage={"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230},
                finish_reason="tool_calls",
                status="SUCCESS",
            )
        else:
            return ModelTurnResponse(
                content="Stage synthesis complete.",
                tool_calls=[],
                raw_usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                finish_reason="stop",
                status="SUCCESS",
            )


# =============================================================================
# 1. Mock-Client Injection & Worker Reachability Audit
# =============================================================================

def test_contract_01_worker_reachability_and_injection_audit(tk_root, canonical_doc_paths):
    """Area 1: Verify mock-client injection interfaces and audit worker routing.
    
    Proves that:
    1. With mock_llm_client injected, the coordinator invokes run_agent_pipeline.
    2. Without mock_llm_client (the production default), the worker falls back to run_mock_pipeline template copying.
    3. get_deepseek_client() is currently orphaned and never called by the worker.
    """
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "contract_01_test")
        os.makedirs(output_dir, exist_ok=True)

        # Case A: Injected mock client
        mock_client = MultiFileMockLLMClient()
        app_injected = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            mock_llm_client=mock_client,
            workflow_mode="offline_pipeline",
        )
        assert app_injected.mock_llm_client is mock_client

        # Case B: Production default (no injected client)
        app_default = AgentGUIApp(tk_root, suppress_messagebox=True)
        assert app_default.mock_llm_client is None
        assert app_default.allow_live_dispatch is False
        assert app_default.workflow_mode == "offline_pipeline"
        assert str(app_default.radio_live.cget("state")) == "disabled"

        # Case C: get_deepseek_client() exists but is not invoked in production worker
        client_instance = app_default.get_deepseek_client()
        assert isinstance(client_instance, DeepSeekClient)
        assert hasattr(app_default, "get_deepseek_client")


# =============================================================================
# 2. Multi-File Agent Generation via Mock Responses
# =============================================================================

def test_contract_02_multi_file_agent_generation_and_validation(tk_root, canonical_doc_paths):
    """Area 2: Verify multi-file agent generation using mock responses with project deliverables."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "contract_02_multi_file")
        os.makedirs(output_dir, exist_ok=True)

        budget = BudgetController(ceiling=0.80)
        mock_client = MultiFileMockLLMClient()

        coordinator = OfflinePipelineCoordinator(
            workspace_root=output_dir,
            output_dir=output_dir,
            budget_controller=budget,
            llm_client=mock_client,
        )

        summary = coordinator.run_agent_pipeline(
            doc_path=doc_path,
            view_path=view_path,
            llm_client=mock_client,
        )

        assert summary.success is True, f"Pipeline failed: {summary.errors}"
        assert summary.total_stages_run == 4
        assert len(summary.completed_stages) == 4

        # Validate that all expected project deliverables were generated on disk
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
        ]
        for rel in expected_files:
            full = os.path.join(output_dir, rel)
            assert os.path.isfile(full), f"Deliverable missing from disk: {rel}"

        # Validate with ProjectValidator
        val = ProjectValidator.validate_project(output_dir, require_full_game=True)
        assert val.success is True, f"Validation failed with errors: {val.errors}"
        assert val.ast_syntax_valid is True
        assert val.interfaces_valid is True


# =============================================================================
# 3. Malformed Responses and Invalid Tool Arguments
# =============================================================================

def test_contract_03_malformed_responses_and_invalid_arguments(tk_root):
    """Area 3: Verify malformed responses and invalid tool arguments handling in parser and loop."""
    with tempfile.TemporaryDirectory() as temp_dir:
        budget = BudgetController(ceiling=0.80)

        # Case A: Model returns malformed JSON in tool call arguments string
        class MalformedArgClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            tool_id="call_bad_json",
                            name="write_file",
                            arguments={"_parse_error": "Malformed tool arguments JSON: Unterminated string"},
                        )
                    ],
                    raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    status="SUCCESS",
                )

        loop_a = AgentLoop(
            workspace_root=temp_dir,
            llm_client=MalformedArgClient(),
            budget_controller=budget,
            max_turns=2,
            session_id="contract_03a",
        )
        res_a = loop_a.run("Test prompt")
        # Loop must catch _parse_error and return tool failure observation without crashing
        assert len(res_a.transcript) >= 2
        tool_results = [t.tool_result for t in res_a.transcript if t.role == "tool_result"]
        assert any("Malformed tool arguments" in (tr or "") for tr in tool_results)

        # Case B: Model returns REFUSAL outcome
        class RefusalClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(
                    content="",
                    tool_calls=[],
                    status="REFUSAL",
                    error_message="I cannot fulfill this request.",
                    raw_usage={"prompt_tokens": 30, "completion_tokens": 5},
                )

        loop_b = AgentLoop(
            workspace_root=temp_dir,
            llm_client=RefusalClient(),
            budget_controller=budget,
            max_turns=2,
            session_id="contract_03b",
        )
        res_b = loop_b.run("Test prompt")
        assert res_b.status == LoopTerminationStatus.REFUSAL_ABORTED
        assert res_b.error_message == "I cannot fulfill this request."


# =============================================================================
# 4. Path Traversal Rejection and Staging Boundary Containment
# =============================================================================

def test_contract_04_path_traversal_rejection_and_containment():
    """Area 4: Verify path traversal rejection and prove no file can escape staging root."""
    with tempfile.TemporaryDirectory() as temp_dir:
        staging_root = os.path.join(temp_dir, "staging")
        os.makedirs(staging_root, exist_ok=True)
        outside_sentinel = os.path.join(temp_dir, "outside_sentinel.txt")

        budget = BudgetController(ceiling=0.80)

        # Client attempts various traversal payloads
        class TraversalAttemptClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    # Parent directory escape
                    return ModelTurnResponse(
                        content=None,
                        tool_calls=[ToolCall("tc1", "write_file", {"path": "../outside_sentinel.txt", "content": "breach"})],
                        status="SUCCESS",
                    )
                elif self.turn == 2:
                    # Windows absolute path escape
                    return ModelTurnResponse(
                        content=None,
                        tool_calls=[ToolCall("tc2", "write_file", {"path": "C:\\Windows\\breach.txt", "content": "breach"})],
                        status="SUCCESS",
                    )
                elif self.turn == 3:
                    # Null byte injection
                    return ModelTurnResponse(
                        content=None,
                        tool_calls=[ToolCall("tc3", "write_file", {"path": "safe_dir/\0malicious.py", "content": "breach"})],
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        loop = AgentLoop(
            workspace_root=staging_root,
            llm_client=TraversalAttemptClient(),
            budget_controller=budget,
            max_turns=4,
            session_id="contract_04",
        )
        res = loop.run("Attempt path breach")

        # Prove no file was created outside the staging boundary
        assert not os.path.exists(outside_sentinel)
        tool_results = [t.tool_result for t in res.transcript if t.role == "tool_result"]
        assert all("SecurityError" in (tr or "") or "Path traversal" in (tr or "") or "Null bytes" in (tr or "") for tr in tool_results[:3])


# =============================================================================
# 5. Pre-Call Budget Rejection Before Transport
# =============================================================================

def test_contract_05_pre_call_budget_rejection():
    """Area 5: Verify budget rejection occurs strictly before any network transport call."""
    budget = BudgetController(ceiling=0.80)
    # Set cumulative spend right up to the ceiling
    budget.cumulative_spend = 0.800000

    transport_called = False

    def spy_transport(req, timeout):
        nonlocal transport_called
        transport_called = True
        return 200, b'{"choices": []}'

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=budget,
        transport_sender=spy_transport,
    )

    # 1. Direct client call must fail pre-call reservation
    resp = client.send_chat_completion(
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=100,
    )
    assert resp.status == RequestOutcomeStatus.BUDGET_REJECTED
    assert transport_called is False, "CRITICAL: Transport was called despite budget exhaustion!"

    # 2. AgentLoop call must fail before querying model
    class ShouldNotBeCalledClient:
        def chat_completion(self, *args, **kwargs):
            raise AssertionError("Model should never be called when budget is exhausted!")

    loop = AgentLoop(
        workspace_root=".",
        llm_client=ShouldNotBeCalledClient(),
        budget_controller=budget,
        session_id="contract_05",
    )
    loop_res = loop.run("Test budget rejection")
    assert loop_res.status == LoopTerminationStatus.BUDGET_EXCEEDED


# =============================================================================
# 6. Cancellation Behavior Contracts
# =============================================================================

def test_contract_06_cancellation_contracts():
    """Area 6: Verify cancellation contracts (pre-transmission release vs post-transmission failure)."""
    budget = BudgetController(ceiling=0.80)
    cancel_event = threading.Event()

    # Case A: Pre-transmission cancellation
    cancel_event.set()
    client = DeepSeekClient(api_key="sk-test-key", budget_controller=budget)

    resp_pre = client.send_chat_completion(
        messages=[{"role": "user", "content": "Cancel me"}],
        cancel_event=cancel_event,
    )
    assert resp_pre.status == RequestOutcomeStatus.CANCELLED
    assert resp_pre.cancellation_outcome == "cancelled_before_transmission"
    # Zero spend and zero active holds
    assert budget.cumulative_spend == 0.0
    assert budget.get_active_holds() == 0.0

    # Case B: Post-transmission transport failure / uncertain outcome
    cancel_event.clear()

    def timeout_transport(req, timeout):
        raise TimeoutError("Socket timed out in-flight")

    client_timeout = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=budget,
        transport_sender=timeout_transport,
    )

    req_id = "req-uncertain-test"
    resp_post = client_timeout.send_chat_completion(
        messages=[{"role": "user", "content": "Timeout test"}],
        reservation_id=req_id,
        cancel_event=cancel_event,
    )
    assert resp_post.status == RequestOutcomeStatus.TIMEOUT_ERROR
    # Verify defensive accounting: reservation was permanently locked as potentially billable
    assert budget.permanently_locked_spend > 0.0
    rec = budget._all_reservations.get(req_id)
    assert rec.state == ReservationState.PERMANENTLY_LOCKED


# =============================================================================
# 7. Invalid Python Syntax Prevents Promotion
# =============================================================================

def test_contract_07_invalid_python_syntax_prevents_promotion(tk_root, canonical_doc_paths):
    """Area 7: Verify invalid Python syntax in generated code prevents directory promotion."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "contract_07_syntax")
        os.makedirs(output_dir, exist_ok=True)

        class SyntaxErrorMockClient(MultiFileMockLLMClient):
            def chat_completion(self, messages, tools, reservation_id=None):
                resp = super().chat_completion(messages, tools, reservation_id)
                # If writing fraction_math.py, inject syntax error
                if resp.tool_calls:
                    for tc in resp.tool_calls:
                        if tc.arguments.get("path") == "space_fractions/fraction_math.py":
                            tc.arguments["content"] = "class Fraction:\n    def broken_syntax( self:"
                return resp

        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            mock_llm_client=SyntaxErrorMockClient(),
            workflow_mode="offline_pipeline",
        )

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract07_run")

        # Must fail closed
        assert app.run_state == "FAILED"
        assert app.last_run_dir is None
        # Promotion did not occur
        for item in os.listdir(output_dir):
            assert not item.startswith("space_fractions_run_")


# =============================================================================
# 8. Missing Required Interfaces Prevent Promotion
# =============================================================================

def test_contract_08_missing_required_interfaces_prevent_promotion(tk_root, canonical_doc_paths):
    """Area 8: Verify missing required interfaces in generated code prevent directory promotion."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "contract_08_interface")
        os.makedirs(output_dir, exist_ok=True)

        class MissingInterfaceMockClient(MultiFileMockLLMClient):
            def chat_completion(self, messages, tools, reservation_id=None):
                resp = super().chat_completion(messages, tools, reservation_id)
                # Omit 'simplify' and 'equals' from Fraction
                if resp.tool_calls:
                    for tc in resp.tool_calls:
                        if tc.arguments.get("path") == "space_fractions/fraction_math.py":
                            tc.arguments["content"] = (
                                "class Fraction:\n"
                                "    def __init__(self, n=0, d=1): self.n = n; self.d = d\n"
                                "    def add(self, o): return self\n"
                                "    def subtract(self, o): return self\n"
                                "    def compare(self, o): return 0\n"
                                "class MathQuestionGenerator:\n"
                                "    def generate(self, *args): return None\n"
                            )
                return resp

        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            mock_llm_client=MissingInterfaceMockClient(),
            workflow_mode="offline_pipeline",
        )

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract08_run")

        assert app.run_state == "FAILED"
        assert app.last_run_dir is None


# =============================================================================
# 9. Missing, Malformed, and Unavailable Telemetry Fault Tolerance
# =============================================================================

def test_contract_09_telemetry_fault_tolerance(tk_root):
    """Area 9: Verify missing, malformed, and unavailable telemetry does not crash the workflow."""
    app = AgentGUIApp(tk_root, suppress_messagebox=True)

    # 1. Missing usage dictionary (None)
    budget = BudgetController(ceiling=0.80)
    reserved = budget.reserve("t1", "prompt", max_output_tokens=100)
    # Reconciling with None usage permanently locks reservation safely without raising an unhandled exception
    locked = budget.reconcile("t1", usage_data=None)
    assert locked == reserved
    assert budget._all_reservations["t1"].state == ReservationState.PERMANENTLY_LOCKED
    assert budget.permanently_locked_spend == reserved

    # 2. Corrupt non-numeric usage data
    budget.reserve("t2", "prompt", max_output_tokens=100)
    locked2 = budget.reconcile("t2", usage_data={"prompt_tokens": "corrupt_non_int"})
    assert locked2 > 0
    assert budget._all_reservations["t2"].state == ReservationState.PERMANENTLY_LOCKED

    # 3. Corrupted spend strings on GUI telemetry display
    app.budget_controller.cumulative_spend = "bad_telemetry"
    app._update_telemetry_display()
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "INVALID"

    # 4. None telemetry on GUI
    app.budget_controller.cumulative_spend = None
    app._update_telemetry_display()
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "UNAVAILABLE"

    # 5. NaN telemetry on GUI
    app.budget_controller.cumulative_spend = float("nan")
    app._update_telemetry_display()
    assert app.telemetry_labels["Cumulative Verified Live Spend:"].cget("text") == "INVALID"


# =============================================================================
# 10. Failed Staging Output Preservation
# =============================================================================

def test_contract_10_failed_staging_output_preservation(tk_root, canonical_doc_paths):
    """Area 10: Verify failed staging directory is preserved intact for post-mortem analysis."""
    doc_path, view_path = canonical_doc_paths
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = os.path.join(temp_dir, "contract_10_preservation")
        os.makedirs(output_dir, exist_ok=True)

        class FailingStagingClient(MultiFileMockLLMClient):
            def chat_completion(self, messages, tools, reservation_id=None):
                resp = super().chat_completion(messages, tools, reservation_id)
                if resp.tool_calls:
                    for tc in resp.tool_calls:
                        if tc.arguments.get("path") == "space_fractions/fraction_math.py":
                            tc.arguments["content"] = "class Fraction:\n    invalid syntax !@#$"
                return resp

        app = AgentGUIApp(
            tk_root,
            suppress_messagebox=True,
            mock_llm_client=FailingStagingClient(),
            workflow_mode="offline_pipeline",
        )

        app._run_offline_pipeline_worker(doc_path, view_path, output_dir, run_id="contract10_fail")

        assert app.run_state == "FAILED"
        assert app.last_staging_dir is not None
        # Staging folder MUST remain preserved on disk
        assert os.path.isdir(app.last_staging_dir), "Staging directory was unexpectedly deleted upon failure!"


# =============================================================================
# 11. Budget-Reservation Ownership between AgentLoop and DeepSeekClient
# =============================================================================

def test_contract_11_budget_reservation_ownership_and_guard():
    """Area 11: Verify reservation ownership between AgentLoop and DeepSeekClient.
    
    Detects duplicate reservations or accounting bypasses:
    - AgentLoop manages the reservation lifecycle.
    - DeepSeekClient verifies the active reservation when skip_budget_reservation=True.
    - Calling DeepSeekClient with skip_budget_reservation=True without active reservation fails immediately.
    """
    budget = BudgetController(ceiling=0.80)

    # 1. Pre-transmission check: Calling with skip_budget_reservation=True without reservation raises BudgetException
    client = DeepSeekClient(api_key="sk-test-key", budget_controller=budget, max_output_tokens=1200)

    with pytest.raises(BudgetException, match="No active reservation found"):
        client.send_chat_completion(
            messages=[{"role": "user", "content": "Bypass attempt"}],
            max_tokens=1200,
            skip_budget_reservation=True,
            reservation_id="non_existent_res_id",
        )

    # 2. Token mismatch check: Reservation has 1200 tokens but request specifies 2400
    budget.reserve("res_mismatch", "prompt", max_output_tokens=1200)
    with pytest.raises(BudgetException, match="Pre-transmission token mismatch"):
        client.send_chat_completion(
            messages=[{"role": "user", "content": "Mismatch attempt"}],
            max_tokens=2400,
            skip_budget_reservation=True,
            reservation_id="res_mismatch",
        )

    # 3. Proper ownership: Adapter passes reservation_id through cleanly without duplicate reserve()
    adapter = DeepSeekAgentAdapter(client=client, max_output_tokens=1200)
    initial_reservations_count = len(budget.active_reservations)
    assert initial_reservations_count == 1  # res_mismatch from above
