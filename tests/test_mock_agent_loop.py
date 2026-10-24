"""Comprehensive Mock-Driven Agent Loop Integration Tests (Stage F5C).

Validates:
1. Assistant returns a final text response and the loop completes.
2. Assistant requests a valid file write, receives an observation, and completes on a later turn.
3. Assistant requests a file read/list operation within the workspace.
4. Malformed tool arguments are rejected safely (malformed JSON, non-dict, missing fields).
5. Path traversal, absolute paths, null bytes, and canonical spec modifications are rejected.
6. run_command is blocked unconditionally and never reaches an OS process API.
7. A tool failure is returned as a bounded observation (capped at 1500 chars).
8. Repeated identical failures trip the circuit breaker.
9. Maximum turn count is enforced.
10. Budget rejection stops the loop before a client request.
11. Cancellation stops further turns and prevents subsequent file writes.
12. Refusal, empty response, malformed response, and truncated response are handled explicitly.
13. A simulated network failure does not trigger an automatic retry.
14. The offline GUI still uses the mock pipeline and reports the correct execution mode.
15. A regression test fails if an unmocked network request is attempted.
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
from typing import Any, Dict, List, Tuple
import pytest

from src.agent_gui import AgentGUIApp
from src.agent_loop import (
    AgentLoop,
    DeepSeekAgentAdapter,
    LoopTerminationStatus,
    ModelTurnResponse,
    ToolCall,
)
from src.budget_controller import BudgetController
from src.deepseek_client import (
    DeepSeekClient,
    DeepSeekResponse,
    RequestOutcomeStatus,
)


@pytest.fixture(autouse=True)
def guard_no_real_network_and_subprocesses(monkeypatch):
    """Enforces zero live network requests and zero subprocess executions across all F5C tests."""
    def block_network(*args, **kwargs):
        raise RuntimeError("Prohibited live network call attempted in Stage F5C offline test suite!")

    def block_subprocess(*args, **kwargs):
        raise RuntimeError("Prohibited subprocess execution attempted in Stage F5C offline test suite!")

    monkeypatch.setattr(urllib.request, "urlopen", block_network)
    monkeypatch.setattr(http.client.HTTPConnection, "request", block_network)
    monkeypatch.setattr(socket.socket, "connect", block_network)
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", block_subprocess)
    monkeypatch.setattr(subprocess, "run", block_subprocess)


@pytest.fixture
def temp_workspace():
    """Provides an isolated clean temporary workspace directory."""
    with tempfile.TemporaryDirectory(prefix="agent_f5c_ws_") as ws:
        yield os.path.realpath(ws)


def make_mock_deepseek_client(responses_queue: List[Dict[str, Any]], transport_calls: List[Dict[str, Any]] = None) -> DeepSeekClient:
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
            # Fallback completion
            default_resp = {
                "choices": [{"message": {"role": "assistant", "content": "Default task completed."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
            return 200, json.dumps(default_resp).encode("utf-8")

        item = queue_copy.pop(0)
        status_code = item.get("status_code", 200)
        body = item.get("body", {})
        if isinstance(body, dict):
            return status_code, json.dumps(body).encode("utf-8")
        elif isinstance(body, bytes):
            return status_code, body
        elif isinstance(body, Exception):
            raise body
        return status_code, str(body).encode("utf-8")

    return DeepSeekClient(
        api_key="sk-mock-f5c-synthetic-key-12345",
        transport_sender=mock_transport,
    )


# -----------------------------------------------------------------------------
# Test 1: Final text response completes the loop
# -----------------------------------------------------------------------------

def test_1_final_text_response_completes_loop(temp_workspace):
    """Assistant returns a final text response with no tool calls and the loop completes."""
    budget = BudgetController(ceiling=0.80)
    mock_responses = [
        {
            "body": {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Grade 6 Space Fractions architecture analyzed."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 15, "total_tokens": 55},
            }
        }
    ]
    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget)

    result = agent.run("Analyze architecture.")
    assert result.status == LoopTerminationStatus.COMPLETED
    assert result.total_turns == 1
    assert result.final_response == "Grade 6 Space Fractions architecture analyzed."
    # Spending must be accounted
    assert budget.cumulative_spend > 0.0


# -----------------------------------------------------------------------------
# Test 2: File write -> observation -> final completion
# -----------------------------------------------------------------------------

def test_2_file_write_observation_and_completion(temp_workspace):
    """Assistant requests a valid file write, receives observation, and completes on next turn."""
    budget = BudgetController(ceiling=0.80)
    mock_responses = [
        # Turn 1: Model requests write_file tool
        {
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Writing fractions core math module.",
                            "tool_calls": [
                                {
                                    "id": "call_write_01",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps({
                                            "path": "space_fractions/math.py",
                                            "content": "def simplify(n, d): return (1, 2)",
                                        }),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
            }
        },
        # Turn 2: Model completes task after seeing write observation
        {
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Module written successfully. Deliverable complete.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 15, "total_tokens": 115},
            }
        },
    ]

    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget)

    result = agent.run("Create math.py")
    assert result.status == LoopTerminationStatus.COMPLETED
    assert result.total_turns == 2

    # Verify file was written to disk inside temporary workspace
    target_file = os.path.join(temp_workspace, "space_fractions", "math.py")
    assert os.path.isfile(target_file)
    with open(target_file, "r", encoding="utf-8") as f:
        assert "def simplify(n, d):" in f.read()

    # Verify transcript contains tool observation
    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("Successfully wrote" in r for r in tool_results)


# -----------------------------------------------------------------------------
# Test 3: File read and directory listing within workspace
# -----------------------------------------------------------------------------

def test_3_file_read_and_directory_listing(temp_workspace):
    """Assistant requests file read and directory listing within workspace."""
    # Pre-create test files in workspace
    test_file = os.path.join(temp_workspace, "data.txt")
    with open(test_file, "w", encoding="utf-8") as f:
        f.write("Numerator=3, Denominator=4")

    subdir = os.path.join(temp_workspace, "sub")
    os.makedirs(subdir, exist_ok=True)
    with open(os.path.join(subdir, "child.js"), "w", encoding="utf-8") as f:
        f.write("console.log(1);")

    budget = BudgetController(ceiling=0.80)
    mock_responses = [
        # Turn 1: Read file and list directory
        {
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Inspecting existing files.",
                            "tool_calls": [
                                {
                                    "id": "c_read",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "data.txt"}),
                                    },
                                },
                                {
                                    "id": "c_list",
                                    "type": "function",
                                    "function": {
                                        "name": "list_directory",
                                        "arguments": json.dumps({"subpath": "sub"}),
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
            }
        },
        # Turn 2: Finish
        {
            "body": {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Read completed."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100},
            }
        },
    ]

    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget)

    result = agent.run("Read files")
    assert result.status == LoopTerminationStatus.COMPLETED
    assert result.total_turns == 2

    # Verify observations in transcript
    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("Numerator=3, Denominator=4" in r for r in tool_results)
    assert any("child.js" in r for r in tool_results)


# -----------------------------------------------------------------------------
# Test 4: Malformed tool arguments rejected safely
# -----------------------------------------------------------------------------

def test_4_malformed_tool_arguments_rejected_safely(temp_workspace):
    """Malformed JSON string, non-dict args, or missing fields produce structured errors without crashing."""
    budget = BudgetController(ceiling=0.80)
    mock_responses = [
        {
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Sending malformed calls.",
                            "tool_calls": [
                                # Malformed JSON string in arguments
                                {
                                    "id": "c_malformed_json",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": "{bad_json: true,",
                                    },
                                },
                                # Missing required content field
                                {
                                    "id": "c_missing_field",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps({"path": "valid.txt"}),
                                    },
                                },
                                # Non-string argument type
                                {
                                    "id": "c_type_error",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": 9999}),
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 60, "completion_tokens": 40, "total_tokens": 100},
            }
        },
        # Next turn model sees errors and stops
        {
            "body": {
                "choices": [{"message": {"role": "assistant", "content": "Understood the errors."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 10, "total_tokens": 130},
            }
        },
    ]

    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget)

    result = agent.run("Test schema safety")
    assert result.status == LoopTerminationStatus.COMPLETED
    assert result.total_turns == 2

    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("Malformed tool arguments JSON" in r for r in tool_results)
    assert any("requires string arguments 'path' and 'content'" in r for r in tool_results)
    assert any("Invalid argument type for 'read_file'" in r for r in tool_results)


# -----------------------------------------------------------------------------
# Test 5: Path traversal, absolute paths, null bytes, canonical doc protection
# -----------------------------------------------------------------------------

def test_5_path_traversal_and_canonical_doc_protection(temp_workspace):
    """Validates rejection of traversal, absolute paths, null bytes, and canonical spec overwriting."""
    budget = BudgetController(ceiling=0.80)
    mock_responses = [
        {
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Testing path violations.",
                            "tool_calls": [
                                # 1. Directory traversal escape
                                {
                                    "id": "c_trav",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps({"path": "../../escape.txt", "content": "hacked"}),
                                    },
                                },
                                # 2. Windows absolute path
                                {
                                    "id": "c_abs",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "C:\\Windows\\System32\\cmd.exe"}),
                                    },
                                },
                                # 3. Null byte injection
                                {
                                    "id": "c_null",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps({"path": "null\0bad.txt", "content": "data"}),
                                    },
                                },
                                # 4. Attempt to overwrite canonical Architecture_Documentation.md
                                {
                                    "id": "c_canon",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps({
                                            "path": "Architecture_Documentation.md",
                                            "content": "Tampered architecture specs",
                                        }),
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 80, "completion_tokens": 50, "total_tokens": 130},
            }
        },
        {
            "body": {
                "choices": [{"message": {"role": "assistant", "content": "Paths safely rejected."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 140, "completion_tokens": 10, "total_tokens": 150},
            }
        },
    ]

    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget)

    result = agent.run("Test path boundaries")
    assert result.status == LoopTerminationStatus.COMPLETED

    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("SecurityError" in r and "escapes workspace" in r for r in tool_results)
    assert any("SecurityError" in r and "Absolute paths are prohibited" in r for r in tool_results)
    assert any("SecurityError" in r and "Null bytes detected" in r for r in tool_results)
    assert any("SecurityError" in r and "canonical architecture document" in r for r in tool_results)


# -----------------------------------------------------------------------------
# Test 6: run_command is blocked unconditionally
# -----------------------------------------------------------------------------

def test_6_run_command_blocked_unconditionally(temp_workspace):
    """Validates that run_command is unconditionally blocked with structured error and zero process calls."""
    budget = BudgetController(ceiling=0.80)
    mock_responses = [
        {
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Executing tests via run_command.",
                            "tool_calls": [
                                {
                                    "id": "c_cmd",
                                    "type": "function",
                                    "function": {
                                        "name": "run_command",
                                        "arguments": json.dumps({"command": "python -m pytest"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
            }
        },
        {
            "body": {
                "choices": [{"message": {"role": "assistant", "content": "Acknowledged execution is disabled."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90},
            }
        },
    ]

    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client)
    # Default AgentLoop has block_run_command=True
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget)

    result = agent.run("Run pytest")
    assert result.status == LoopTerminationStatus.COMPLETED

    tool_results = [e.tool_result for e in result.transcript if e.tool_result]
    assert any("'run_command' execution is permanently disabled for safety" in r for r in tool_results)
    assert any("Host process execution is not permitted" in r for r in tool_results)


# -----------------------------------------------------------------------------
# Test 7: Tool failure returned as a bounded observation (<= 1500 chars)
# -----------------------------------------------------------------------------

def test_7_tool_failure_bounded_observation(temp_workspace):
    """Validates that huge tool outputs are capped at MAX_OBSERVATION_CHARS (1500)."""
    huge_filename = "non_existent_" + ("x" * 2000) + ".txt"

    budget = BudgetController(ceiling=0.80)
    mock_responses = [
        {
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Reading huge missing file.",
                            "tool_calls": [
                                {
                                    "id": "c_huge",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": huge_filename}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
            }
        },
        {
            "body": {
                "choices": [{"message": {"role": "assistant", "content": "Done."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 5, "total_tokens": 85},
            }
        },
    ]

    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget)

    result = agent.run("Read huge")
    assert result.status == LoopTerminationStatus.COMPLETED

    for entry in result.transcript:
        if entry.tool_result:
            assert len(entry.tool_result) <= AgentLoop.MAX_OBSERVATION_CHARS


# -----------------------------------------------------------------------------
# Test 8: Repeated identical failures trip circuit breaker
# -----------------------------------------------------------------------------

def test_8_repeated_failures_trip_circuit_breaker(temp_workspace):
    """Validates that 3 identical consecutive tool failures trip the circuit breaker and abort the loop."""
    budget = BudgetController(ceiling=0.80)

    # Repeating failing tool call
    failing_body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Retrying failing file read.",
                    "tool_calls": [
                        {
                            "id": "c_repeat",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "missing_file.py"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
    }

    mock_responses = [{"body": failing_body} for _ in range(5)]
    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget)

    result = agent.run("Loop until stuck")
    assert result.status == LoopTerminationStatus.STUCK_LOOP_ABORTED
    assert result.total_turns == 3
    assert "Circuit breaker tripped" in (result.error_message or "")


# -----------------------------------------------------------------------------
# Test 9: Maximum turn count enforced
# -----------------------------------------------------------------------------

def test_9_maximum_turn_count_enforced(temp_workspace):
    """Validates that an infinite tool loop halts strictly when max_turns is reached."""
    budget = BudgetController(ceiling=0.80)

    # Varying benign tool calls to avoid circuit breaker
    mock_responses = []
    for i in range(10):
        mock_responses.append({
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"Step {i}",
                            "tool_calls": [
                                {
                                    "id": f"c_{i}",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps({"path": f"file_{i}.txt", "content": f"data {i}"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
            }
        })

    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget, max_turns=4)

    result = agent.run("Run 4 turns max")
    assert result.status == LoopTerminationStatus.MAX_TURNS_REACHED
    assert result.total_turns == 4
    assert "maximum turn limit of 4" in (result.error_message or "")


# -----------------------------------------------------------------------------
# Test 10: Budget rejection stops loop before client request
# -----------------------------------------------------------------------------

def test_10_budget_rejection_stops_loop_before_request(temp_workspace):
    """Validates that budget exhaustion prevents the turn from executing."""
    # Budget ceiling set extremely low ($0.000001) so reservation immediately fails
    tiny_budget = BudgetController(ceiling=0.000001)

    calls = []
    client = make_mock_deepseek_client([], transport_calls=calls)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=tiny_budget)

    result = agent.run("Run on empty budget")
    assert result.status == LoopTerminationStatus.BUDGET_EXCEEDED
    assert result.total_turns == 0
    # Zero client calls made
    assert len(calls) == 0


# -----------------------------------------------------------------------------
# Test 11: Cancellation stops further turns and prevents subsequent file writes
# -----------------------------------------------------------------------------

def test_11_cancellation_stops_turns_and_writes(temp_workspace):
    """Validates that setting cancel_event terminates the loop immediately without writing files."""
    budget = BudgetController(ceiling=0.80)
    cancel_event = threading.Event()

    mock_responses = [
        # Turn 1: Write file 1
        {
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Turn 1 write.",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps({"path": "turn1.txt", "content": "turn1"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
            }
        },
        # Turn 2: Attempt file 2
        {
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Turn 2 write.",
                            "tool_calls": [
                                {
                                    "id": "c2",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps({"path": "turn2.txt", "content": "turn2"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45},
            }
        },
    ]

    client = make_mock_deepseek_client(mock_responses)
    adapter = DeepSeekAgentAdapter(client, cancel_event=cancel_event)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget, cancel_event=cancel_event)

    # Signal cancellation before turn 2 begins
    cancel_event.set()

    result = agent.run("Run with cancellation")
    assert result.status == LoopTerminationStatus.CANCELLED
    assert result.total_turns == 0

    # Ensure no file was created
    assert not os.path.exists(os.path.join(temp_workspace, "turn1.txt"))
    assert not os.path.exists(os.path.join(temp_workspace, "turn2.txt"))


# -----------------------------------------------------------------------------
# Test 12: Refusal, empty response, malformed response, and truncation
# -----------------------------------------------------------------------------

def test_12_refusal_empty_malformed_truncated_handling(temp_workspace):
    """Validates explicit terminal state handling for refusal, empty, malformed, and truncated responses."""
    budget = BudgetController(ceiling=0.80)

    # 1. Refusal -> REFUSAL_ABORTED
    refusal_resp = {
        "body": {
            "choices": [{"message": {"role": "assistant", "content": None, "refusal": "Policy violation"}, "finish_reason": "refusal"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        }
    }
    client_refusal = make_mock_deepseek_client([refusal_resp])
    agent_refusal = AgentLoop(temp_workspace, DeepSeekAgentAdapter(client_refusal), budget_controller=budget)
    res_refusal = agent_refusal.run("Unsafe prompt")
    assert res_refusal.status == LoopTerminationStatus.REFUSAL_ABORTED

    # 2. Truncated -> MALFORMED_RESPONSE_ABORTED
    truncated_resp = {
        "body": {
            "choices": [{"message": {"role": "assistant", "content": "Incomplete code snippet..."}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 1200, "total_tokens": 1220},
        }
    }
    client_trunc = make_mock_deepseek_client([truncated_resp])
    agent_trunc = AgentLoop(temp_workspace, DeepSeekAgentAdapter(client_trunc), budget_controller=budget)
    res_trunc = agent_trunc.run("Generate long file")
    assert res_trunc.status == LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED

    # 3. Malformed JSON -> MALFORMED_RESPONSE_ABORTED
    malformed_resp = {"status_code": 200, "body": b"<html>502 Bad Gateway</html>"}
    client_malformed = make_mock_deepseek_client([malformed_resp])
    agent_malformed = AgentLoop(temp_workspace, DeepSeekAgentAdapter(client_malformed), budget_controller=budget)
    res_malformed = agent_malformed.run("Ping")
    assert res_malformed.status == LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED

    # 4. Empty Content with no tools -> MALFORMED_RESPONSE_ABORTED
    empty_resp = {
        "body": {
            "choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
        }
    }
    client_empty = make_mock_deepseek_client([empty_resp])
    agent_empty = AgentLoop(temp_workspace, DeepSeekAgentAdapter(client_empty), budget_controller=budget)
    res_empty = agent_empty.run("Empty ping")
    assert res_empty.status == LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED


# -----------------------------------------------------------------------------
# Test 13: Simulated network failure does not trigger automatic retries
# -----------------------------------------------------------------------------

def test_13_network_failure_zero_automatic_retries(temp_workspace):
    """Simulated network failure aborts the turn without retrying and locks reservation."""
    budget = BudgetController(ceiling=0.80)
    calls = []

    def failing_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        calls.append({"url": req.full_url})
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=503,
            msg="Service Unavailable",
            hdrs=req.headers,
            fp=None,
        )

    client = DeepSeekClient(api_key="sk-mock-key", transport_sender=failing_sender)
    adapter = DeepSeekAgentAdapter(client)
    agent = AgentLoop(temp_workspace, adapter, budget_controller=budget)

    result = agent.run("Ping with failure")
    assert result.status == LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED
    # Guarantee: exactly 1 call dispatched, zero automatic retries
    assert len(calls) == 1
    # Active reservation locked into cumulative spend
    assert budget.permanently_locked_spend > 0.0


# -----------------------------------------------------------------------------
# Test 14: Offline GUI uses mock pipeline and reports correct execution mode
# -----------------------------------------------------------------------------

def test_14_gui_offline_mock_pipeline_preserved():
    """Validates that AgentGUIApp initializes with offline mode and keeps live API mode disabled."""
    import tkinter as tk
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("Tkinter display environment unavailable")

    try:
        app = AgentGUIApp(root, suppress_messagebox=True)

        # 1. Execution mode defaults to offline
        assert app.execution_mode_var.get() == "offline"

        # 2. Live API radio button is disabled
        assert "disabled" in str(app.radio_live.cget("state"))
        assert str(app.radio_offline.cget("state")) == "normal"

        # 3. Gate E verified spend preserved ($0.000186)
        assert app.budget_controller.cumulative_spend == 0.000186
        assert app.budget_controller.ceiling == 0.800000

        # 4. Client factory produces non-thinking client with redacted secret
        client = app.get_deepseek_client()
        assert client.model == "deepseek-v4-pro"
        assert "[PROTECTED_AND_REDACTED]" in repr(client)
    finally:
        root.destroy()


# -----------------------------------------------------------------------------
# Test 15: Regression test fails if unmocked network request is attempted
# -----------------------------------------------------------------------------

def test_15_regression_unmocked_network_fails(temp_workspace):
    """Asserts that attempting an unmocked network call raises RuntimeError from isolation guard."""
    unmocked_client = DeepSeekClient(api_key="sk-unmocked-test-key")
    # Calling unmocked_client without transport_sender must attempt urllib.request.urlopen and fail
    with pytest.raises(RuntimeError, match="Prohibited live network call"):
        unmocked_client.send_chat_completion(messages=[{"role": "user", "content": "test"}])
