"""Stage F5C.2 Focused Reservation Lifecycle and Terminal-State Accounting Tests.

Validates the 12 requirements specified in Stage F5C.2 Authorization:
1. Releasing an existing, definitely pre-transmission reservation.
2. Releasing a reservation twice (idempotent, safe).
3. Attempting to release an unknown reservation (raises KeyError).
4. Confirming release does not increase cumulative or permanently locked spend.
5. Confirming a possibly transmitted request cannot be treated as an uncharged released request.
6. Network timeout/error locks the appropriate reservation and does not automatically retry.
7. BUDGET_REJECTED leaves no dangling active hold in AgentLoop.
8. The rejection path does not alter another turn's reservation.
9. Valid usage reconciles exactly once.
10. Malformed usage retains conservative accounting.
11. Budget exhaustion prevents another request.
12. The client and loop cannot double-reserve, double-reconcile, or double-charge the same request.
"""

from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple
import pytest

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
from src.agent_loop import (
    AgentLoop,
    DeepSeekAgentAdapter,
    LoopTerminationStatus,
    ModelTurnResponse,
    ToolCall,
)


@pytest.fixture(autouse=True)
def guard_no_real_network_and_subprocesses(monkeypatch):
    """Enforces zero live network requests across all lifecycle tests."""
    def block_network(*args, **kwargs):
        raise RuntimeError("Prohibited live network call attempted in Stage F5C.2 test suite!")

    monkeypatch.setattr(urllib.request, "urlopen", block_network)
    monkeypatch.setattr(http.client.HTTPConnection, "request", block_network)


# -----------------------------------------------------------------------------
# Test 1: Releasing an existing, definitely pre-transmission reservation
# -----------------------------------------------------------------------------

def test_1_releasing_existing_pre_transmission_reservation():
    """Validates that a reservation in RESERVED state can be released cleanly."""
    controller = BudgetController(ceiling=0.80)
    res_id = "req-test-1"
    reserved_amount = controller.reserve(res_id, "Sample prompt text", max_output_tokens=1200)

    assert res_id in controller.active_reservations
    assert controller.active_reservations[res_id].state == ReservationState.RESERVED
    assert controller.get_active_holds() == reserved_amount
    assert controller.cumulative_spend == 0.0
    assert controller.permanently_locked_spend == 0.0

    # Release pre-transmission reservation
    released_val = controller.release_reservation(res_id)

    assert released_val == reserved_amount
    assert res_id not in controller.active_reservations
    assert controller.get_active_holds() == 0.0
    assert controller.cumulative_spend == 0.0
    assert controller.permanently_locked_spend == 0.0
    assert controller._all_reservations[res_id].state == ReservationState.RELEASED


# -----------------------------------------------------------------------------
# Test 2: Releasing a reservation twice (idempotency)
# -----------------------------------------------------------------------------

def test_2_releasing_reservation_twice_is_idempotent():
    """Validates that repeated release calls safely handle idempotency without double-adjusting."""
    controller = BudgetController(ceiling=0.80)
    res_id = "req-test-2"
    reserved_amount = controller.reserve(res_id, "Sample prompt text", max_output_tokens=1200)

    # First release
    first_release = controller.release_reservation(res_id)
    assert first_release == reserved_amount
    assert controller.get_active_holds() == 0.0
    assert controller.cumulative_spend == 0.0
    assert controller.permanently_locked_spend == 0.0

    # Second release (must be safe, idempotent, and not double-adjust)
    second_release = controller.release_reservation(res_id)
    assert second_release == reserved_amount
    assert controller.get_active_holds() == 0.0
    assert controller.cumulative_spend == 0.0
    assert controller.permanently_locked_spend == 0.0


# -----------------------------------------------------------------------------
# Test 3: Attempting to release an unknown reservation
# -----------------------------------------------------------------------------

def test_3_releasing_unknown_reservation_raises_keyerror():
    """Validates that attempting to release an unknown reservation raises KeyError."""
    controller = BudgetController(ceiling=0.80)

    with pytest.raises(KeyError) as exc_info:
        controller.release_reservation("non-existent-id")

    assert "No reservation found with ID 'non-existent-id'" in str(exc_info.value)
    assert controller.get_active_holds() == 0.0
    assert controller.cumulative_spend == 0.0
    assert controller.permanently_locked_spend == 0.0


# -----------------------------------------------------------------------------
# Test 4: Confirming release does not increase cumulative or locked spend
# -----------------------------------------------------------------------------

def test_4_release_does_not_increase_cumulative_or_locked_spend():
    """Validates accounting invariants: release never increases cumulative or locked spend."""
    controller = BudgetController(ceiling=0.80)
    # Simulate prior spend baseline
    controller.cumulative_spend = 0.050000
    controller.permanently_locked_spend = 0.010000

    res_id = "req-test-4"
    controller.reserve(res_id, "Prompt for turn", max_output_tokens=1200)

    initial_spend = controller.cumulative_spend
    initial_locked = controller.permanently_locked_spend

    controller.release_reservation(res_id)

    assert controller.cumulative_spend == initial_spend == 0.050000
    assert controller.permanently_locked_spend == initial_locked == 0.010000
    assert controller.get_active_holds() == 0.0


# -----------------------------------------------------------------------------
# Test 5: Confirming a possibly transmitted request cannot be released
# -----------------------------------------------------------------------------

def test_5_possibly_transmitted_request_cannot_be_released():
    """Validates that once transmission starts (TRANSMITTING), release is rejected."""
    controller = BudgetController(ceiling=0.80)
    res_id = "req-test-5"
    reserved_amount = controller.reserve(res_id, "Prompt for turn", max_output_tokens=1200)

    # Transmission is initiated
    controller.mark_transmission_started(res_id)
    assert controller.active_reservations[res_id].state == ReservationState.TRANSMITTING

    # Attempting to release must raise BudgetException to protect against uncharged spend
    with pytest.raises(BudgetException) as exc_info:
        controller.release_reservation(res_id)

    assert "transmission may have already started" in str(exc_info.value)
    # Hold remains active until resolved via record_network_failure or reconcile
    assert res_id in controller.active_reservations

    # Ambiguous outcome must be resolved via record_network_failure
    locked = controller.record_network_failure(res_id)
    assert locked == reserved_amount
    assert controller.cumulative_spend == reserved_amount
    assert controller.permanently_locked_spend == reserved_amount
    assert controller.get_active_holds() == 0.0


# -----------------------------------------------------------------------------
# Test 6: Network timeout locks reservation and does not retry
# -----------------------------------------------------------------------------

def test_6_network_timeout_locks_reservation_and_zero_retries():
    """Validates that socket timeout locks reservation permanently and performs zero retries."""
    controller = BudgetController(ceiling=0.80)
    call_count = 0

    def mock_timeout_transport(req, timeout):
        nonlocal call_count
        call_count += 1
        raise socket.timeout("Socket timed out during transmission")

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=controller,
        max_output_tokens=1200,
        transport_sender=mock_timeout_transport,
    )

    resp = client.send_chat_completion(messages=[{"role": "user", "content": "Hello"}])

    assert resp.status == RequestOutcomeStatus.TIMEOUT_ERROR
    assert resp.is_success is False
    assert call_count == 1  # Exactly one call, zero retries
    assert controller.get_active_holds() == 0.0
    assert controller.cumulative_spend > 0.0
    assert controller.permanently_locked_spend == controller.cumulative_spend


# -----------------------------------------------------------------------------
# Test 7: BUDGET_REJECTED leaves no dangling active hold
# -----------------------------------------------------------------------------

def test_7_budget_rejected_leaves_no_dangling_active_hold():
    """Validates that when the client returns BUDGET_REJECTED, AgentLoop releases turn hold."""
    controller = BudgetController(ceiling=0.80)

    class MockBudgetRejectedClient:
        def chat_completion(self, messages, tools, reservation_id=None):
            return ModelTurnResponse(
                content=None,
                tool_calls=[],
                finish_reason="budget_rejected",
                status="BUDGET_REJECTED",
                error_message="Simulated client-level budget rejection.",
            )

    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = AgentLoop(
            workspace_root=tmp_dir,
            llm_client=MockBudgetRejectedClient(),
            budget_controller=controller,
        )

        result = agent.run("Perform task")

        assert result.status == LoopTerminationStatus.BUDGET_EXCEEDED
        # No dangling hold must remain in active_reservations!
        assert len(controller.active_reservations) == 0
        assert controller.get_active_holds() == 0.0
        assert controller.cumulative_spend == 0.0
        assert controller.permanently_locked_spend == 0.0


# -----------------------------------------------------------------------------
# Test 8: The rejection path does not alter another turn's reservation
# -----------------------------------------------------------------------------

def test_8_budget_rejected_does_not_alter_another_turns_reservation():
    """Validates that releasing turn-1 does not touch unrelated active reservations."""
    controller = BudgetController(ceiling=0.80)
    other_amount = controller.reserve("other-turn-res", "Independent task", max_output_tokens=500)

    class MockBudgetRejectedClient:
        def chat_completion(self, messages, tools, reservation_id=None):
            return ModelTurnResponse(
                content=None,
                tool_calls=[],
                finish_reason="budget_rejected",
                status="BUDGET_REJECTED",
                error_message="Budget rejection on turn 1.",
            )

    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = AgentLoop(
            workspace_root=tmp_dir,
            llm_client=MockBudgetRejectedClient(),
            budget_controller=controller,
        )

        result = agent.run("Perform task")

        assert result.status == LoopTerminationStatus.BUDGET_EXCEEDED
        # turn-1 hold was released, but other-turn-res remains active and untouched!
        assert "turn-1" not in controller.active_reservations
        assert "other-turn-res" in controller.active_reservations
        assert controller.active_reservations["other-turn-res"].reserved_amount == other_amount
        assert controller.get_active_holds() == other_amount


# -----------------------------------------------------------------------------
# Test 9: Valid usage reconciles exactly once
# -----------------------------------------------------------------------------

def test_9_valid_usage_reconciles_exactly_once():
    """Validates that valid usage reconciles once and a second reconcile attempt is rejected."""
    controller = BudgetController(ceiling=0.80)
    res_id = "req-test-9"
    controller.reserve(res_id, "Sample prompt", max_output_tokens=1200)

    telemetry = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "prompt_cache_hit_tokens": 20,
        "prompt_cache_miss_tokens": 80,
    }

    cost = controller.reconcile(res_id, telemetry)
    assert cost > 0.0
    assert controller.cumulative_spend == cost
    assert res_id not in controller.active_reservations

    # Second reconcile attempt must raise KeyError and NOT double-adjust spend
    with pytest.raises(KeyError):
        controller.reconcile(res_id, telemetry)

    assert controller.cumulative_spend == cost


# -----------------------------------------------------------------------------
# Test 10: Malformed usage retains conservative accounting
# -----------------------------------------------------------------------------

def test_10_malformed_usage_retains_conservative_accounting():
    """Validates that malformed telemetry permanently locks the entire conservative reservation."""
    controller = BudgetController(ceiling=0.80)
    res_id = "req-test-10"
    reserved_amount = controller.reserve(res_id, "Sample prompt", max_output_tokens=1200)

    # Malformed telemetry with negative tokens
    bad_telemetry = {"prompt_tokens": -10, "completion_tokens": 50}

    locked_cost = controller.reconcile(res_id, bad_telemetry)
    assert locked_cost == reserved_amount
    assert controller.cumulative_spend == reserved_amount
    assert controller.permanently_locked_spend == reserved_amount
    assert controller.get_active_holds() == 0.0


# -----------------------------------------------------------------------------
# Test 11: Budget exhaustion prevents another request
# -----------------------------------------------------------------------------

def test_11_budget_exhaustion_prevents_another_request():
    """Validates that once budget is exhausted, further requests are refused before dispatch."""
    controller = BudgetController(ceiling=0.010000)
    # Reserve almost all budget
    controller.reserve("initial-res", "A" * 1500, max_output_tokens=1200)

    calls = 0
    def mock_sender(req, timeout):
        nonlocal calls
        calls += 1
        return 200, b"{}"

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=controller,
        max_output_tokens=1200,
        transport_sender=mock_sender,
    )

    resp = client.send_chat_completion(messages=[{"role": "user", "content": "Another request"}])
    assert resp.status == RequestOutcomeStatus.BUDGET_REJECTED
    assert calls == 0  # Zero network transmission


# -----------------------------------------------------------------------------
# Test 12: Client and loop cannot double-reserve, double-reconcile, or double-charge
# -----------------------------------------------------------------------------

def test_12_no_double_reserve_double_reconcile_or_double_charge():
    """Validates end-to-end that AgentLoop and DeepSeekClient perform single-reservation and single-reconciliation."""
    controller = BudgetController(ceiling=0.80)
    calls = []

    def mock_sender(req, timeout):
        calls.append(req)
        # Return valid mock response with finish_reason=stop (completes loop)
        body = {
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Task completed successfully."},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 30,
                "prompt_cache_hit_tokens": 50,
                "prompt_cache_miss_tokens": 100,
            }
        }
        return 200, json.dumps(body).encode("utf-8")

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=controller,
        max_output_tokens=1200,
        transport_sender=mock_sender,
    )
    adapter = DeepSeekAgentAdapter(client)

    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = AgentLoop(
            workspace_root=tmp_dir,
            llm_client=adapter,
            budget_controller=controller,
        )

        result = agent.run("Do work")

        assert result.status == LoopTerminationStatus.COMPLETED
        assert result.total_turns == 1
        assert len(calls) == 1

        # Exactly 1 reservation occurred across the entire turn (turn-1)
        assert len(controller._all_reservations) == 1
        assert "turn-1" in controller._all_reservations
        assert controller._all_reservations["turn-1"].state == ReservationState.RECONCILED

        # Active holds must be 0
        assert controller.get_active_holds() == 0.0

        # Exact spend calculation:
        # 100 miss * 0.00000132 = 0.000132
        # 50 hit * 0.000000044 = 0.0000022
        # 30 out * 0.00000396 = 0.0001188
        # total = 0.000253
        expected_cost = (100 * 1.32 / 1_000_000) + (50 * 0.044 / 1_000_000) + (30 * 3.96 / 1_000_000)
        assert pytest.approx(controller.cumulative_spend, abs=1e-6) == expected_cost
        assert controller.permanently_locked_spend == 0.0


# -----------------------------------------------------------------------------
# Test 13: Input-bound rejection before dispatch and zero active hold leaks
# -----------------------------------------------------------------------------

def test_13_input_bound_rejection_before_dispatch():
    """Confirms that oversized input exceeding MAX_INPUT_TOKEN_BOUND aborts before dispatch without active holds."""
    controller = BudgetController(ceiling=0.80)
    calls = []

    def mock_sender(req, timeout):
        calls.append(req)
        return 200, b"{}"

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=controller,
        max_output_tokens=1200,
        transport_sender=mock_sender,
    )
    adapter = DeepSeekAgentAdapter(client)

    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = AgentLoop(
            workspace_root=tmp_dir,
            llm_client=adapter,
            budget_controller=controller,
        )

        oversized_prompt = "A" * 15000  # Token bound will exceed 8000
        result = agent.run(oversized_prompt)

        assert result.status == LoopTerminationStatus.BUDGET_EXCEEDED
        assert "exceeds maximum input bound" in (result.error_message or "")
        assert len(calls) == 0  # Zero network transmission attempted
        assert controller.get_active_holds() == 0.0
        assert controller.cumulative_spend == 0.0
        assert controller.permanently_locked_spend == 0.0


# -----------------------------------------------------------------------------
# Test 14: Reservation cleanup after known pre-dispatch rejection
# -----------------------------------------------------------------------------

def test_14_reservation_cleanup_after_known_pre_dispatch_rejection():
    """Confirms that pre-dispatch cancellation or refusal releases reservations cleanly."""
    controller = BudgetController(ceiling=0.80)
    cancel_evt = threading.Event()
    cancel_evt.set()  # Signal cancellation before dispatch

    calls = []

    def mock_sender(req, timeout):
        calls.append(req)
        return 200, b"{}"

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=controller,
        max_output_tokens=1200,
        transport_sender=mock_sender,
    )
    adapter = DeepSeekAgentAdapter(client, cancel_event=cancel_evt)

    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = AgentLoop(
            workspace_root=tmp_dir,
            llm_client=adapter,
            budget_controller=controller,
            cancel_event=cancel_evt,
        )

        result = agent.run("Do work but cancel")

        assert result.status == LoopTerminationStatus.CANCELLED
        assert len(calls) == 0  # Pre-dispatch cancellation prevented network transmission
        assert controller.get_active_holds() == 0.0
        assert controller.cumulative_spend == 0.0
        assert controller.permanently_locked_spend == 0.0


# -----------------------------------------------------------------------------
# Test 15: Preservation of uncertain-outcome lock after transmission started
# -----------------------------------------------------------------------------

def test_15_preservation_of_uncertain_outcome_lock_after_transmission_started():
    """Confirms that transport failures after transmission has begun lock reservation permanently."""
    controller = BudgetController(ceiling=0.80)
    res_id = "req-test-timeout"
    reserved_amount = controller.reserve(res_id, "Sample prompt", max_output_tokens=1200)

    # Transmission begins
    controller.mark_transmission_started(res_id)
    assert controller.active_reservations[res_id].state == ReservationState.TRANSMITTING

    # Cannot release a transmitted reservation
    with pytest.raises(BudgetException, match="transmission may have already started"):
        controller.release_reservation(res_id)

    # Record failure locks spend conservatively
    controller.record_network_failure(res_id)
    assert res_id not in controller.active_reservations
    assert controller.get_active_holds() == 0.0
    assert controller.permanently_locked_spend == reserved_amount
    assert controller.cumulative_spend == reserved_amount
    assert controller._all_reservations[res_id].state == ReservationState.PERMANENTLY_LOCKED


# -----------------------------------------------------------------------------
# Test 16: No retries after HTTP or transport failure
# -----------------------------------------------------------------------------

def test_16_no_retries_after_http_or_transport_failure():
    """Confirms that HTTP 500/429 or socket errors result in zero automatic retries."""
    controller = BudgetController(ceiling=0.80)
    calls = []

    def failing_sender(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=500,
            msg="Internal Server Error",
            hdrs=req.headers,
            fp=None,
        )

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=controller,
        max_output_tokens=1200,
        transport_sender=failing_sender,
    )
    adapter = DeepSeekAgentAdapter(client)

    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = AgentLoop(
            workspace_root=tmp_dir,
            llm_client=adapter,
            budget_controller=controller,
        )

        result = agent.run("Ping with server failure")

        assert result.status == LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED
        assert len(calls) == 1  # Exactly 1 call, zero automatic retries
        assert controller.get_active_holds() == 0.0
        assert controller.permanently_locked_spend > 0.0
        assert controller.cumulative_spend == controller.permanently_locked_spend
