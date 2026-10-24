"""Stage F5C.4 Canary Preflight Contract and Terminal Handling Tests.

Validates the exact proposed canary request construction, reservation lifecycle,
and terminal handling using mocked transports only.
"""

from __future__ import annotations

import http.client
import json
import socket
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


@pytest.fixture(autouse=True)
def guard_no_real_network_and_subprocesses(monkeypatch):
    """Enforces zero live network requests across all canary preflight tests."""
    def block_network(*args, **kwargs):
        raise RuntimeError("Prohibited live network call attempted in Stage F5C.4 test suite!")

    monkeypatch.setattr(urllib.request, "urlopen", block_network)
    monkeypatch.setattr(http.client.HTTPConnection, "request", block_network)


# -----------------------------------------------------------------------------
# Test 1: Exact Canary Request Construction and Headers
# -----------------------------------------------------------------------------

def test_canary_exact_request_payload_and_headers():
    """Validates that DeepSeekClient constructs the exact expected canary payload and headers."""
    intercepted_requests = []

    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        intercepted_requests.append((req, timeout))
        response_body = {
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "PONG"},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 28,
                "completion_tokens": 2,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 28,
            }
        }
        return 200, json.dumps(response_body).encode("utf-8")

    controller = BudgetController(ceiling=0.010186)
    controller.cumulative_spend = 0.000186

    client = DeepSeekClient(
        api_key="sk-canary-dummy-test-key",
        budget_controller=controller,
        max_output_tokens=16,
        timeout=10.0,
        transport_sender=mock_sender,
    )

    resp = client.send_chat_completion(
        messages=[{"role": "user", "content": "Reply with exactly one word: PONG."}],
        max_tokens=16,
        temperature=0.0,
        reservation_id="canary-preflight-001",
    )

    assert len(intercepted_requests) == 1
    req, timeout = intercepted_requests[0]

    # Verify endpoint and timeout
    assert req.full_url == "https://api.deepseek.com/chat/completions"
    assert req.get_method() == "POST"
    assert timeout == 10.0

    # Verify headers
    headers = {k.lower(): v for k, v in req.headers.items()}
    assert headers["content-type"] == "application/json"
    assert headers["authorization"] == "Bearer sk-canary-dummy-test-key"
    assert headers["user-agent"] == "DeepSeek-CodeAgent-Client/1.0"

    # Verify deserialized payload
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["max_tokens"] == 16
    assert payload["temperature"] == 0.0
    assert payload["stream"] is False
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["messages"] == [{"role": "user", "content": "Reply with exactly one word: PONG."}]
    assert "tools" not in payload

    # Verify successful completion and response content
    assert resp.status == RequestOutcomeStatus.SUCCESS
    assert resp.is_success is True
    assert resp.content == "PONG"


# -----------------------------------------------------------------------------
# Test 2: Successful Canary Terminal Handling and Reconciliation
# -----------------------------------------------------------------------------

def test_canary_successful_reconciliation_and_zero_active_holds():
    """Validates that a successful canary reconciles exact spend and leaves zero active holds."""
    controller = BudgetController(ceiling=0.010186)
    controller.cumulative_spend = 0.000186  # Preserves verified historical baseline

    def mock_sender(req, timeout):
        response_body = {
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "PONG"},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 28,
                "completion_tokens": 2,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 28,
            }
        }
        return 200, json.dumps(response_body).encode("utf-8")

    client = DeepSeekClient(
        api_key="sk-canary-dummy-test-key",
        budget_controller=controller,
        max_output_tokens=16,
        transport_sender=mock_sender,
    )

    resp = client.send_chat_completion(
        messages=[{"role": "user", "content": "Reply with exactly one word: PONG."}],
        max_tokens=16,
        reservation_id="canary-preflight-002",
    )

    assert resp.is_success is True
    assert resp.content == "PONG"

    # Reconciled cost calculation:
    # 28 miss * $0.00000132 = $0.00003696
    # 2 out * $0.00000396 = $0.00000792
    # Total request cost = $0.00004488
    expected_req_cost = (28 * 1.32 / 1_000_000) + (2 * 3.96 / 1_000_000)
    assert pytest.approx(resp.reconciled_cost, abs=1e-8) == expected_req_cost

    # Cumulative spend must be baseline ($0.000186) + request cost
    expected_total_spend = 0.000186 + expected_req_cost
    assert pytest.approx(controller.cumulative_spend, abs=1e-8) == expected_total_spend

    # No active hold remains
    assert len(controller.active_reservations) == 0
    assert controller.get_active_holds() == 0.0
    assert controller.permanently_locked_spend == 0.0

    # Reservation state is RECONCILED
    record = controller._all_reservations["canary-preflight-002"]
    assert record.state == ReservationState.RECONCILED


# -----------------------------------------------------------------------------
# Test 3: Network Timeout Locks Reservation with Zero Retries
# -----------------------------------------------------------------------------

def test_canary_timeout_locks_hold_with_zero_retries():
    """Validates that a socket timeout permanently locks the reservation and does not retry."""
    controller = BudgetController(ceiling=0.010186)
    controller.cumulative_spend = 0.000186
    call_count = 0

    def mock_sender(req, timeout):
        nonlocal call_count
        call_count += 1
        raise socket.timeout("Timed out waiting for socket")

    client = DeepSeekClient(
        api_key="sk-canary-dummy-test-key",
        budget_controller=controller,
        max_output_tokens=16,
        transport_sender=mock_sender,
    )

    resp = client.send_chat_completion(
        messages=[{"role": "user", "content": "Reply with exactly one word: PONG."}],
        max_tokens=16,
        reservation_id="canary-preflight-003",
    )

    assert resp.status == RequestOutcomeStatus.TIMEOUT_ERROR
    assert resp.is_success is False
    assert call_count == 1  # Exactly 1 attempt, ZERO retries

    # Reservation must be permanently locked
    assert len(controller.active_reservations) == 0
    assert controller.get_active_holds() == 0.0
    assert controller.permanently_locked_spend > 0.0
    assert controller.cumulative_spend == 0.000186 + controller.permanently_locked_spend

    record = controller._all_reservations["canary-preflight-003"]
    assert record.state == ReservationState.PERMANENTLY_LOCKED


# -----------------------------------------------------------------------------
# Test 4: HTTP 500 Error Locks Reservation with Zero Retries
# -----------------------------------------------------------------------------

def test_canary_http_500_locks_hold_with_zero_retries():
    """Validates that an HTTP error locks the reservation permanently and does not retry."""
    controller = BudgetController(ceiling=0.010186)
    controller.cumulative_spend = 0.000186
    call_count = 0

    def mock_sender(req, timeout):
        nonlocal call_count
        call_count += 1
        fp = None
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=fp,
        )

    client = DeepSeekClient(
        api_key="sk-canary-dummy-test-key",
        budget_controller=controller,
        max_output_tokens=16,
        transport_sender=mock_sender,
    )

    resp = client.send_chat_completion(
        messages=[{"role": "user", "content": "Reply with exactly one word: PONG."}],
        max_tokens=16,
        reservation_id="canary-preflight-004",
    )

    assert resp.status == RequestOutcomeStatus.HTTP_ERROR
    assert resp.is_success is False
    assert resp.http_status == 500
    assert call_count == 1  # Exactly 1 attempt, ZERO retries

    assert len(controller.active_reservations) == 0
    assert controller.get_active_holds() == 0.0
    assert controller.permanently_locked_spend > 0.0
    assert controller.cumulative_spend == 0.000186 + controller.permanently_locked_spend


# -----------------------------------------------------------------------------
# Test 5: Missing or Invalid Telemetry Conservatively Locked
# -----------------------------------------------------------------------------

def test_canary_missing_telemetry_conservatively_locked():
    """Validates that a response missing usage telemetry permanently locks the reservation."""
    controller = BudgetController(ceiling=0.010186)
    controller.cumulative_spend = 0.000186

    def mock_sender(req, timeout):
        # 200 OK but usage object is completely missing
        response_body = {
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "PONG"},
                "finish_reason": "stop"
            }]
        }
        return 200, json.dumps(response_body).encode("utf-8")

    client = DeepSeekClient(
        api_key="sk-canary-dummy-test-key",
        budget_controller=controller,
        max_output_tokens=16,
        transport_sender=mock_sender,
    )

    resp = client.send_chat_completion(
        messages=[{"role": "user", "content": "Reply with exactly one word: PONG."}],
        max_tokens=16,
        reservation_id="canary-preflight-005",
    )

    # Missing usage means reconciliation cannot determine actual token counts:
    # Full conservative reservation is locked into permanently_locked_spend
    assert len(controller.active_reservations) == 0
    assert controller.get_active_holds() == 0.0
    assert controller.permanently_locked_spend > 0.0
    assert controller.cumulative_spend == 0.000186 + controller.permanently_locked_spend


# -----------------------------------------------------------------------------
# Test 6: Pre-Transmission Budget Ceiling Rejection
# -----------------------------------------------------------------------------

def test_canary_budget_ceiling_rejection_prevents_transmission():
    """Validates that if the per-request ceiling would be breached, zero network calls are made."""
    # Ceiling set to exactly $0.000200 (less than the ~$0.000404 required for the canary reservation)
    tight_controller = BudgetController(ceiling=0.000200)
    tight_controller.cumulative_spend = 0.000186  # Only $0.000014 headroom remaining

    call_count = 0
    def mock_sender(req, timeout):
        nonlocal call_count
        call_count += 1
        return 200, b"{}"

    client = DeepSeekClient(
        api_key="sk-canary-dummy-test-key",
        budget_controller=tight_controller,
        max_output_tokens=16,
        transport_sender=mock_sender,
    )

    resp = client.send_chat_completion(
        messages=[{"role": "user", "content": "Reply with exactly one word: PONG."}],
        max_tokens=16,
        reservation_id="canary-preflight-006",
    )

    assert resp.status == RequestOutcomeStatus.BUDGET_REJECTED
    assert resp.is_success is False
    assert call_count == 0  # Zero network transmission
    assert len(tight_controller.active_reservations) == 0
    assert tight_controller.get_active_holds() == 0.0
    assert tight_controller.cumulative_spend == 0.000186
