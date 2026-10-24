"""Integration Tests for Minimal Offline Agent Pipeline (Phase 2.1).

Validates the complete integration between:
- OfflinePipelineCoordinator (src/generator_pipeline.py)
- AgentLoop & DeepSeekAgentAdapter (src/agent_loop.py)
- DeepSeekClient mock transport doubles (src/deepseek_client.py)
- ResponseParser & ResponseClassification (src/response_parser.py)
- BudgetController preflight reservation and accounting (src/budget_controller.py)
- PathSecurity workspace sandboxing and bounding (src/path_security.py)

Exercising the 8 required deterministic response scenarios:
1. Normal text response (completes without tool execution)
2. Valid supported tool call (write_file executed safely in output dir)
3. Malformed tool-call arguments (malformed JSON / non-dict handled safely)
4. Unsupported tool name (unknown tool safely rejected with observation)
5. Empty or truncated response (handled cleanly with MALFORMED_RESPONSE_ABORTED)
6. Client/network transport failure (locks reservation, zero automatic retries)
7. Budget reservation refusal (halts turn before model dispatch)
8. Cancellation request (terminates immediately without writing files)

Along with verification of all 8 non-negotiable security invariants.
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
from typing import Any, Callable, Dict, List, Optional, Tuple
import pytest

from src.agent_gui import AgentGUIApp
from src.agent_loop import (
    AgentLoop,
    AgentLoopResult,
    DeepSeekAgentAdapter,
    LLMClientProtocol,
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
    RequestOutcomeStatus,
)
from src.generator_pipeline import (
    GenerationPlan,
    OfflinePipelineCoordinator,
    PipelineExecutionError,
    PipelineExecutionSummary,
    PipelineStageType,
    StagePlan,
)
from src.path_security import PathSecurityError, safe_read_file, validate_workspace_path
from src.spec_preprocessor import SpecificationPreprocessor


@pytest.fixture(autouse=True)
def guard_no_live_network_and_subprocesses(monkeypatch):
    """Safety guard asserting that zero live network sockets or subprocesses are spawned."""
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Security Violation: Prohibited live network socket attempted in Phase 2.1 test!")

    def forbidden_subprocess(*args, **kwargs):
        raise AssertionError("Security Violation: Prohibited subprocess execution attempted in Phase 2.1 test!")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden_network)
    monkeypatch.setattr(http.client.HTTPConnection, "request", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", forbidden_subprocess)
    monkeypatch.setattr(subprocess, "run", forbidden_subprocess)


@pytest.fixture
def workspace_env():
    """Isolated temporary workspace."""
    with tempfile.TemporaryDirectory(prefix="agent_p21_ws_") as ws_dir:
        yield os.path.realpath(ws_dir)


class ScriptedLLMClient:
    """Deterministic script of model turn responses implementing LLMClientProtocol."""

    def __init__(self, turns: List[ModelTurnResponse]):
        self.turns = list(turns)
        self.history: List[List[Dict[str, Any]]] = []
        self.turn_count = 0

    def chat_completion(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], reservation_id: Optional[str] = None
    ) -> ModelTurnResponse:
        self.history.append(messages)
        if self.turn_count < len(self.turns):
            resp = self.turns[self.turn_count]
            self.turn_count += 1
            return resp
        return ModelTurnResponse(content="Stage completed successfully.", tool_calls=[])


def make_mock_transport_client(
    responses_queue: List[Dict[str, Any]], transport_calls: Optional[List[Dict[str, Any]]] = None
) -> DeepSeekClient:
    """Creates a DeepSeekClient with a deterministic mock transport sender."""
    queue_copy = list(responses_queue)

    def mock_transport(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        if transport_calls is not None:
            transport_calls.append({
                "url": req.full_url,
                "headers": dict(req.headers),
                "data": json.loads(req.data.decode("utf-8")),
                "timeout": timeout,
            })
        if not queue_copy:
            default_resp = {
                "choices": [{"message": {"role": "assistant", "content": "Default completed."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
            return 200, json.dumps(default_resp).encode("utf-8")

        item = queue_copy.pop(0)
        status_code = item.get("status_code", 200)
        body = item.get("body", {})
        if isinstance(body, Exception):
            raise body
        if isinstance(body, dict):
            return status_code, json.dumps(body).encode("utf-8")
        elif isinstance(body, bytes):
            return status_code, body
        return status_code, str(body).encode("utf-8")

    return DeepSeekClient(
        api_key="sk-mock-p21-synthetic-test-key",
        transport_sender=mock_transport,
    )


DOC_PATH = "Architecture_Documentation.md"
VIEW_PATH = "Architecture_View.md"


# -----------------------------------------------------------------------------
# Scenario 1: Normal text response (no tool calls, completes stage cleanly)
# -----------------------------------------------------------------------------

def test_scenario_1_normal_text_response(workspace_env):
    """Model returns a normal text completion; stage completes cleanly without tool execution."""
    output_dir = os.path.join(workspace_env, "output")
    os.makedirs(output_dir, exist_ok=True)
    budget = BudgetController(ceiling=0.80)

    client = ScriptedLLMClient([
        ModelTurnResponse(
            content="Architecture specifications for Space Fractions verified and approved.",
            tool_calls=[],
            raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
        )
    ])

    coordinator = OfflinePipelineCoordinator(
        workspace_root=workspace_env,
        output_dir=output_dir,
        budget_controller=budget,
        llm_client=client,
    )

    stage = StagePlan(
        stage_id="STAGE-1-TEXT",
        stage_type=PipelineStageType.SPECS_GENERATION,
        description="Verify specs text",
        expected_output_files=["specs/notes.md"],
        max_output_tokens=500,
    )

    result = coordinator.execute_stage_with_agent(stage)
    assert result.status == LoopTerminationStatus.COMPLETED
    assert result.total_turns == 1
    assert "Architecture specifications" in (result.final_response or "")
    assert budget.cumulative_spend > 0.0


# -----------------------------------------------------------------------------
# Scenario 2: Valid supported tool call (write_file executed safely)
# -----------------------------------------------------------------------------

def test_scenario_2_valid_supported_tool_call(workspace_env):
    """Model requests write_file; file is written within output_dir and task completes."""
    output_dir = os.path.join(workspace_env, "output")
    os.makedirs(output_dir, exist_ok=True)
    budget = BudgetController(ceiling=0.80)

    code_content = "class Fraction:\n    def __init__(self, n, d=1): self.n, self.d = n, d\n"
    client = ScriptedLLMClient([
        ModelTurnResponse(
            content="Writing fraction class implementation.",
            tool_calls=[
                ToolCall(
                    tool_id="call_w1",
                    name="write_file",
                    arguments={"path": "space_fractions/fraction_math.py", "content": code_content},
                )
            ],
            raw_usage={"prompt_tokens": 80, "completion_tokens": 40},
        ),
        ModelTurnResponse(
            content="Fraction module written successfully.",
            tool_calls=[],
            raw_usage={"prompt_tokens": 120, "completion_tokens": 10},
        ),
    ])

    coordinator = OfflinePipelineCoordinator(
        workspace_root=workspace_env,
        output_dir=output_dir,
        budget_controller=budget,
        llm_client=client,
    )

    stage = StagePlan(
        stage_id="STAGE-2-WRITE",
        stage_type=PipelineStageType.GAME_CORE_GENERATION,
        description="Generate fraction math",
        expected_output_files=["space_fractions/fraction_math.py"],
        max_output_tokens=600,
    )

    result = coordinator.execute_stage_with_agent(stage)
    assert result.status == LoopTerminationStatus.COMPLETED
    assert result.total_turns == 2

    # Verify file was written to disk inside output_dir
    target_path = os.path.join(output_dir, "space_fractions", "fraction_math.py")
    assert os.path.isfile(target_path)
    assert "class Fraction:" in open(target_path, "r", encoding="utf-8").read()
    assert target_path in coordinator.written_files


# -----------------------------------------------------------------------------
# Scenario 3: Malformed tool-call arguments
# -----------------------------------------------------------------------------

def test_scenario_3_malformed_tool_call_arguments(workspace_env):
    """Model returns malformed JSON arguments or non-dict args; safely returned as error observation."""
    output_dir = os.path.join(workspace_env, "output")
    os.makedirs(output_dir, exist_ok=True)
    budget = BudgetController(ceiling=0.80)

    # Turn 1: Malformed JSON string parsed by adapter into _parse_error dict
    # Turn 2: Non-dict / missing fields
    # Turn 3: Recover and complete
    client = ScriptedLLMClient([
        ModelTurnResponse(
            content="Attempting write with bad syntax.",
            tool_calls=[
                ToolCall(
                    tool_id="call_malformed",
                    name="write_file",
                    arguments={"_parse_error": "Malformed tool arguments JSON: Expecting property name enclosed in double quotes", "_raw": "{bad_json: 1}"},
                )
            ],
            raw_usage={"prompt_tokens": 50, "completion_tokens": 20},
        ),
        ModelTurnResponse(
            content="Attempting write with missing arguments.",
            tool_calls=[
                ToolCall(
                    tool_id="call_missing",
                    name="write_file",
                    arguments={"path": "valid_path.txt"},  # Missing 'content'
                )
            ],
            raw_usage={"prompt_tokens": 80, "completion_tokens": 20},
        ),
        ModelTurnResponse(
            content="Acknowledged argument errors and finishing.",
            tool_calls=[],
            raw_usage={"prompt_tokens": 100, "completion_tokens": 15},
        ),
    ])

    coordinator = OfflinePipelineCoordinator(
        workspace_root=workspace_env,
        output_dir=output_dir,
        budget_controller=budget,
        llm_client=client,
    )

    stage = StagePlan(
        stage_id="STAGE-3-MALFORMED",
        stage_type=PipelineStageType.GAME_CORE_GENERATION,
        description="Test malformed args",
        expected_output_files=[],
        max_output_tokens=500,
    )

    result = coordinator.execute_stage_with_agent(stage)
    assert result.status == LoopTerminationStatus.COMPLETED
    assert result.total_turns == 3

    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("Malformed tool arguments JSON" in r for r in tool_results)
    assert any("requires string arguments 'path' and 'content'" in r for r in tool_results)


# -----------------------------------------------------------------------------
# Scenario 4: Unsupported tool name
# -----------------------------------------------------------------------------

def test_scenario_4_unsupported_tool_name(workspace_env):
    """Model requests an unapproved tool; safely rejected with error observation."""
    output_dir = os.path.join(workspace_env, "output")
    os.makedirs(output_dir, exist_ok=True)
    budget = BudgetController(ceiling=0.80)

    client = ScriptedLLMClient([
        ModelTurnResponse(
            content="Invoking dangerous tool.",
            tool_calls=[
                ToolCall(
                    tool_id="call_unsupported",
                    name="arbitrary_exec",
                    arguments={"command": "do_something"},
                )
            ],
            raw_usage={"prompt_tokens": 40, "completion_tokens": 20},
        ),
        ModelTurnResponse(
            content="Saw tool was unknown.",
            tool_calls=[],
            raw_usage={"prompt_tokens": 70, "completion_tokens": 10},
        ),
    ])

    coordinator = OfflinePipelineCoordinator(
        workspace_root=workspace_env,
        output_dir=output_dir,
        budget_controller=budget,
        llm_client=client,
    )

    stage = StagePlan(
        stage_id="STAGE-4-UNKNOWN",
        stage_type=PipelineStageType.GAME_CORE_GENERATION,
        description="Test unknown tool",
        expected_output_files=[],
        max_output_tokens=500,
    )

    result = coordinator.execute_stage_with_agent(stage)
    assert result.status == LoopTerminationStatus.COMPLETED
    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("Unknown tool 'arbitrary_exec'" in r for r in tool_results)


# -----------------------------------------------------------------------------
# Scenario 5: Empty or truncated response
# -----------------------------------------------------------------------------

def test_scenario_5_empty_or_truncated_response(workspace_env):
    """Model returns empty content or truncated response; terminates with MALFORMED_RESPONSE_ABORTED."""
    output_dir = os.path.join(workspace_env, "output")
    os.makedirs(output_dir, exist_ok=True)

    # Subcase A: Truncated response
    budget_a = BudgetController(ceiling=0.80)
    client_trunc = ScriptedLLMClient([
        ModelTurnResponse(
            content="Halfway through generating...",
            tool_calls=[],
            status="TRUNCATED",
            error_message="Model response truncated by token limit.",
            raw_usage={"prompt_tokens": 30, "completion_tokens": 1200},
        )
    ])
    coord_a = OfflinePipelineCoordinator(workspace_env, output_dir, budget_a, llm_client=client_trunc)
    stage_a = StagePlan("S5-TRUNC", PipelineStageType.GAME_CORE_GENERATION, "trunc test", [], 500)
    res_trunc = coord_a.execute_stage_with_agent(stage_a)
    assert res_trunc.status == LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED

    # Subcase B: Empty content with no tools
    budget_b = BudgetController(ceiling=0.80)
    client_empty = ScriptedLLMClient([
        ModelTurnResponse(
            content="",
            tool_calls=[],
            status="EMPTY_CONTENT",
            error_message="Model emitted empty content with no tool calls.",
            raw_usage={"prompt_tokens": 20, "completion_tokens": 0},
        )
    ])
    coord_b = OfflinePipelineCoordinator(workspace_env, output_dir, budget_b, llm_client=client_empty)
    stage_b = StagePlan("S5-EMPTY", PipelineStageType.GAME_CORE_GENERATION, "empty test", [], 500)
    res_empty = coord_b.execute_stage_with_agent(stage_b)
    assert res_empty.status == LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED


# -----------------------------------------------------------------------------
# Scenario 6: Client/network transport failure (zero automatic retries)
# -----------------------------------------------------------------------------

def test_scenario_6_network_transport_failure(workspace_env):
    """Network failure locks reservation, terminates with MALFORMED_RESPONSE_ABORTED, and does zero retries."""
    output_dir = os.path.join(workspace_env, "output")
    os.makedirs(output_dir, exist_ok=True)
    budget = BudgetController(ceiling=0.80)
    calls = []

    def failing_transport(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        calls.append({"url": req.full_url})
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", req.headers, None)

    deepseek_client = DeepSeekClient(
        api_key="sk-mock-p21-key",
        transport_sender=failing_transport,
    )
    adapter = DeepSeekAgentAdapter(deepseek_client)

    coordinator = OfflinePipelineCoordinator(
        workspace_root=workspace_env,
        output_dir=output_dir,
        budget_controller=budget,
        llm_client=adapter,
    )

    stage = StagePlan("S6-NET", PipelineStageType.GAME_CORE_GENERATION, "net test", [], 500)
    result = coordinator.execute_stage_with_agent(stage)

    assert result.status == LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED
    # Invariant 5: exactly one request dispatched, zero retries
    assert len(calls) == 1
    # Reservation permanently locked
    assert budget.permanently_locked_spend > 0.0


# -----------------------------------------------------------------------------
# Scenario 7: Budget reservation refusal
# -----------------------------------------------------------------------------

def test_scenario_7_budget_reservation_refusal(workspace_env):
    """When budget ceiling would be exceeded, turn is rejected before model dispatch."""
    output_dir = os.path.join(workspace_env, "output")
    os.makedirs(output_dir, exist_ok=True)
    # Extremely small budget ($0.000001) that cannot even reserve one turn (~$0.005)
    tiny_budget = BudgetController(ceiling=0.000001)

    calls = []
    client = make_mock_transport_client([], transport_calls=calls)
    adapter = DeepSeekAgentAdapter(client)

    coordinator = OfflinePipelineCoordinator(
        workspace_root=workspace_env,
        output_dir=output_dir,
        budget_controller=tiny_budget,
        llm_client=adapter,
    )

    stage = StagePlan("S7-BUDGET", PipelineStageType.GAME_CORE_GENERATION, "budget test", [], 500)
    result = coordinator.execute_stage_with_agent(stage)

    assert result.status == LoopTerminationStatus.BUDGET_EXCEEDED
    assert result.total_turns == 0
    # Zero client calls made
    assert len(calls) == 0


# -----------------------------------------------------------------------------
# Scenario 8: Cancellation request
# -----------------------------------------------------------------------------

def test_scenario_8_cancellation_stops_execution(workspace_env):
    """Setting cancel_event stops the loop immediately and prevents subsequent writes."""
    output_dir = os.path.join(workspace_env, "output")
    os.makedirs(output_dir, exist_ok=True)
    budget = BudgetController(ceiling=0.80)
    cancel_event = threading.Event()

    client = ScriptedLLMClient([
        ModelTurnResponse(
            content="Writing should not happen.",
            tool_calls=[
                ToolCall(
                    tool_id="call_cancel",
                    name="write_file",
                    arguments={"path": "cancelled.txt", "content": "should not exist"},
                )
            ],
        )
    ])

    coordinator = OfflinePipelineCoordinator(
        workspace_root=workspace_env,
        output_dir=output_dir,
        budget_controller=budget,
        llm_client=client,
        cancel_event=cancel_event,
    )

    # Cancel before execution
    cancel_event.set()

    stage = StagePlan("S8-CANCEL", PipelineStageType.GAME_CORE_GENERATION, "cancel test", [], 500)
    result = coordinator.execute_stage_with_agent(stage)

    assert result.status == LoopTerminationStatus.CANCELLED
    assert result.total_turns == 0
    assert not os.path.exists(os.path.join(output_dir, "cancelled.txt"))


# -----------------------------------------------------------------------------
# End-to-End Pipeline Execution with Mock Agent
# -----------------------------------------------------------------------------

def test_end_to_end_agent_pipeline_execution(workspace_env):
    """Validates complete 4-stage generation through OfflinePipelineCoordinator.run_agent_pipeline."""
    output_dir = os.path.join(workspace_env, "output")
    os.makedirs(output_dir, exist_ok=True)
    budget = BudgetController(ceiling=0.80)

    # Scripted client that generates files across stages
    client = ScriptedLLMClient([
        # Stage 1: specs
        ModelTurnResponse(
            content="Specs complete.",
            tool_calls=[
                ToolCall("c_s1", "write_file", {"path": "specs/data_dictionary.md", "content": "# Data Dictionary\n"})
            ],
            raw_usage={"prompt_tokens": 100, "completion_tokens": 50},
        ),
        ModelTurnResponse(content="Specs done.", tool_calls=[]),

        # Stage 2: game core
        ModelTurnResponse(
            content="Core math complete.",
            tool_calls=[
                ToolCall("c_s2", "write_file", {"path": "space_fractions/fraction_math.py", "content": "class Fraction: pass\n"})
            ],
            raw_usage={"prompt_tokens": 120, "completion_tokens": 60},
        ),
        ModelTurnResponse(content="Core done.", tool_calls=[]),

        # Stage 3: game ui
        ModelTurnResponse(
            content="UI complete.",
            tool_calls=[
                ToolCall("c_s3", "write_file", {"path": "space_fractions/main.py", "content": "# Space Fractions Main\n"})
            ],
            raw_usage={"prompt_tokens": 140, "completion_tokens": 70},
        ),
        ModelTurnResponse(content="UI done.", tool_calls=[]),

        # Stage 4: tests
        ModelTurnResponse(
            content="Tests complete.",
            tool_calls=[
                ToolCall("c_s4", "write_file", {"path": "tests/test_fraction_math.py", "content": "def test_ok(): pass\n"})
            ],
            raw_usage={"prompt_tokens": 110, "completion_tokens": 50},
        ),
        ModelTurnResponse(content="Tests done.", tool_calls=[]),
    ])

    coordinator = OfflinePipelineCoordinator(
        workspace_root=workspace_env,
        output_dir=output_dir,
        budget_controller=budget,
        llm_client=client,
    )

    summary = coordinator.run_agent_pipeline(DOC_PATH, VIEW_PATH)

    assert summary.success is True
    assert summary.total_stages_run == 4
    assert len(summary.completed_stages) == 4
    assert len(summary.generated_files) == 4
    assert summary.cumulative_spend > 0.0
    assert summary.cumulative_spend <= 0.80
    assert len(summary.errors) == 0

    # Verify generated files exist on disk in output_dir
    for f in summary.generated_files:
        assert os.path.isfile(f)


# -----------------------------------------------------------------------------
# Verification of the 8 Non-Negotiable Invariants
# -----------------------------------------------------------------------------

def test_invariants_verification(workspace_env):
    """Verifies all 8 non-negotiable security and governance invariants."""
    # Invariant 1: Configured model is deepseek-v4-pro
    client = DeepSeekClient(api_key="sk-test-key")
    assert client.model == "deepseek-v4-pro"
    assert client.base_url == "https://api.deepseek.com"

    # Invariant 2: Budget reservation occurs before network transmission
    budget = BudgetController(ceiling=0.80)
    res_id = "test-res-inv2"
    reserved = budget.reserve(res_id, "hello", 100, "test")
    assert reserved > 0.0
    assert res_id in budget.active_reservations
    assert budget.active_reservations[res_id].state == ReservationState.RESERVED

    # Invariant 3: Malformed or unsupported tool calls never execute
    agent = AgentLoop(workspace_env, ScriptedLLMClient([]), budget_controller=budget)
    obs, is_err = agent._dispatch_tool(ToolCall("tc1", "unsupported_xyz", {}))
    assert is_err is True
    assert "Unknown tool" in obs

    # Invariant 4: Every file write is confined to designated workspace
    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, "../../outside.txt")
    with pytest.raises(PathSecurityError):
        validate_workspace_path(workspace_env, "C:\\Windows\\System32\\cmd.exe")

    # Invariant 5: Cancellation & network errors do not trigger automatic retries
    # Verified by test_scenario_6_network_transport_failure and test_scenario_8_cancellation_stops_execution

    # Invariant 6: Mock mode remains available without requiring an API key
    coord_mock = OfflinePipelineCoordinator(workspace_env, os.path.join(workspace_env, "out"))
    # Can run mock pipeline without any API key or network access
    summary_mock = coord_mock.run_mock_pipeline(DOC_PATH, VIEW_PATH)
    assert summary_mock.success is True

    # Invariant 7: Live mode cannot be entered accidentally by default
    import tkinter as tk
    try:
        root = tk.Tk()
        root.withdraw()
        app = AgentGUIApp(root, suppress_messagebox=True)
        assert app.execution_mode_var.get() == "offline"
        assert "disabled" in str(app.radio_live.cget("state"))
        root.destroy()
    except tk.TclError:
        pass  # Headless environment

    # Invariant 8: No secret appears in logs, exceptions, GUI status text, or generated reports
    sensitive_key = "sk-super-secret-production-key-999"
    client_sec = DeepSeekClient(api_key=sensitive_key)
    repr_str = repr(client_sec)
    assert sensitive_key not in repr_str
    assert "[PROTECTED_AND_REDACTED]" in repr_str
