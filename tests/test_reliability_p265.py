"""Deterministic Offline Reliability and Recovery Hardening Test Suite (Phase 2.65).

Covers all 10 failure-injection and reliability scenarios mandated in Phase 2.65:
1. Deliverable lifecycle manifest tracking (PENDING, GENERATING, VALIDATED, FAILED, REPAIR_REQUIRED).
2. Missing deliverable detection preventing premature success declaration.
3. Empty file and comment-only stub rejection.
4. Incomplete Python syntax pre-write rejection.
5. Truncation detection (finish_reason='length') and bounded retry.
6. Truncation retry ceiling exhaustion triggering SAFETY_ABORTED.
7. Malformed provider response handling.
8. HTTP errors and timeouts zero-retry and budget locking.
9. Interrupted generation and safe resume without redundant re-writes.
10. Integration contract error detection and bounded repair success.
11. Integration repair exhaustion triggering REPAIR_EXHAUSTED_ABORTED.
12. Pre-dispatch budget gate and token bound ceiling enforcement.
13. Missing usage telemetry reported as unknown cost and permanently locked.
14. Strict secret and credential quarantine in logs and data structures.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional
import pytest

from src.agent_loop import (
    AgentLoop,
    DeliverableManifestEntry,
    DeliverableStatus,
    LoopTerminationStatus,
    ModelTurnResponse,
    ToolCall,
)
from src.budget_controller import (
    BudgetController,
    BudgetExceededError,
    ReservationState,
)
from src.deepseek_client import (
    DeepSeekClient,
    DeepSeekResponse,
    PreparedRequest,
    RequestOutcomeStatus,
)
from src.project_validator import ProjectValidator


VALID_FRACTION_MATH = '''"""Fraction arithmetic module."""
class Fraction:
    def __init__(self, numerator: int, denominator: int = 1):
        if denominator == 0: raise ValueError("Denominator cannot be zero")
        self.numerator = numerator
        self.denominator = denominator
    def simplify(self): return self
    def add(self, other): return self
    def subtract(self, other): return self
    def compare(self, other): return 0
    def equals(self, other): return True
    def to_float(self): return float(self.numerator) / self.denominator
    def __str__(self): return f"{self.numerator}/{self.denominator}"
    def __repr__(self): return f"Fraction({self.numerator}, {self.denominator})"

class MathQuestionGenerator:
    def generate(self):
        return {"prompt": "1/2 + 1/2", "options": ["1", "2"], "correct_index": 0, "explanation": "1"}
'''

VALID_GAME_ENGINE = '''"""Game engine module."""
from enum import Enum

class GameState(str, Enum):
    TITLE = "TITLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    GAME_OVER = "GAME_OVER"

class GameEngine:
    def __init__(self):
        self.state = GameState.PLAYING
        self.score = 0
    def play(self): pass
    def submit_answer(self, ans): return True
    def pause(self): self.state = GameState.PAUSED
    def resume(self): self.state = GameState.PLAYING
    def is_game_over(self): return self.state == GameState.GAME_OVER
    def view_score(self): return self.score
'''

VALID_UI_SCREENS = '''"""UI screens module."""
class FractionVisualizer:
    def __init__(self, parent, numerator=1, denominator=2, mode="pie", width=180, height=180): pass
    def set_fraction(self, num, den): pass

class HUDFrame:
    def __init__(self, parent): pass
    def update_score(self, s): pass
    def update_lives(self, l): pass
    def update_progress(self, cur, total): pass
'''

VALID_MAIN = '''"""Main application."""
class SpaceFractionsApp:
    def __init__(self, root=None): pass
    def start_game(self): pass

def main(): pass
'''

VALID_TESTS = '''"""Unit tests."""
import unittest

class TestGame(unittest.TestCase):
    def test_sample(self):
        self.assertTrue(True)
'''

VALID_QUESTION_BANK = '''"""Question bank."""
QUESTIONS = [{"prompt": "Simplify 2/4", "options": ["1/2", "1/4"], "answer": 0}]
'''

VALID_README = '''# Space Fractions Grade 6
An interactive desktop educational game that helps Grade 6 students master fraction arithmetic.
Local standalone desktop client built with Python standard library.
'''

VALID_REQUIREMENTS = '''# Stdlib only
'''


class MockLLMClient:
    """Configurable offline deterministic LLM test double."""

    def __init__(self, responses: List[ModelTurnResponse], model: str = "deepseek-v4-pro") -> None:
        self.responses = list(responses)
        self.turn = 0
        self.history: List[List[Dict[str, Any]]] = []
        self.max_output_tokens = 2500
        self.model = model

    def prepare_request(
        self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None
    ) -> PreparedRequest:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools or [],
            "max_tokens": self.max_output_tokens,
            "temperature": 0.0,
        }
        serialized_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        token_bound = BudgetController.calculate_request_token_bound(payload)
        return PreparedRequest(
            serialized_bytes=serialized_bytes,
            payload=payload,
            max_output_tokens=self.max_output_tokens,
            token_bound=token_bound,
        )

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        reservation_id: Optional[str] = None,
        prepared_request: Optional[PreparedRequest] = None,
    ) -> ModelTurnResponse:
        self.history.append(json.loads(json.dumps(messages)))
        if self.turn < len(self.responses):
            resp = self.responses[self.turn]
            self.turn += 1
            return resp
        return ModelTurnResponse(
            content="Summary: completed.",
            tool_calls=[],
            raw_usage={"prompt_tokens": 100, "completion_tokens": 10},
            finish_reason="stop",
            status="SUCCESS",
        )


@pytest.fixture
def workspace_dir():
    d = tempfile.mkdtemp(prefix="p265_test_ws_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def budget_ctrl():
    return BudgetController(ceiling=0.80)


# =====================================================================
# Scenario 1: Deliverable Lifecycle Manifest Tracking
# =====================================================================
def test_deliverable_lifecycle_manifest_tracking(workspace_dir, budget_ctrl):
    """Verifies that each file is explicitly tracked as PENDING, VALIDATED, or FAILED with metadata."""
    agent = AgentLoop(
        workspace_dir,
        MockLLMClient([]),
        budget_controller=budget_ctrl,
        deliverables_checklist=AgentLoop.CANONICAL_SPACE_FRACTIONS_DELIVERABLES,
    )

    # Initial state: all 8 deliverables must be PENDING
    summary = agent.get_manifest_summary()
    assert len(summary) == 8
    for path, data in summary.items():
        assert data["status"] == DeliverableStatus.PENDING.value
        assert data["size_bytes"] == 0

    # Write one valid file
    full_path = os.path.join(workspace_dir, "space_fractions", "fraction_math.py")
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(VALID_FRACTION_MATH)

    summary_after = agent.get_manifest_summary()
    assert summary_after["space_fractions/fraction_math.py"]["status"] == DeliverableStatus.VALIDATED.value
    assert summary_after["space_fractions/fraction_math.py"]["size_bytes"] > 0
    assert summary_after["space_fractions/game_engine.py"]["status"] == DeliverableStatus.PENDING.value


# =====================================================================
# Scenario 2: Missing Deliverable Detection Prevents Success Declaration
# =====================================================================
def test_missing_deliverable_detection_blocks_success(workspace_dir, budget_ctrl):
    """Verifies that an agent turn with empty tool calls CANNOT declare success when deliverables are missing."""
    llm = MockLLMClient([
        # Model emits conversational text without calling write_file
        ModelTurnResponse(
            content="I have designed the Space Fractions architecture. Done!",
            tool_calls=[],
            finish_reason="stop",
            status="SUCCESS",
        )
    ])

    agent = AgentLoop(
        workspace_dir,
        llm,
        budget_controller=budget_ctrl,
        strict_completeness=True,
        deliverables_checklist=AgentLoop.CANONICAL_SPACE_FRACTIONS_DELIVERABLES,
    )

    result = agent.run("Synthesize Space Fractions Grade 6 educational game")

    # MUST NOT report COMPLETED; must report INCOMPLETE_DELIVERABLES_ABORTED
    assert result.status == LoopTerminationStatus.INCOMPLETE_DELIVERABLES_ABORTED
    assert "8 deliverable(s) incomplete" in result.error_message


# =====================================================================
# Scenario 3: Empty File and Comment-Only Stub Rejection
# =====================================================================
def test_empty_file_and_comment_stub_rejected(workspace_dir, budget_ctrl):
    """Verifies that empty files (0 bytes) and comment-only stubs fail validation."""
    agent = AgentLoop(
        workspace_dir,
        MockLLMClient([]),
        budget_controller=budget_ctrl,
        deliverables_checklist=AgentLoop.CANONICAL_SPACE_FRACTIONS_DELIVERABLES,
    )

    # 1. Zero-byte file
    p1 = os.path.join(workspace_dir, "space_fractions", "fraction_math.py")
    os.makedirs(os.path.dirname(p1), exist_ok=True)
    with open(p1, "w", encoding="utf-8") as f:
        f.write("")

    valid1, errs1 = agent.validate_deliverable("space_fractions/fraction_math.py")
    assert valid1 is False
    assert any("empty (0 bytes)" in e for e in errs1)

    # 2. Comment-only stub (valid syntax but empty AST body)
    with open(p1, "w", encoding="utf-8") as f:
        f.write("# This is just a comment\n# Another comment\n")

    valid2, errs2 = agent.validate_deliverable("space_fractions/fraction_math.py")
    assert valid2 is False
    assert any("zero code statements" in e for e in errs2)


# =====================================================================
# Scenario 4: Incomplete Python Syntax Pre-Write Rejection
# =====================================================================
def test_incomplete_syntax_pre_write_rejection(workspace_dir, budget_ctrl):
    """Verifies that truncated Python syntax is rejected before touching disk, preserving prior disk state."""
    agent = AgentLoop(
        workspace_dir,
        MockLLMClient([]),
        budget_controller=budget_ctrl,
        deliverables_checklist=AgentLoop.CANONICAL_SPACE_FRACTIONS_DELIVERABLES,
    )

    # Stage valid initial file
    p = os.path.join(workspace_dir, "space_fractions", "fraction_math.py")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(VALID_FRACTION_MATH)

    original_size = os.path.getsize(p)

    # Attempt to write truncated syntax
    broken_call = ToolCall(
        tool_id="call_bad_syntax",
        name="write_file",
        arguments={
            "path": "space_fractions/fraction_math.py",
            "content": "class Fraction:\n    def add(self, other):\n        return self.num + ",
            "force_overwrite": True,
        },
    )

    obs, is_err = agent._dispatch_tool(broken_call)
    assert is_err is True
    assert "Python syntax error" in obs
    assert "File was not written to disk" in obs

    # Disk state must remain intact
    assert os.path.getsize(p) == original_size
    with open(p, "r", encoding="utf-8") as f:
        assert f.read() == VALID_FRACTION_MATH


# =====================================================================
# Scenario 5: Truncation Detection and Bounded Retry
# =====================================================================
def test_truncation_detection_and_bounded_retry(workspace_dir, budget_ctrl):
    """Verifies that output truncation (finish_reason='length') triggers a targeted retry directive and recovers."""
    llm = MockLLMClient([
        # Turn 1: Model output is truncated while generating fraction_math.py
        ModelTurnResponse(
            content="Here is fraction math:",
            tool_calls=[
                ToolCall(
                    tool_id="c1",
                    name="write_file",
                    arguments={"_parse_error": "Unterminated string in JSON", "_raw": "{bad_json"},
                )
            ],
            raw_usage={"prompt_tokens": 500, "completion_tokens": 2500},
            finish_reason="length",
            status="SUCCESS",
        ),
        # Turn 2: Retries fraction_math.py cleanly and succeeds
        ModelTurnResponse(
            content="Retrying fraction math concisely:",
            tool_calls=[
                ToolCall(
                    tool_id="c2",
                    name="write_file",
                    arguments={"path": "space_fractions/fraction_math.py", "content": VALID_FRACTION_MATH},
                )
            ],
            raw_usage={"prompt_tokens": 600, "completion_tokens": 800},
            finish_reason="tool_calls",
            status="SUCCESS",
        ),
    ])

    agent = AgentLoop(
        workspace_dir,
        llm,
        budget_controller=budget_ctrl,
        max_turns=3,
        deliverables_checklist=["space_fractions/fraction_math.py"],
    )

    result = agent.run("Synthesize Space Fractions Grade 6 educational game")

    # Turn 2 must have received RETRY DIRECTIVE
    assert len(llm.history) >= 2
    turn2_user_msg = [m for m in llm.history[1] if m["role"] == "user"][-1]["content"]
    assert "RETRY DIRECTIVE (1/2)" in turn2_user_msg
    assert "Write ONLY 'space_fractions/fraction_math.py'" in turn2_user_msg

    # Deliverable must now be validated
    assert agent.is_deliverable_complete("space_fractions/fraction_math.py") is True


# =====================================================================
# Scenario 6: Truncation Retry Ceiling Exhaustion
# =====================================================================
def test_truncation_retry_exhaustion_safety_abort(workspace_dir, budget_ctrl):
    """Verifies that repeated truncation exceeding max_retries_per_deliverable halts with SAFETY_ABORTED."""
    llm = MockLLMClient([
        # Turn 1: Truncation attempt 1
        ModelTurnResponse(
            content=None,
            tool_calls=[ToolCall(tool_id="c1", name="write_file", arguments={"_parse_error": "Cut off 1", "_raw": "def fraction_math()..."})],
            raw_usage={"prompt_tokens": 500, "completion_tokens": 2500},
            finish_reason="length",
        ),
        # Turn 2: Truncation attempt 2
        ModelTurnResponse(
            content=None,
            tool_calls=[ToolCall(tool_id="c2", name="write_file", arguments={"_parse_error": "Cut off 2", "_raw": "class Fraction: def __init__..."})],
            raw_usage={"prompt_tokens": 600, "completion_tokens": 2500},
            finish_reason="length",
        ),
        # Turn 3: Truncation attempt 3 (exceeds default max_retries_per_deliverable=2)
        ModelTurnResponse(
            content=None,
            tool_calls=[ToolCall(tool_id="c3", name="write_file", arguments={"_parse_error": "Cut off 3", "_raw": "from math import gcd..."})],
            raw_usage={"prompt_tokens": 700, "completion_tokens": 2500},
            finish_reason="length",
        ),
    ])

    agent = AgentLoop(
        workspace_dir,
        llm,
        budget_controller=budget_ctrl,
        max_turns=6,
        max_retries_per_deliverable=2,
        deliverables_checklist=["space_fractions/fraction_math.py"],
    )

    result = agent.run("Synthesize Space Fractions Grade 6 educational game")

    assert result.status == LoopTerminationStatus.SAFETY_ABORTED
    assert "Retry limit reached" in result.error_message
    assert "space_fractions/fraction_math.py" in result.error_message


# =====================================================================
# Scenario 7: Malformed Provider Response Handling
# =====================================================================
def test_malformed_response_json_aborted(workspace_dir, budget_ctrl):
    """Verifies that malformed provider responses safely abort without unhandled exceptions."""
    llm = MockLLMClient([
        ModelTurnResponse(
            content=None,
            tool_calls=[],
            finish_reason="malformed_json",
            status="MALFORMED_JSON",
            error_message="Malformed response JSON from provider.",
        )
    ])

    agent = AgentLoop(workspace_dir, llm, budget_controller=budget_ctrl)
    result = agent.run("Synthesize Space Fractions")

    assert result.status == LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED
    assert "Malformed response JSON" in result.error_message


# =====================================================================
# Scenario 8: HTTP Errors and Timeouts Lock Budget (Zero Blind Retries)
# =====================================================================
def test_http_error_and_timeout_locking(budget_ctrl):
    """Verifies that HTTP errors and network timeouts lock the reservation into committed spend with zero retries."""
    client = DeepSeekClient(
        api_key="sk-test000000000000000000000000000000000000",
        budget_controller=budget_ctrl,
    )

    # 1. Simulate HTTP 500
    client._transport_sender = lambda req, timeout: (500, b'{"error": "Internal Server Error"}')
    resp1 = client.send_chat_completion(messages=[{"role": "user", "content": "hi"}], max_tokens=100)
    assert resp1.status == RequestOutcomeStatus.HTTP_ERROR
    assert resp1.is_success is False
    assert budget_ctrl.permanently_locked_spend > 0

    # 2. Simulate Timeout
    import socket
    def timeout_sender(req, timeout): raise socket.timeout("Socket timed out")
    client._transport_sender = timeout_sender
    resp2 = client.send_chat_completion(messages=[{"role": "user", "content": "hi again"}], max_tokens=100)
    assert resp2.status == RequestOutcomeStatus.TIMEOUT_ERROR
    assert resp2.is_success is False


# =====================================================================
# Scenario 9: Interrupted Generation and Safe Resume
# =====================================================================
def test_interrupted_generation_and_safe_resume(workspace_dir, budget_ctrl):
    """Verifies that pre-existing valid files are preserved and only missing files are targeted."""
    # Seed 2 files
    p1 = os.path.join(workspace_dir, "space_fractions", "fraction_math.py")
    p2 = os.path.join(workspace_dir, "space_fractions", "game_engine.py")
    os.makedirs(os.path.dirname(p1), exist_ok=True)
    with open(p1, "w", encoding="utf-8") as f: f.write(VALID_FRACTION_MATH)
    with open(p2, "w", encoding="utf-8") as f: f.write(VALID_GAME_ENGINE)

    p1_stat = os.path.getmtime(p1)

    llm = MockLLMClient([
        # Model tries to re-write fraction_math.py, then writes question_bank.py
        ModelTurnResponse(
            content=None,
            tool_calls=[
                ToolCall(tool_id="c1", name="write_file", arguments={"path": "space_fractions/fraction_math.py", "content": "# overwrite attempt"}),
                ToolCall(tool_id="c2", name="write_file", arguments={"path": "space_fractions/question_bank.py", "content": VALID_QUESTION_BANK}),
            ],
            raw_usage={"prompt_tokens": 1000, "completion_tokens": 200},
        )
    ])

    agent = AgentLoop(
        workspace_dir,
        llm,
        budget_controller=budget_ctrl,
        max_turns=2,
        deliverables_checklist=AgentLoop.CANONICAL_SPACE_FRACTIONS_DELIVERABLES,
    )

    result = agent.run("Synthesize Space Fractions")

    # Initial directive should target question_bank.py (the first missing file)
    turn1_user_msg = [m for m in llm.history[0] if m["role"] == "user"][-1]["content"]
    assert "CHECKLIST: 2/8 completed" in turn1_user_msg
    assert "DIRECTIVE FOR TURN 1: Write ONLY 'space_fractions/question_bank.py'" in turn1_user_msg

    # Original fraction_math.py must NOT be overwritten
    with open(p1, "r", encoding="utf-8") as f:
        assert f.read() == VALID_FRACTION_MATH


# =====================================================================
# Scenario 10: Integration Contract Error Detection and Bounded Repair
# =====================================================================
def test_integration_repair_success(workspace_dir, budget_ctrl):
    """Verifies that cross-file interface errors trigger bounded repair turns and succeed upon correction."""
    # Write 7 files
    pkg = os.path.join(workspace_dir, "space_fractions")
    tests_dir = os.path.join(workspace_dir, "tests")
    os.makedirs(pkg, exist_ok=True)
    os.makedirs(tests_dir, exist_ok=True)

    with open(os.path.join(pkg, "game_engine.py"), "w", encoding="utf-8") as f: f.write(VALID_GAME_ENGINE)
    with open(os.path.join(pkg, "question_bank.py"), "w", encoding="utf-8") as f: f.write(VALID_QUESTION_BANK)
    with open(os.path.join(pkg, "ui_screens.py"), "w", encoding="utf-8") as f: f.write(VALID_UI_SCREENS)
    with open(os.path.join(pkg, "main.py"), "w", encoding="utf-8") as f: f.write(VALID_MAIN)
    with open(os.path.join(tests_dir, "test_fraction_game.py"), "w", encoding="utf-8") as f: f.write(VALID_TESTS)
    with open(os.path.join(workspace_dir, "requirements.txt"), "w", encoding="utf-8") as f: f.write(VALID_REQUIREMENTS)
    with open(os.path.join(workspace_dir, "README.md"), "w", encoding="utf-8") as f: f.write(VALID_README)

    # fraction_math missing 'simplify' method initially
    BROKEN_FRACTION_MATH = '''class Fraction:
    def __init__(self, n, d=1): self.n = n; self.d = d
    def add(self, o): return self
    def subtract(self, o): return self
    def compare(self, o): return 0
    def equals(self, o): return True
class MathQuestionGenerator:
    def generate(self): return {}
'''

    llm = MockLLMClient([
        # Turn 1: Model writes broken fraction_math.py
        ModelTurnResponse(
            content=None,
            tool_calls=[ToolCall(tool_id="c1", name="write_file", arguments={"path": "space_fractions/fraction_math.py", "content": BROKEN_FRACTION_MATH})],
            raw_usage={"prompt_tokens": 1000, "completion_tokens": 500},
        ),
        # Turn 2: Model receives integration error feedback and supplies corrected fraction_math.py
        ModelTurnResponse(
            content="Repaired fraction_math.py with simplify method.",
            tool_calls=[ToolCall(tool_id="c2", name="write_file", arguments={"path": "space_fractions/fraction_math.py", "content": VALID_FRACTION_MATH, "force_overwrite": True})],
            raw_usage={"prompt_tokens": 1200, "completion_tokens": 500},
        ),
        # Turn 3: Conclude
        ModelTurnResponse(content="All deliverables verified.", tool_calls=[]),
    ])

    agent = AgentLoop(
        workspace_dir,
        llm,
        budget_controller=budget_ctrl,
        max_turns=4,
        strict_completeness=True,
        deliverables_checklist=AgentLoop.CANONICAL_SPACE_FRACTIONS_DELIVERABLES,
    )

    result = agent.run("Synthesize Space Fractions Grade 6 educational game")

    # Turn 2 user message must contain INTEGRATION VALIDATION FAILURE
    turn2_user_msg = [m for m in llm.history[1] if m["role"] == "user"][-1]["content"]
    assert "INTEGRATION VALIDATION FAILURE" in turn2_user_msg
    assert "simplify" in turn2_user_msg

    assert result.status == LoopTerminationStatus.COMPLETED


# =====================================================================
# Scenario 11: Integration Repair Exhaustion Halts Safely
# =====================================================================
def test_integration_repair_exhaustion(workspace_dir, budget_ctrl):
    """Verifies that persistent integration failures halt with REPAIR_EXHAUSTED_ABORTED after retry limit."""
    # Write 7 files
    pkg = os.path.join(workspace_dir, "space_fractions")
    tests_dir = os.path.join(workspace_dir, "tests")
    os.makedirs(pkg, exist_ok=True)
    os.makedirs(tests_dir, exist_ok=True)

    with open(os.path.join(pkg, "game_engine.py"), "w", encoding="utf-8") as f: f.write(VALID_GAME_ENGINE)
    with open(os.path.join(pkg, "question_bank.py"), "w", encoding="utf-8") as f: f.write(VALID_QUESTION_BANK)
    with open(os.path.join(pkg, "ui_screens.py"), "w", encoding="utf-8") as f: f.write(VALID_UI_SCREENS)
    with open(os.path.join(pkg, "main.py"), "w", encoding="utf-8") as f: f.write(VALID_MAIN)
    with open(os.path.join(tests_dir, "test_fraction_game.py"), "w", encoding="utf-8") as f: f.write(VALID_TESTS)
    with open(os.path.join(workspace_dir, "requirements.txt"), "w", encoding="utf-8") as f: f.write(VALID_REQUIREMENTS)
    with open(os.path.join(workspace_dir, "README.md"), "w", encoding="utf-8") as f: f.write(VALID_README)

    BROKEN_MATH = "class Fraction: pass\nclass MathQuestionGenerator: pass\n"

    llm = MockLLMClient([
        ModelTurnResponse(
            content=None,
            tool_calls=[ToolCall(tool_id="c1", name="write_file", arguments={"path": "space_fractions/fraction_math.py", "content": BROKEN_MATH})],
        ),
        # Repair attempt 1 fails
        ModelTurnResponse(content="Still broken.", tool_calls=[]),
        # Repair attempt 2 fails
        ModelTurnResponse(content="Still broken.", tool_calls=[]),
    ])

    agent = AgentLoop(
        workspace_dir,
        llm,
        budget_controller=budget_ctrl,
        max_turns=5,
        max_repair_attempts=2,
        strict_completeness=True,
        deliverables_checklist=AgentLoop.CANONICAL_SPACE_FRACTIONS_DELIVERABLES,
    )

    result = agent.run("Synthesize Space Fractions Grade 6 educational game")

    assert result.status == LoopTerminationStatus.REPAIR_EXHAUSTED_ABORTED
    assert "repair attempts" in result.error_message


# =====================================================================
# Scenario 12: Pre-Dispatch Budget Gate and Token Bound Enforcement
# =====================================================================
def test_budget_exhaustion_before_dispatch(workspace_dir):
    """Verifies that requests exceeding budget ceiling are rejected before network dispatch."""
    tight_budget = BudgetController(ceiling=0.01)
    llm = MockLLMClient([])

    agent = AgentLoop(
        workspace_dir,
        llm,
        budget_controller=tight_budget,
        max_output_tokens=2500,
    )

    result = agent.run("A" * 1000)
    assert result.status == LoopTerminationStatus.BUDGET_EXCEEDED
    assert "Budget ceiling exceeded" in result.error_message


# =====================================================================
# Scenario 13: Missing Telemetry Reported as Unknown & Locked
# =====================================================================
def test_missing_telemetry_reported_as_unknown_and_locked(budget_ctrl):
    """Verifies that missing usage telemetry is locked into permanently locked spend rather than treated as zero."""
    res_id = "test-missing-usage"
    reserved = budget_ctrl.reserve(res_id, "test prompt", max_output_tokens=500)
    assert budget_ctrl.get_active_holds() == reserved

    # Reconcile with None usage
    actual = budget_ctrl.reconcile(res_id, usage_data=None)

    # Full reserved amount must be locked, not $0
    assert actual == reserved
    assert budget_ctrl.permanently_locked_spend == reserved
    assert budget_ctrl.cumulative_spend == reserved
    assert budget_ctrl.get_active_holds() == 0.0


# =====================================================================
# Scenario 14: Secret Redaction & Quarantine
# =====================================================================
def test_secrets_and_api_keys_strictly_quarantined():
    """Verifies that API keys and headers are never leaked in string representations or exceptions."""
    secret_key = "sk-deepseek-super-secret-production-key-xyz123"
    client = DeepSeekClient(api_key=secret_key)

    repr_str = repr(client)
    str_str = str(client)

    assert secret_key not in repr_str
    assert secret_key not in str_str
    assert "..." in repr_str or "sk-***" in repr_str or "redacted" in repr_str.lower()
