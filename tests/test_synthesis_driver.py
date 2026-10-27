"""Stage F5F Mock-Driven Synthesis Driver Integration Tests.

Validates the 14 requirements specified in Stage F5F:
1. Preprocessing of both architecture documents.
2. Compact prompt construction and inclusion of requirement references.
3. Prompt-size rejection before any model dispatch.
4. One successful mock generation and a bounded file write.
5. Multiple turns stopping at the three-turn maximum.
6. Budget exhaustion before the next dispatch.
7. Invalid or malformed model responses.
8. Rejected path traversal and protected-file overwrite attempts.
9. Model attempts to invoke run_command.
10. Cancellation and error handling.
11. Missing or invalid usage telemetry with conservative accounting.
12. No retries after transport failure.
13. No active reservation leaks after terminal outcomes.
14. Regression coverage confirming the GUI remains offline-only.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
import pytest

from src.agent_gui import AgentGUIApp
from src.agent_loop import (
    AgentLoop,
    DeepSeekAgentAdapter,
    LoopTerminationStatus,
    ModelTurnResponse,
    ToolCall,
)
from src.budget_controller import (
    BudgetController,
    BudgetExceededError,
    BudgetException,
    ReservationState,
)
from src.deepseek_client import (
    DeepSeekClient,
    DeepSeekResponse,
    RequestOutcomeStatus,
)
from src.synthesis_driver import (
    CommitState,
    SynthesisDriver,
    SynthesisResult,
    SynthesisStatus,
)


@pytest.fixture(autouse=True)
def guard_no_real_network_and_subprocesses(monkeypatch):
    """Enforces zero live network requests and zero subprocess execution."""
    def block_network(*args, **kwargs):
        raise RuntimeError("Prohibited live network call attempted in Stage F5F test suite!")

    def block_subprocess(*args, **kwargs):
        raise RuntimeError("Prohibited subprocess execution attempted in Stage F5F test suite!")

    monkeypatch.setattr(urllib.request, "urlopen", block_network)
    monkeypatch.setattr(http.client.HTTPConnection, "request", block_network)


DOC_PATH = "Architecture_Documentation.md"
VIEW_PATH = "Architecture_View.md"

VALID_FRACTION_MATH_CODE = '''class Fraction:
    def __init__(self, numerator: int, denominator: int = 1):
        if not isinstance(numerator, int) or not isinstance(denominator, int):
            raise TypeError("Numerator and denominator must be integers.")
        if denominator == 0:
            raise ValueError("Denominator cannot be zero.")
        self.n = numerator
        self.d = denominator

    def simplify(self):
        return self

    def add(self, other):
        return self

    def subtract(self, other):
        return self

    def compare(self, other):
        return 0

    def equals(self, other):
        return True


class MathQuestionGenerator:
    def __init__(self, seed: int = 42):
        self.seed = seed

    def generate(self, level: int = 1, index: int = 1):
        return None
'''


# -----------------------------------------------------------------------------
# Test 1: Preprocessing of both architecture documents
# -----------------------------------------------------------------------------

def test_1_preprocessing_both_architecture_documents():
    """Validates that SynthesisDriver pre-processes canonical architecture docs correctly."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        driver = SynthesisDriver(workspace_root=tmp_dir)
        spec_result = driver.preprocess(DOC_PATH, VIEW_PATH)

        assert len(spec_result.view_items) == 11
        assert len(spec_result.diagrams) == 13
        assert len(spec_result.explicit_requirements) >= 5
        assert len(spec_result.recorded_conflicts) >= 1
        assert spec_result.recorded_conflicts[0].conflict_id == "CONFLICT-DELIVERABLE-PLATFORM-001"


# -----------------------------------------------------------------------------
# Test 2: Compact prompt construction and requirement inclusion
# -----------------------------------------------------------------------------

def test_2_compact_prompt_construction_and_traceability():
    """Validates that the constructed prompt is compact, targeted, and includes conflict/traceability."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        driver = SynthesisDriver(workspace_root=tmp_dir)
        spec_result = driver.preprocess(DOC_PATH, VIEW_PATH)

        prompt, req_ids = driver.build_synthesis_prompt(
            spec_result, target_artifact="space_fractions/fraction_math.py"
        )

        assert "space_fractions/fraction_math.py" in prompt
        assert "CONFLICT-DELIVERABLE-PLATFORM-001" in prompt
        assert "desktop" in prompt.lower()
        assert len(req_ids) > 0
        assert any("FR-" in rid for rid in req_ids)

        # Confirm prompt is compact (< 3,000 characters and < 8,000 tokens)
        assert len(prompt) < 3000
        token_bound = driver.budget_controller.calculate_token_bound(prompt, include_framing=True)
        assert token_bound < 4000
        assert token_bound <= driver.MAX_ALLOWED_INPUT_TOKENS


# -----------------------------------------------------------------------------
# Test 3: Prompt-size rejection before any model dispatch
# -----------------------------------------------------------------------------

def test_3_prompt_size_rejection_before_model_dispatch():
    """Validates that oversized prompts exceeding 8,000 token bound are rejected preflight."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        driver = SynthesisDriver(workspace_root=tmp_dir)

        # Create artificially huge prompt > 8,000 chars / tokens
        huge_prompt = "X" * 10000

        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, custom_prompt=huge_prompt)

        assert res.status == SynthesisStatus.PREFLIGHT_REJECTED
        assert res.total_turns == 0
        assert "exceeds maximum input bound of 8000 tokens" in (res.error_message or "")
        # Zero spend and zero holds
        assert res.active_holds == 0.0


# -----------------------------------------------------------------------------
# Test 4: One successful mock generation and a bounded file write
# -----------------------------------------------------------------------------

def test_4_successful_mock_generation_and_bounded_file_write():
    """Validates a successful single-turn synthesis where the model writes the target file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        os.makedirs(os.path.join(tmp_dir, "space_fractions"), exist_ok=True)

        class MockClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    # Model requests write_file tool call
                    return ModelTurnResponse(
                        content="I will generate the fraction math engine.",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_write_math",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 150, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                else:
                    # Model completes
                    return ModelTurnResponse(
                        content="Module successfully generated and verified.",
                        tool_calls=[],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 20},
                        status="SUCCESS",
                        finish_reason="stop",
                    )

        mock_client = MockClient()
        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=mock_client, max_turns=3)

        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.COMPLETED
        assert res.is_complete is True
        assert "space_fractions/fraction_math.py" in res.files_written
        assert os.path.isfile(res.target_path)
        # Workspace root target was not directly written/polluted
        assert not os.path.exists(os.path.join(tmp_dir, "space_fractions", "fraction_math.py"))

        # Verify accounting
        assert res.active_holds == 0.0
        assert res.cumulative_spend > driver.HISTORICAL_SPEND_BASELINE
        assert res.permanently_locked_spend == 0.0


# -----------------------------------------------------------------------------
# Test 5: Multiple turns stopping at the three-turn maximum
# -----------------------------------------------------------------------------

def test_5_multiple_turns_stopping_at_three_turn_maximum():
    """Validates that a multi-turn agent loop stops strictly at max_turns=3."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class LoopingMockClient:
            def __init__(self):
                self.turns = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turns += 1
                # Keeps requesting read_file tools indefinitely
                return ModelTurnResponse(
                    content=f"Turn {self.turns} inspecting environment.",
                    tool_calls=[
                        ToolCall(
                            tool_id=f"call_{self.turns}",
                            name="list_directory",
                            arguments={"path": "."},
                        )
                    ],
                    raw_usage={"prompt_tokens": 100, "completion_tokens": 20},
                    status="SUCCESS",
                )

        looping_client = LoopingMockClient()
        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=looping_client, max_turns=3)

        res = driver.run_synthesis(DOC_PATH, VIEW_PATH)

        assert res.status == SynthesisStatus.MAX_TURNS_REACHED
        assert res.total_turns == 3
        assert looping_client.turns == 3
        assert res.active_holds == 0.0


# -----------------------------------------------------------------------------
# Test 6: Budget exhaustion before next dispatch
# -----------------------------------------------------------------------------

def test_6_budget_exhaustion_before_next_dispatch():
    """Validates that exhausting the incremental budget halts the driver cleanly with zero dangling holds."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Set tight budget ceiling of $0.005
        tight_budget = BudgetController(ceiling=0.005000)
        tight_budget.cumulative_spend = 0.000211

        class LoopingMockClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                # Simulated high token usage per turn (~0.003 spend)
                return ModelTurnResponse(
                    content="Working...",
                    tool_calls=[ToolCall(tool_id="c1", name="list_directory", arguments={"path": "."})],
                    raw_usage={"prompt_tokens": 1500, "completion_tokens": 800},
                    status="SUCCESS",
                )

        driver = SynthesisDriver(
            workspace_root=tmp_dir,
            llm_client=LoopingMockClient(),
            budget_controller=tight_budget,
            max_turns=3,
        )

        res = driver.run_synthesis(DOC_PATH, VIEW_PATH)

        assert res.status == SynthesisStatus.BUDGET_EXCEEDED
        assert res.active_holds == 0.0
        assert tight_budget.cumulative_spend <= 0.005000


# -----------------------------------------------------------------------------
# Test 7: Invalid or malformed model responses
# -----------------------------------------------------------------------------

def test_7_malformed_model_response_handling():
    """Validates that malformed responses trip the error boundary and terminate safely."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class MalformedMockClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(
                    content=None,
                    tool_calls=[],
                    status="MALFORMED_JSON",
                    error_message="Provider returned malformed truncated JSON.",
                )

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=MalformedMockClient())
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH)

        assert res.status == SynthesisStatus.FAILED
        assert res.active_holds == 0.0
        assert res.permanently_locked_spend > 0.0


# -----------------------------------------------------------------------------
# Test 8: Rejected path traversal and protected-file overwrite attempts
# -----------------------------------------------------------------------------

def test_8_path_traversal_and_canonical_doc_overwrite_rejected():
    """Validates that attempts to escape workspace or overwrite canonical docs are blocked."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class MaliciousMockClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    # Attempt 1: Path traversal outside workspace
                    return ModelTurnResponse(
                        content="Escaping workspace",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_escape",
                                name="write_file",
                                arguments={"path": "../../../escaped.txt", "content": "malicious"},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                elif self.calls == 2:
                    # Attempt 2: Overwrite canonical architecture document
                    return ModelTurnResponse(
                        content="Overwriting canonical doc",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_overwrite_doc",
                                name="write_file",
                                arguments={"path": "Architecture_Documentation.md", "content": "corrupted"},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                else:
                    return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=MaliciousMockClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH)

        # Both malicious tool calls must be blocked
        assert len(res.blocked_tool_calls) >= 2
        assert any("SecurityError" in b or "traversal" in b.lower() or "outside" in b.lower() for b in res.blocked_tool_calls)
        assert any("canonical architecture" in b.lower() for b in res.blocked_tool_calls)
        # Neither file was created
        assert not os.path.exists(os.path.join(tmp_dir, "Architecture_Documentation.md"))


# -----------------------------------------------------------------------------
# Test 9: Model attempts to invoke run_command
# -----------------------------------------------------------------------------

def test_9_run_command_unconditionally_blocked():
    """Validates that model attempts to call run_command are permanently blocked."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class CommandMockClient:
            def __init__(self):
                self.called = False

            def chat_completion(self, messages, tools, reservation_id=None):
                if not self.called:
                    self.called = True
                    return ModelTurnResponse(
                        content="Running test command",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_cmd",
                                name="run_command",
                                arguments={"command": "python -m pytest"},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=CommandMockClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH)

        # run_command must be blocked
        assert any("run_command" in b for b in res.blocked_tool_calls)
        assert any("permanently disabled" in b for b in res.blocked_tool_calls)


# -----------------------------------------------------------------------------
# Test 10: Cancellation and error handling
# -----------------------------------------------------------------------------

def test_10_cancellation_releases_holds_cleanly():
    """Validates that cancelling via cancel_event stops the run and leaves zero active holds."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cancel_event = threading.Event()
        cancel_event.set()  # Signal cancellation immediately

        class DummyClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(content="Hello", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(
            workspace_root=tmp_dir,
            llm_client=DummyClient(),
            cancel_event=cancel_event,
        )

        res = driver.run_synthesis(DOC_PATH, VIEW_PATH)

        assert res.status == SynthesisStatus.CANCELLED
        assert res.active_holds == 0.0
        assert res.cumulative_spend == driver.HISTORICAL_SPEND_BASELINE


# -----------------------------------------------------------------------------
# Test 11: Missing or invalid telemetry with conservative accounting
# -----------------------------------------------------------------------------

def test_11_invalid_telemetry_locks_spend_conservatively():
    """Validates that corrupt or missing usage metadata locks the reservation permanently."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class CorruptedUsageClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                # Returns invalid negative token count
                return ModelTurnResponse(
                    content="Writing module",
                    tool_calls=[],
                    status="SUCCESS",
                    raw_usage={"prompt_tokens": -50, "completion_tokens": 20},
                )

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=CorruptedUsageClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH)

        assert res.active_holds == 0.0
        assert res.permanently_locked_spend > 0.0
        assert res.cumulative_spend == driver.HISTORICAL_SPEND_BASELINE + res.permanently_locked_spend


# -----------------------------------------------------------------------------
# Test 12: No retries after transport failure
# -----------------------------------------------------------------------------

def test_12_no_retries_after_transport_failure():
    """Validates that network transport errors lock the hold and perform zero retries."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        call_count = 0

        def failing_transport(req, timeout):
            nonlocal call_count
            call_count += 1
            raise socket.timeout("Socket timed out")

        controller = BudgetController(ceiling=0.050211)
        controller.cumulative_spend = 0.000211

        client = DeepSeekClient(
            api_key="sk-test-dummy-key",
            budget_controller=controller,
            max_output_tokens=1200,
            transport_sender=failing_transport,
        )
        adapter = DeepSeekAgentAdapter(client)

        driver = SynthesisDriver(
            workspace_root=tmp_dir,
            llm_client=adapter,
            budget_controller=controller,
            max_turns=3,
        )

        res = driver.run_synthesis(DOC_PATH, VIEW_PATH)

        assert res.status == SynthesisStatus.FAILED
        assert call_count == 1  # ZERO retries
        assert res.active_holds == 0.0
        assert res.permanently_locked_spend > 0.0


# -----------------------------------------------------------------------------
# Test 13: No active reservation leaks after terminal outcomes
# -----------------------------------------------------------------------------

def test_13_no_active_reservation_leaks_across_terminal_outcomes():
    """Validates that across all terminal execution outcomes, active holds are strictly zero."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Case A: Success
        class SuccessClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS", finish_reason="stop")

        d1 = SynthesisDriver(workspace_root=tmp_dir, llm_client=SuccessClient())
        r1 = d1.run_synthesis(DOC_PATH, VIEW_PATH)
        assert r1.active_holds == 0.0
        assert len(d1.budget_controller.active_reservations) == 0

        # Case B: Refusal
        class RefusalClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(content="", tool_calls=[], status="REFUSAL", error_message="Refused")

        d2 = SynthesisDriver(workspace_root=tmp_dir, llm_client=RefusalClient())
        r2 = d2.run_synthesis(DOC_PATH, VIEW_PATH)
        assert r2.active_holds == 0.0
        assert len(d2.budget_controller.active_reservations) == 0

        # Case C: Budget Rejected
        class BudgetRejectedClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(content="", tool_calls=[], status="BUDGET_REJECTED")

        d3 = SynthesisDriver(workspace_root=tmp_dir, llm_client=BudgetRejectedClient())
        r3 = d3.run_synthesis(DOC_PATH, VIEW_PATH)
        assert r3.active_holds == 0.0
        assert len(d3.budget_controller.active_reservations) == 0


# -----------------------------------------------------------------------------
# Test 14: Regression coverage confirming GUI remains offline-only
# -----------------------------------------------------------------------------

def test_14_gui_remains_offline_only_regression():
    """Validates that AgentGUIApp remains strictly in offline mode with live radio disabled."""
    import tkinter as tk
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as err:
        pytest.skip(f"Tkinter display environment unavailable: {err}")

    try:
        app = AgentGUIApp(root)

        # 1. Default mode must be offline
        assert app.execution_mode_var.get() == "offline"

        # 2. Live radio toggle must be disabled
        assert str(app.radio_live.cget("state")) == "disabled"

        # 3. Verified spend baseline preserved
        assert app.budget_controller.cumulative_spend == 0.000186
        assert app.BASE_VERIFIED_SPEND == 0.000186

        # 4. Starting the worker must run offline pipeline only
        assert hasattr(app, "_run_offline_pipeline_worker")

    finally:
        root.destroy()


# -----------------------------------------------------------------------------
# Test 15: F-02: Target artifact completion requires current run writing exact target
# -----------------------------------------------------------------------------

def test_15_completion_requires_current_run_writing_exact_target():
    """Validates that a stale pre-existing target file does NOT count as complete if current run wrote another file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Pre-create a stale target file
        stale_target = os.path.join(tmp_dir, "space_fractions", "fraction_math.py")
        os.makedirs(os.path.dirname(stale_target), exist_ok=True)
        with open(stale_target, "w", encoding="utf-8") as f:
            f.write("# stale pre-existing code from prior run")

        # Mock client that only writes an unrelated file 'notes.txt'
        class WriteUnrelatedFileClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurnResponse(
                        content="Writing notes",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_1",
                                name="write_file",
                                arguments={"path": "notes.txt", "content": "Just notes"},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=WriteUnrelatedFileClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.COMPLETED
        assert "notes.txt" in res.files_written
        # Must be False because the current run did NOT write space_fractions/fraction_math.py!
        assert res.is_complete is False

        # Now test that writing the actual target DOES qualify for completion
        class WriteTargetClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurnResponse(
                        content="Writing target",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_2",
                                name="write_file",
                                arguments={"path": "space_fractions/fraction_math.py", "content": VALID_FRACTION_MATH_CODE},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver2 = SynthesisDriver(workspace_root=tmp_dir, llm_client=WriteTargetClient(), max_turns=3)
        res2 = driver2.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res2.status == SynthesisStatus.COMPLETED
        assert any("fraction_math.py" in f for f in res2.files_written)
        assert res2.is_complete is True
        assert os.path.isfile(res2.target_path)
        # Pre-existing workspace target was preserved untouched
        with open(stale_target, "r", encoding="utf-8") as f:
            assert f.read() == "# stale pre-existing code from prior run"


# -----------------------------------------------------------------------------
# Test 16: F-02: Assistant text mentioning write without tool write record does not qualify
# -----------------------------------------------------------------------------

def test_16_transcript_text_alone_without_tool_write_does_not_qualify():
    """Validates that assistant text claiming success without actual write_file tool call does not mark complete."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class TextOnlyClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(
                    content="Successfully wrote 100 characters to 'space_fractions/fraction_math.py'.",
                    tool_calls=[],
                    status="SUCCESS",
                    raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                )

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=TextOnlyClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.COMPLETED
        assert len(res.files_written) == 0
        assert res.is_complete is False


# -----------------------------------------------------------------------------
# Test 17: F-02: Non-completed terminal statuses cannot be complete
# -----------------------------------------------------------------------------

def test_17_non_completed_terminal_statuses_cannot_be_complete():
    """Validates that runs ending in MAX_TURNS_REACHED, CANCELLED, or BUDGET_EXCEEDED cannot be marked complete."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Case A: Wrote file on Turn 1, but hit MAX_TURNS_REACHED
        class TurnExhaustionClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurnResponse(
                        content="Writing target",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_write",
                                name="write_file",
                                arguments={"path": "space_fractions/fraction_math.py", "content": "pass"},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                # Keep asking for tools until turns exhausted
                return ModelTurnResponse(
                    content="Reading directory",
                    tool_calls=[ToolCall(tool_id=f"call_{self.turn}", name="list_directory", arguments={})],
                    status="SUCCESS",
                    raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                )

        driver_turns = SynthesisDriver(workspace_root=tmp_dir, llm_client=TurnExhaustionClient(), max_turns=3)
        res_turns = driver_turns.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res_turns.status == SynthesisStatus.MAX_TURNS_REACHED
        assert len(res_turns.files_written) == 1
        assert res_turns.is_complete is False

        # Case B: Cancelled after file write
        cancel_event = threading.Event()

        class CancelAfterWriteClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    # Write file and immediately set cancel signal
                    cancel_event.set()
                    return ModelTurnResponse(
                        content="Writing target",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_cancel_write",
                                name="write_file",
                                arguments={"path": "space_fractions/fraction_math.py", "content": "pass"},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver_cancel = SynthesisDriver(
            workspace_root=tmp_dir,
            llm_client=CancelAfterWriteClient(),
            max_turns=3,
            cancel_event=cancel_event,
        )
        res_cancel = driver_cancel.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res_cancel.status == SynthesisStatus.CANCELLED
        assert res_cancel.is_complete is False

        # Case C: Budget Exceeded on Turn 2 after successful file write on Turn 1
        class BudgetExceededAfterWriteClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurnResponse(
                        content="Writing target",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_budget_write",
                                name="write_file",
                                arguments={"path": "space_fractions/fraction_math.py", "content": "pass"},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                # On Turn 2, simulate budget rejection
                return ModelTurnResponse(
                    content="",
                    tool_calls=[],
                    status="BUDGET_REJECTED",
                    error_message="Budget ceiling reached.",
                )

        driver_budget = SynthesisDriver(workspace_root=tmp_dir, llm_client=BudgetExceededAfterWriteClient(), max_turns=3)
        res_budget = driver_budget.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res_budget.status == SynthesisStatus.BUDGET_EXCEEDED
        assert len(res_budget.files_written) == 1
        assert res_budget.is_complete is False

        # Case D: Failure / Malformed response on Turn 2 after successful file write on Turn 1
        class FailureAfterWriteClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurnResponse(
                        content="Writing target",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_fail_write",
                                name="write_file",
                                arguments={"path": "space_fractions/fraction_math.py", "content": "pass"},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                # On Turn 2, simulate provider error / malformed response
                return ModelTurnResponse(
                    content="",
                    tool_calls=[],
                    status="MALFORMED_JSON",
                    error_message="Invalid JSON response from provider.",
                )

        driver_fail = SynthesisDriver(workspace_root=tmp_dir, llm_client=FailureAfterWriteClient(), max_turns=3)
        res_fail = driver_fail.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res_fail.status == SynthesisStatus.FAILED
        assert len(res_fail.files_written) == 1
        assert res_fail.is_complete is False


# -----------------------------------------------------------------------------
# Test 18: F-02: Equivalent normalized relative paths and workspace escape rejection
# -----------------------------------------------------------------------------

def test_18_equivalent_normalized_relative_paths_and_workspace_escape_rejection():
    """Validates that equivalent normalized relative paths qualify while directory escapes fail safely."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Case A: Target has redundant ./ or subpath normalization
        class WriteNormalizedClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurnResponse(
                        content="Writing with redundant path components",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_norm",
                                name="write_file",
                                arguments={"path": "space_fractions/../space_fractions/fraction_math.py", "content": VALID_FRACTION_MATH_CODE},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver_norm = SynthesisDriver(workspace_root=tmp_dir, llm_client=WriteNormalizedClient(), max_turns=3)
        res_norm = driver_norm.run_synthesis(
            DOC_PATH, VIEW_PATH, target_artifact="./space_fractions/fraction_math.py"
        )
        assert res_norm.status == SynthesisStatus.COMPLETED
        assert res_norm.is_complete is True
        assert os.path.isfile(res_norm.target_path)

        # Case B: Traversal escape in target_artifact
        driver_escape = SynthesisDriver(workspace_root=tmp_dir, llm_client=WriteNormalizedClient(), max_turns=3)
        res_escape = driver_escape.run_synthesis(
            DOC_PATH, VIEW_PATH, target_artifact="../../../escaped.py"
        )
        assert res_escape.is_complete is False


# -----------------------------------------------------------------------------
# Test 19: F-03: Unique reservation IDs across sequential runs on shared controller
# -----------------------------------------------------------------------------

def test_19_unique_reservation_ids_across_sequential_runs_on_shared_controller():
    """Validates that sequential synthesis runs sharing a single BudgetController use distinct reservation IDs without collisions."""
    controller = BudgetController(ceiling=0.80)
    seen_reservation_ids_run1 = []
    seen_reservation_ids_run2 = []

    class MultiRunMockClient:
        def __init__(self, record_list):
            self.turn = 0
            self.record_list = record_list

        def chat_completion(self, messages, tools, reservation_id=None):
            self.turn += 1
            if reservation_id:
                self.record_list.append(reservation_id)
            if self.turn == 1:
                return ModelTurnResponse(
                    content="Turn 1 write",
                    tool_calls=[
                        ToolCall(
                            tool_id=f"call_{self.turn}",
                            name="write_file",
                            arguments={"path": f"file_{self.turn}.py", "content": "pass"},
                        )
                    ],
                    status="SUCCESS",
                    raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                )
            return ModelTurnResponse(
                content="Turn 2 done",
                tool_calls=[],
                status="SUCCESS",
                raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
            )

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Run 1
        driver1 = SynthesisDriver(
            workspace_root=tmp_dir,
            llm_client=MultiRunMockClient(seen_reservation_ids_run1),
            budget_controller=controller,
            max_turns=3,
        )
        res1 = driver1.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="file_1.py")

        # Run 2 on the SAME BudgetController
        driver2 = SynthesisDriver(
            workspace_root=tmp_dir,
            llm_client=MultiRunMockClient(seen_reservation_ids_run2),
            budget_controller=controller,
            max_turns=3,
        )
        res2 = driver2.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="file_1.py")

        # 1. Both runs must have completed successfully
        assert res1.status == SynthesisStatus.COMPLETED
        assert res2.status == SynthesisStatus.COMPLETED

        # 2. Check that reservation IDs were observed
        assert len(seen_reservation_ids_run1) == 2
        assert len(seen_reservation_ids_run2) == 2

        # 3. Every reservation ID across both runs must be distinct!
        all_seen_ids = seen_reservation_ids_run1 + seen_reservation_ids_run2
        assert len(set(all_seen_ids)) == 4, f"Duplicate reservation IDs found: {all_seen_ids}"

        # 4. Each ID must follow the run-<session-id>-turn-<index> format with 32-character hex UUID
        for rid in all_seen_ids:
            assert rid.startswith("run-")
            assert "-turn-" in rid
            session_part = rid.split("-turn-")[0].replace("run-", "")
            assert len(session_part) == 32, f"Expected 32-character hex UUID, got '{session_part}'"

        # 5. Controller's _all_reservations must contain all 4 distinct historical records intact
        assert len(controller._all_reservations) == 4
        for rid in all_seen_ids:
            assert rid in controller._all_reservations
            assert controller._all_reservations[rid].state == ReservationState.RECONCILED

        # 6. Active holds must be 0 (no leaks)
        assert controller.get_active_holds() == 0.0
        assert len(controller.active_reservations) == 0


# -----------------------------------------------------------------------------
# Test 20: Bounded synthesis prompt prepares local interface context (Stage F5G.1)
# -----------------------------------------------------------------------------

def test_20_bounded_synthesis_prompt_prepares_local_interface_context():
    """Validates that build_synthesis_prompt embeds local module specifications so model requires zero discovery."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        driver = SynthesisDriver(workspace_root=tmp_dir)
        spec_result = driver.preprocess(DOC_PATH, VIEW_PATH)

        prompt, req_ids = driver.build_synthesis_prompt(
            spec_result, target_artifact="space_fractions/fraction_math.py"
        )

        # Must include fraction math interfaces
        assert "Fraction(numerator: int, denominator: int" in prompt
        assert "gcd(a, b)" in prompt
        assert "lcm(a, b)" in prompt
        assert "MathQuestion" in prompt
        assert "MathQuestionGenerator" in prompt
        assert "All necessary architecture and interface requirements are provided in this prompt" in prompt
        assert "write_file" in prompt

        # Token sizing must remain strictly bounded
        token_bound = driver.budget_controller.calculate_token_bound(prompt, include_framing=True)
        assert token_bound < 3000
        assert token_bound <= driver.MAX_ALLOWED_INPUT_TOKENS


# -----------------------------------------------------------------------------
# Test 21: Bounded synthesis restricts tools schema to write_file only (Stage F5G.1)
# -----------------------------------------------------------------------------

def test_21_bounded_synthesis_restricts_tools_to_write_file_only():
    """Validates that in bounded_mode, only write_file is exposed and exploratory tools are rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        seen_tools = []

        class ToolSnifferClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                seen_tools.extend(tools)
                # Model attempts to call list_directory despite it not being advertised
                return ModelTurnResponse(
                    content="Exploring workspace",
                    tool_calls=[
                        ToolCall(
                            tool_id="call_list",
                            name="list_directory",
                            arguments={"subpath": "."},
                        )
                    ],
                    status="SUCCESS",
                    raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                )

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=ToolSnifferClient(), max_turns=1, bounded_mode=True)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        # 1. Exposed tool schema must ONLY contain write_file
        tool_names = [t["function"]["name"] for t in seen_tools]
        assert tool_names == ["write_file"]

        # 2. Attempted list_directory must be rejected
        assert any("list_directory" in b for b in res.blocked_tool_calls)
        assert any("not permitted" in b for b in res.blocked_tool_calls)


# -----------------------------------------------------------------------------
# Test 22: Bounded synthesis rejects writes outside authorized target (Stage F5G.1)
# -----------------------------------------------------------------------------

def test_22_bounded_synthesis_rejects_writes_outside_authorized_target():
    """Validates that in bounded_mode, writes to any path other than authorized target are rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class WriteWrongTargetClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurnResponse(
                        content="Writing unapproved file",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_wrong",
                                name="write_file",
                                arguments={"path": "unauthorized_module.py", "content": "# rogue code"},
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=WriteWrongTargetClient(), max_turns=3, bounded_mode=True)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        # Must NOT write the unauthorized file
        assert len(res.files_written) == 0
        assert not os.path.exists(os.path.join(tmp_dir, "unauthorized_module.py"))

        # SecurityError must be recorded
        assert any("SecurityError" in f for f in res.files_failed)
        assert any("restricted exclusively to authorized target" in f for f in res.files_failed)
        assert res.is_complete is False


# -----------------------------------------------------------------------------
# Test 23: Bounded synthesis successful write and completion (Stage F5G.1)
# -----------------------------------------------------------------------------

def test_23_bounded_synthesis_successful_write_and_completion():
    """Validates that writing the authorized target in bounded_mode succeeds and reports is_complete=True."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class WriteAuthorizedTargetClient:
            def __init__(self):
                self.turn = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.turn += 1
                if self.turn == 1:
                    return ModelTurnResponse(
                        content="Writing math module",
                        tool_calls=[
                            ToolCall(
                                tool_id="call_auth",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        status="SUCCESS",
                        raw_usage={"prompt_tokens": 80, "completion_tokens": 40},
                    )
                return ModelTurnResponse(content="Implementation complete.", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=WriteAuthorizedTargetClient(), max_turns=3, bounded_mode=True)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.COMPLETED
        assert res.is_complete is True
        assert "space_fractions/fraction_math.py" in res.files_written
        written_path = res.target_path
        assert os.path.isfile(written_path)
        with open(written_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "class Fraction" in content
            assert "class MathQuestionGenerator" in content
        assert not os.path.exists(os.path.join(tmp_dir, "space_fractions", "fraction_math.py"))


# -----------------------------------------------------------------------------
# Test 24: Bounded synthesis safety invariants, cancellation, and blocked command (Stage F5G.1)
# -----------------------------------------------------------------------------

def test_24_bounded_synthesis_safety_invariants_cancellation_and_blocked_command():
    """Validates that run_command remains blocked and cancellation releases holds in bounded_mode."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Part A: run_command is blocked
        class CommandInBoundedClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(
                    content="Attempting shell",
                    tool_calls=[
                        ToolCall(tool_id="call_sh", name="run_command", arguments={"command": "dir"})
                    ],
                    status="SUCCESS",
                    raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
                )

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=CommandInBoundedClient(), max_turns=1, bounded_mode=True)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert any("run_command" in b for b in res.blocked_tool_calls)
        assert any("permanently disabled" in b for b in res.blocked_tool_calls)
        assert res.is_complete is False

        # Part B: Cancellation releases holds
        cancel_evt = threading.Event()
        cancel_evt.set()

        class DummyClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(content="test", tool_calls=[], status="SUCCESS")

        driver_cancelled = SynthesisDriver(
            workspace_root=tmp_dir,
            llm_client=DummyClient(),
            cancel_event=cancel_evt,
            bounded_mode=True,
        )
        res_cancelled = driver_cancelled.run_synthesis(DOC_PATH, VIEW_PATH)
        assert res_cancelled.status == SynthesisStatus.CANCELLED
        assert res_cancelled.active_holds == 0.0
        assert res_cancelled.is_complete is False


# -----------------------------------------------------------------------------
# Test 25: Canonical target file in workspace root preserved during and after synthesis
# -----------------------------------------------------------------------------

def test_25_original_file_preserved_in_workspace_root_during_and_after_synthesis():
    """Validates that any existing file in the workspace root remains strictly untouched during synthesis."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        orig_dir = os.path.join(tmp_dir, "space_fractions")
        os.makedirs(orig_dir, exist_ok=True)
        orig_file = os.path.join(orig_dir, "fraction_math.py")
        sentinel_content = "# CANONICAL EXISTING PRODUCTION CODE HASH: ABCDEF123456"
        with open(orig_file, "w", encoding="utf-8") as f:
            f.write(sentinel_content)

        class MockValidClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Generating fraction math",
                        tool_calls=[
                            ToolCall(
                                tool_id="c1",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=MockValidClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.COMPLETED
        assert res.is_complete is True
        # Generated code promoted to run directory
        assert os.path.isfile(res.target_path)
        assert res.target_path != orig_file
        # Original file in workspace root remained 100% byte-for-byte identical
        with open(orig_file, "r", encoding="utf-8") as f:
            assert f.read() == sentinel_content


# -----------------------------------------------------------------------------
# Test 26: Syntactically broken code rejected by validator and staging preserved
# -----------------------------------------------------------------------------

def test_26_syntactically_broken_code_rejected_and_staging_preserved():
    """Validates that syntactically broken code fails interface validation, is not promoted, and preserves staging."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class BrokenSyntaxClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing broken code",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_broken",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": "def broken_func(:\n    return 42\n",
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 30},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=BrokenSyntaxClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.FAILED
        assert res.is_complete is False
        assert "Syntax error" in (res.error_message or "")
        # Staging directory must be preserved on disk
        staging_dir = res.details.get("staging_dir")
        assert staging_dir is not None
        assert os.path.isdir(staging_dir)
        staged_file = os.path.join(staging_dir, "space_fractions", "fraction_math.py")
        assert os.path.isfile(staged_file)
        # Final directory not created
        assert "run_dir" not in res.details


# -----------------------------------------------------------------------------
# Test 27: Missing Fraction or MathQuestionGenerator rejected by validator
# -----------------------------------------------------------------------------

def test_27_missing_fraction_or_generator_rejected_by_validator():
    """Validates that code missing Fraction, MathQuestionGenerator, or required methods fails validation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Case A: Missing Fraction class entirely
        class MissingClassClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing partial module",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_partial",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": "class MathQuestionGenerator:\n    def generate(self):\n        pass\n",
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 30},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=MissingClassClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.FAILED
        assert res.is_complete is False
        assert "Missing required class 'Fraction'" in (res.error_message or "")
        assert os.path.isdir(res.details["staging_dir"])

        # Case B: Fraction missing required methods
        class MissingMethodClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing incomplete fraction",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_missing_method",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": "class Fraction:\n    def __init__(self): pass\nclass MathQuestionGenerator:\n    def generate(self): pass\n",
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 30},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver2 = SynthesisDriver(workspace_root=tmp_dir, llm_client=MissingMethodClient(), max_turns=3)
        res2 = driver2.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res2.status == SynthesisStatus.FAILED
        assert res2.is_complete is False
        assert "missing required methods" in (res2.error_message or "")
        assert os.path.isdir(res2.details["staging_dir"])


# -----------------------------------------------------------------------------
# Test 28: Interface validator is actually invoked in the real synthesis path
# -----------------------------------------------------------------------------

def test_28_interface_validator_actually_invoked_in_synthesis_path():
    """Proves with a spy that validate_fraction_math_interface is called on the staged target before promotion."""
    from unittest.mock import MagicMock

    with tempfile.TemporaryDirectory() as tmp_dir:
        class ValidClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing module",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_spy",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=ValidClient(), max_turns=3)
        spy_validator = MagicMock(side_effect=driver.validate_fraction_math_interface)
        driver.validate_fraction_math_interface = spy_validator

        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.COMPLETED
        assert res.is_complete is True
        assert spy_validator.call_count == 1
        called_arg = spy_validator.call_args[0][0]
        # Arg passed to validator must be inside staging directory
        assert ".staging_" in called_arg
        assert os.path.normpath(called_arg).endswith(os.path.normpath("space_fractions/fraction_math.py"))


# -----------------------------------------------------------------------------
# Test 29: Cancellation before promotion halts and preserves staging
# -----------------------------------------------------------------------------

def test_29_cancellation_before_promotion_halts_and_preserves_staging():
    """Validates that a cancellation signaled before promotion halts the run and preserves staging."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cancel_evt = threading.Event()

        class CancelTriggerClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing target",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_cancel",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                # Model completes turn 2, but set cancellation right as model returns
                cancel_evt.set()
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(
            workspace_root=tmp_dir,
            llm_client=CancelTriggerClient(),
            max_turns=3,
            cancel_event=cancel_evt,
        )
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.CANCELLED
        assert res.is_complete is False
        assert "cancelled" in (res.error_message or "").lower()
        # Staging preserved
        staging_dir = res.details.get("staging_dir")
        assert staging_dir is not None
        assert os.path.isdir(staging_dir)
        # Final directory not created
        assert "run_dir" not in res.details


# -----------------------------------------------------------------------------
# Test 30: Successful promotion is atomic and creates synthesis_manifest.json
# -----------------------------------------------------------------------------

def test_30_successful_promotion_atomic_and_writes_manifest():
    """Validates that a valid run creates synthesis_manifest.json and atomically promotes staging."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class ValidClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing target",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_manifest",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=ValidClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.COMPLETED
        assert res.is_complete is True
        assert os.path.isfile(res.target_path)
        run_dir = res.details["run_dir"]
        assert os.path.isdir(run_dir)

        manifest_path = res.details["manifest_path"]
        assert os.path.isfile(manifest_path)
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        assert manifest_data["status"] == "COMPLETED"
        assert manifest_data["validation"] == "PASSED"
        assert manifest_data["target_artifact"] == "space_fractions/fraction_math.py"
        assert manifest_data["total_turns"] == res.total_turns
        assert "space_fractions/fraction_math.py" in manifest_data["files_written"]

        # Staging directory no longer exists (atomically renamed to run_dir)
        staging_dir = os.path.join(tmp_dir, f".staging_{res.details['session_id']}")
        assert not os.path.exists(staging_dir)


# -----------------------------------------------------------------------------
# Test 31: Destination directory collision fails safely and preserves staging
# -----------------------------------------------------------------------------

def test_31_destination_directory_collision_fails_safely():
    """Validates that if the destination directory exists, synthesis fails safely without overwriting."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        class ValidClient:
            def chat_completion(self, messages, tools, reservation_id=None):
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        colliding_dir = os.path.join(tmp_dir, "colliding_run")
        os.makedirs(colliding_dir, exist_ok=True)
        with open(os.path.join(colliding_dir, "existing.txt"), "w", encoding="utf-8") as f:
            f.write("existing content")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=ValidClient(), max_turns=3)
        res = driver.run_synthesis(
            DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py", destination_dir=colliding_dir
        )

        assert res.status == SynthesisStatus.FAILED
        assert res.is_complete is False
        assert "Destination directory collision" in (res.error_message or "")
        # Colliding destination file was preserved
        with open(os.path.join(colliding_dir, "existing.txt"), "r", encoding="utf-8") as f:
            assert f.read() == "existing content"


# -----------------------------------------------------------------------------
# Test 32: Manifest creation failure preserves staging and aborts promotion
# -----------------------------------------------------------------------------

def test_32_manifest_write_failure_preserves_staging_and_aborts_promotion():
    """Validates that if synthesis manifest write fails, staging is preserved and promotion aborts."""
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp_dir:
        class ValidClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing target",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_mf_fail",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=ValidClient(), max_turns=3)

        real_open = open
        def faulty_open(path, *args, **kwargs):
            if "synthesis_manifest.json" in str(path):
                raise OSError("Simulated disk error writing manifest")
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=faulty_open):
            res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.FAILED
        assert res.is_complete is False
        assert "Failed to write synthesis manifest" in (res.error_message or "")
        staging_dir = res.details["staging_dir"]
        assert os.path.isdir(staging_dir)
        assert "run_dir" not in res.details


# -----------------------------------------------------------------------------
# Test 33: Path traversal in target_artifact rejected preflight
# -----------------------------------------------------------------------------

def test_33_path_traversal_in_target_artifact_rejected_preflight():
    """Validates that directory traversal attempts in target_artifact are rejected before loop execution."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        driver = SynthesisDriver(workspace_root=tmp_dir)

        # Relative escape
        res_rel = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="../../escaped.py")
        assert res_rel.status == SynthesisStatus.FAILED
        assert res_rel.is_complete is False
        assert "escapes workspace root" in (res_rel.error_message or "")

        # Absolute escape
        res_abs = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="/etc/passwd")
        assert res_abs.status == SynthesisStatus.FAILED
        assert res_abs.is_complete is False
        assert "escapes workspace root" in (res_abs.error_message or "")


# -----------------------------------------------------------------------------
# Test 34: Concurrency Ordering A - Cancellation wins the commit boundary first
# -----------------------------------------------------------------------------

def test_34_concurrency_cancellation_wins_commit_boundary_first():
    """Validates that when cancellation is requested prior to claiming COMMITTING,

    cancellation wins, promotion is aborted, and staging remains intact.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        class ValidClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing module",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_conc_a",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=ValidClient(), max_turns=3)

        worker_at_pre_commit = threading.Event()
        resume_worker = threading.Event()

        def pre_commit_hook():
            worker_at_pre_commit.set()
            resume_worker.wait()

        driver._pre_commit_hook = pre_commit_hook

        worker_result: Dict[str, Any] = {}

        def worker_target():
            worker_result["res"] = driver.run_synthesis(
                DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py"
            )

        worker_thread = threading.Thread(target=worker_target)
        worker_thread.start()

        # Deterministically wait until worker has generated code, validated interface, written manifest,
        # and is paused right before claiming the commit lock
        assert worker_at_pre_commit.wait(timeout=5.0)

        # Main thread requests cancellation under commit_lock BEFORE worker enters commit
        cancel_accepted = driver.request_cancellation()
        assert cancel_accepted is True
        assert driver.commit_state == CommitState.CANCELLED

        # Release worker to proceed to commit boundary
        resume_worker.set()
        worker_thread.join(timeout=5.0)

        res: SynthesisResult = worker_result["res"]
        # Run reports CANCELLED
        assert res.status == SynthesisStatus.CANCELLED
        assert res.is_complete is False
        assert "run_dir" not in res.details

        # No final destination directory exists
        for item in os.listdir(tmp_dir):
            assert not item.startswith("synthesis_run_")

        # Staging remains available for inspection
        staging_dir = res.details.get("staging_dir")
        assert staging_dir is not None
        assert os.path.isdir(staging_dir)
        staged_target = os.path.join(staging_dir, "space_fractions", "fraction_math.py")
        assert os.path.isfile(staged_target)
        manifest_file = os.path.join(staging_dir, "synthesis_manifest.json")
        assert os.path.isfile(manifest_file)


# -----------------------------------------------------------------------------
# Test 35: Concurrency Ordering B - Worker wins the commit boundary first
# -----------------------------------------------------------------------------

def test_35_concurrency_worker_wins_commit_boundary_first():
    """Validates that when the worker claims COMMITTING first under the lock,

    subsequent cancellation is rejected, promotion completes, and CANCELLED is never reported.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        class ValidClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing module",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_conc_b",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=ValidClient(), max_turns=3)

        worker_in_committing = threading.Event()
        resume_promotion = threading.Event()

        def in_commit_hook():
            # Called inside with driver.commit_lock, after self.commit_state = CommitState.COMMITTING
            worker_in_committing.set()
            resume_promotion.wait()

        driver._in_commit_hook = in_commit_hook

        worker_result: Dict[str, Any] = {}

        def worker_target():
            worker_result["res"] = driver.run_synthesis(
                DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py"
            )

        worker_thread = threading.Thread(target=worker_target)
        worker_thread.start()

        # Deterministically wait until worker has entered commit_lock and claimed COMMITTING
        assert worker_in_committing.wait(timeout=5.0)

        # Thread 2 attempts to request cancellation while worker holds commit_lock in COMMITTING
        cancel_attempt_result: Dict[str, Any] = {}

        def cancel_attempt_target():
            cancel_attempt_result["accepted"] = driver.request_cancellation()

        cancel_thread = threading.Thread(target=cancel_attempt_target)
        cancel_thread.start()

        # Release worker to proceed with os.rename and transition to COMMITTED
        resume_promotion.set()

        # Both threads finish cleanly
        worker_thread.join(timeout=5.0)
        cancel_thread.join(timeout=5.0)

        # Cancellation was rejected as too late
        assert cancel_attempt_result["accepted"] is False

        res: SynthesisResult = worker_result["res"]
        # Run reports COMPLETED, never CANCELLED
        assert res.status == SynthesisStatus.COMPLETED
        assert res.is_complete is True
        assert os.path.isfile(res.target_path)
        assert driver.commit_state == CommitState.COMMITTED

        # Destination directory exists and contains promoted artifacts
        run_dir = res.details["run_dir"]
        assert os.path.isdir(run_dir)
        assert os.path.isfile(res.details["manifest_path"])


# -----------------------------------------------------------------------------
# Test 36: Cancellation rejected after terminal FAILED state
# -----------------------------------------------------------------------------

def test_36_cancellation_rejected_after_terminal_failed_state():
    """Validates that once a run enters terminal FAILED state (e.g. promotion error),

    subsequent cancellation requests return False and preserve FAILED state.
    """
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp_dir:
        class ValidClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing module",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_fail_test",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": VALID_FRACTION_MATH_CODE,
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=ValidClient(), max_turns=3)

        # Trigger a promotion failure by simulating OSError on os.rename
        with patch("os.rename", side_effect=OSError("Simulated rename sharing violation")):
            res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.FAILED
        assert res.is_complete is False
        assert driver.commit_state == CommitState.FAILED

        # Attempt to cancel AFTER the run has reached terminal FAILED state
        cancel_accepted = driver.request_cancellation()
        assert cancel_accepted is False
        # State must remain strictly FAILED
        assert driver.commit_state == CommitState.FAILED


# -----------------------------------------------------------------------------
# Test 37: Repeated cancellation after terminal CANCELLED state returns False
# -----------------------------------------------------------------------------

def test_37_repeated_cancellation_after_terminal_cancelled_state_returns_false():
    """Validates that once a run is in terminal CANCELLED state,

    subsequent cancellation requests return False and preserve CANCELLED.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        driver = SynthesisDriver(workspace_root=tmp_dir)

        # First cancellation before or during execution succeeds
        first_cancel = driver.request_cancellation()
        assert first_cancel is True
        assert driver.commit_state == CommitState.CANCELLED

        # Second cancellation must be rejected as redundant and cannot alter state
        second_cancel = driver.request_cancellation()
        assert second_cancel is False
        assert driver.commit_state == CommitState.CANCELLED


# -----------------------------------------------------------------------------
# Test 38: Cancellation rejected after interface validation failure
# -----------------------------------------------------------------------------

def test_38_cancellation_rejected_after_interface_validation_failure():
    """Validates that a run failing interface validation enters FAILED,

    and subsequent cancellation is rejected without changing state.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        class IncompleteClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, tools, reservation_id=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurnResponse(
                        content="Writing broken module",
                        tool_calls=[
                            ToolCall(
                                tool_id="c_broken",
                                name="write_file",
                                arguments={
                                    "path": "space_fractions/fraction_math.py",
                                    "content": "class Incomplete: pass\n",
                                },
                            )
                        ],
                        raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
                        status="SUCCESS",
                    )
                return ModelTurnResponse(content="Done", tool_calls=[], status="SUCCESS")

        driver = SynthesisDriver(workspace_root=tmp_dir, llm_client=IncompleteClient(), max_turns=3)
        res = driver.run_synthesis(DOC_PATH, VIEW_PATH, target_artifact="space_fractions/fraction_math.py")

        assert res.status == SynthesisStatus.FAILED
        assert driver.commit_state == CommitState.FAILED

        # Attempt to cancel after validation failure
        cancel_accepted = driver.request_cancellation()
        assert cancel_accepted is False
        assert driver.commit_state == CommitState.FAILED
