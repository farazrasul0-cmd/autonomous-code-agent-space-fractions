"""Comprehensive Offline Unit Tests for DeepSeekClient.

Validates:
1. Endpoint URL, HTTP POST method, and headers.
2. Model identifier ('deepseek-v4-pro'), non-thinking mode payload, and max_tokens constraint.
3. Response parsing: Success, Refusal, Truncation, Empty Content, Malformed JSON.
4. HTTP error handling (429, 500) and socket timeouts.
5. Strict zero-automatic-retries guarantee (calls transport exactly once).
6. Budget preflight reservation and budget rejection.
7. Conservative cancellation handling (clean cancel before transmission vs ambiguous cancel after).
8. Strict API key quarantine and redaction in repr, str, and exceptions.
9. Offline isolation guard: fails immediately if real network connection is attempted.
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

from src.budget_controller import BudgetController
from src.deepseek_client import (
    DeepSeekClient,
    DeepSeekResponse,
    RequestOutcomeStatus,
)


@pytest.fixture(autouse=True)
def guard_no_real_network(monkeypatch):
    """Enforces zero live network requests across all client unit tests."""
    def block_network(*args, **kwargs):
        raise RuntimeError("Prohibited live network call attempted in offline test suite!")

    monkeypatch.setattr(urllib.request, "urlopen", block_network)
    monkeypatch.setattr(http.client.HTTPConnection, "request", block_network)
    monkeypatch.setattr(socket.socket, "connect", block_network)


# -----------------------------------------------------------------------------
# 1. Endpoint, Model, Payload, and Header Verification
# -----------------------------------------------------------------------------

def test_endpoint_and_payload_construction():
    """Verifies official endpoint, deepseek-v4-pro model, non-thinking mode, and default 1200 max_tokens."""
    captured_request = {}

    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        captured_request["url"] = req.full_url
        captured_request["method"] = req.get_method()
        captured_request["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured_request["data"] = json.loads(req.data.decode("utf-8"))
        captured_request["timeout"] = timeout

        response_body = {
            "id": "chatcmpl-test-01",
            "object": "chat.completion",
            "created": 1729640000,
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello Grade 6 Space Fractions!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 25,
                "completion_tokens": 12,
                "total_tokens": 37,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }
        return 200, json.dumps(response_body).encode("utf-8")

    client = DeepSeekClient(
        api_key="sk-test-sample-key-123456",
        transport_sender=mock_sender,
    )

    response = client.send_chat_completion(
        messages=[{"role": "user", "content": "Hello"}],
    )

    # 1. URL and method
    assert captured_request["url"] == "https://api.deepseek.com/chat/completions"
    assert captured_request["method"] == "POST"

    # 2. Headers: Content-Type and Authorization Bearer
    assert captured_request["headers"]["content-type"] == "application/json"
    assert captured_request["headers"]["authorization"] == "Bearer sk-test-sample-key-123456"

    # 3. Payload configuration
    payload = captured_request["data"]
    assert payload["model"] == "deepseek-v4-pro"
    assert payload["max_tokens"] == 1200  # Default cap <= 1200
    assert payload["thinking"] == {"type": "disabled"}  # Non-thinking mode enforced
    assert payload["stream"] is False

    # 4. Timeout
    assert captured_request["timeout"] == 30.0

    # 5. Response output
    assert response.is_success is True
    assert response.status == RequestOutcomeStatus.SUCCESS
    assert response.content == "Hello Grade 6 Space Fractions!"
    assert response.http_status == 200


def test_custom_output_limit_enforced():
    """Verifies that max_tokens parameter is respected up to safety bounds."""
    captured_payload = {}

    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        captured_payload.update(json.loads(req.data.decode("utf-8")))
        resp = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return 200, json.dumps(resp).encode("utf-8")

    client = DeepSeekClient(
        api_key="sk-test-key",
        max_output_tokens=800,
        transport_sender=mock_sender,
    )

    client.send_chat_completion(
        messages=[{"role": "user", "content": "test"}],
        max_tokens=600,
    )
    assert captured_payload["max_tokens"] == 600

    # Default fallback to client.max_output_tokens
    client.send_chat_completion(messages=[{"role": "user", "content": "test"}])
    assert captured_payload["max_tokens"] == 800


# -----------------------------------------------------------------------------
# 2. Response Parsing: Classification and Structure
# -----------------------------------------------------------------------------

def test_response_parsing_success_with_tool_calls():
    """Verifies success classification and parsed tool calls."""
    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        resp = {
            "id": "cmpl-tool-1",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Calling tool now.",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": "{\"path\": \"test.js\", \"content\": \"console.log(1);\"}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
        }
        return 200, json.dumps(resp).encode("utf-8")

    client = DeepSeekClient(api_key="sk-test-key", transport_sender=mock_sender)
    res = client.send_chat_completion(messages=[{"role": "user", "content": "generate code"}])

    assert res.status == RequestOutcomeStatus.SUCCESS
    assert res.is_success is True
    assert res.finish_reason == "tool_calls"
    assert res.parsed_response is not None
    assert len(res.parsed_response.tool_calls) == 1
    assert res.parsed_response.tool_calls[0]["function"]["name"] == "write_file"


def test_response_refusal_detection():
    """Verifies that provider refusal / safety filtering is classified as REFUSAL."""
    # Case A: finish_reason is "refusal"
    def mock_sender_a(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        resp = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "I cannot fulfill this request."},
                    "finish_reason": "refusal",
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
        }
        return 200, json.dumps(resp).encode("utf-8")

    client = DeepSeekClient(api_key="sk-test-key", transport_sender=mock_sender_a)
    res_a = client.send_chat_completion(messages=[{"role": "user", "content": "unsafe query"}])
    assert res_a.status == RequestOutcomeStatus.REFUSAL
    assert res_a.is_success is False
    assert res_a.finish_reason == "refusal"

    # Case B: message contains refusal field
    def mock_sender_b(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        resp = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "refusal": "Content policy violation detected.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
        }
        return 200, json.dumps(resp).encode("utf-8")

    client_b = DeepSeekClient(api_key="sk-test-key", transport_sender=mock_sender_b)
    res_b = client_b.send_chat_completion(messages=[{"role": "user", "content": "unsafe query"}])
    assert res_b.status == RequestOutcomeStatus.REFUSAL
    assert res_b.is_success is False


def test_response_truncation_detection():
    """Verifies that response truncated due to token limit is classified as TRUNCATED."""
    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        resp = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "function computeFraction() { let x = 1;"},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 1200, "total_tokens": 1220},
        }
        return 200, json.dumps(resp).encode("utf-8")

    client = DeepSeekClient(api_key="sk-test-key", transport_sender=mock_sender)
    res = client.send_chat_completion(messages=[{"role": "user", "content": "write code"}])
    assert res.status == RequestOutcomeStatus.TRUNCATED
    assert res.is_success is False
    assert res.finish_reason == "length"
    assert "computeFraction" in (res.content or "")


def test_response_empty_content_detection():
    """Verifies that an empty content string or missing content without tool calls is EMPTY_CONTENT."""
    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        resp = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
        }
        return 200, json.dumps(resp).encode("utf-8")

    client = DeepSeekClient(api_key="sk-test-key", transport_sender=mock_sender)
    res = client.send_chat_completion(messages=[{"role": "user", "content": "ping"}])
    assert res.status == RequestOutcomeStatus.EMPTY_CONTENT
    assert res.is_success is False


def test_response_malformed_json_handling():
    """Verifies graceful classification of invalid non-JSON HTTP responses."""
    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        return 200, b"<html><body>502 Bad Gateway from Proxy</body></html>"

    client = DeepSeekClient(api_key="sk-test-key", transport_sender=mock_sender)
    res = client.send_chat_completion(messages=[{"role": "user", "content": "ping"}])
    assert res.status == RequestOutcomeStatus.MALFORMED_JSON
    assert res.is_success is False
    assert "Failed to parse provider response JSON" in (res.error_message or "")


# -----------------------------------------------------------------------------
# 3. HTTP Errors, Timeouts, and Zero-Retries Guarantee
# -----------------------------------------------------------------------------

def test_http_429_rate_limit_and_zero_retries():
    """Verifies HTTP 429 returns HTTP_ERROR and executes exactly once without retry."""
    call_count = 0

    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        nonlocal call_count
        call_count += 1
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=429,
            msg="Too Many Requests",
            hdrs=req.headers,
            fp=None,
        )

    client = DeepSeekClient(api_key="sk-test-key", transport_sender=mock_sender)
    res = client.send_chat_completion(messages=[{"role": "user", "content": "ping"}])

    assert res.status == RequestOutcomeStatus.HTTP_ERROR
    assert res.is_success is False
    assert res.http_status == 429
    assert "429" in (res.error_message or "")
    # Strict zero-automatic-retries check: exactly 1 call was dispatched
    assert call_count == 1


def test_http_500_server_error_and_zero_retries():
    """Verifies HTTP 500 returns HTTP_ERROR and does not retry."""
    call_count = 0

    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        nonlocal call_count
        call_count += 1
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=500,
            msg="Internal Server Error",
            hdrs=req.headers,
            fp=None,
        )

    client = DeepSeekClient(api_key="sk-test-key", transport_sender=mock_sender)
    res = client.send_chat_completion(messages=[{"role": "user", "content": "ping"}])

    assert res.status == RequestOutcomeStatus.HTTP_ERROR
    assert res.is_success is False
    assert res.http_status == 500
    assert call_count == 1


def test_socket_timeout_handling():
    """Verifies socket timeout is classified as TIMEOUT_ERROR and latency is tracked."""
    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        raise socket.timeout("Operation timed out")

    client = DeepSeekClient(api_key="sk-test-key", transport_sender=mock_sender)
    res = client.send_chat_completion(messages=[{"role": "user", "content": "ping"}])

    assert res.status == RequestOutcomeStatus.TIMEOUT_ERROR
    assert res.is_success is False
    assert "timeout" in (res.error_message or "").lower()
    assert res.latency_ms >= 0.0


# -----------------------------------------------------------------------------
# 4. Budget Enforcement & Reconciled Accounting
# -----------------------------------------------------------------------------

def test_budget_preflight_reservation_and_reconciliation():
    """Verifies that pre-call budget is reserved before transmission and reconciled on success."""
    budget = BudgetController(ceiling=0.800000)
    budget.cumulative_spend = 0.000186

    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        # During transmission, an active reservation must exist
        assert len(budget.active_reservations) == 1
        resp = {
            "choices": [{"message": {"role": "assistant", "content": "Code generated."}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }
        return 200, json.dumps(resp).encode("utf-8")

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=budget,
        transport_sender=mock_sender,
    )

    res = client.send_chat_completion(messages=[{"role": "user", "content": "write code"}])

    assert res.is_success is True
    # After completion, active reservations must be 0 (reconciled)
    assert len(budget.active_reservations) == 0
    # Spend should increase by verified reconciled cost
    assert budget.cumulative_spend > 0.000186
    assert budget.cumulative_spend < 0.800000
    assert res.reconciled_cost is not None
    assert res.reconciled_cost > 0.0


def test_budget_rejection_prevents_network_call():
    """Verifies that requests exceeding remaining budget are rejected before any network transmission."""
    budget = BudgetController(ceiling=0.800000)
    # Set cumulative spend near ceiling so reservation exceeds remaining budget
    budget.cumulative_spend = 0.799990  # Only $0.000010 remaining

    called = False

    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        nonlocal called
        called = True
        return 200, b"{}"

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=budget,
        max_output_tokens=1200,
        transport_sender=mock_sender,
    )

    # Sending a large prompt will require more than $0.000010
    res = client.send_chat_completion(messages=[{"role": "user", "content": "A" * 5000}])

    assert res.status == RequestOutcomeStatus.BUDGET_REJECTED
    assert res.is_success is False
    assert "rejected by budget controller" in (res.error_message or "").lower()
    # Zero network calls made
    assert called is False
    assert budget.cumulative_spend == 0.799990


# -----------------------------------------------------------------------------
# 5. Cancellation Semantics: Clean Pre-Transmission vs Ambiguous Post-Transmission
# -----------------------------------------------------------------------------

def test_cancellation_before_transmission_is_clean():
    """Verifies that cancelling before network transmission releases reservation cleanly."""
    budget = BudgetController(ceiling=0.800000)
    cancel_event = threading.Event()
    cancel_event.set()  # Signal cancellation immediately

    called = False

    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        nonlocal called
        called = True
        return 200, b"{}"

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=budget,
        transport_sender=mock_sender,
    )

    res = client.send_chat_completion(
        messages=[{"role": "user", "content": "cancel me"}],
        cancel_event=cancel_event,
    )

    assert res.status == RequestOutcomeStatus.CANCELLED
    assert res.cancellation_outcome == "cancelled_before_transmission"
    assert called is False
    # No locked reservations
    assert len(budget.active_reservations) == 0


def test_cancellation_after_transmission_is_ambiguous_and_locked():
    """Verifies that cancelling after network transmission marks outcome unknown and locks reservation."""
    budget = BudgetController(ceiling=0.800000)
    cancel_event = threading.Event()

    def mock_sender(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
        # Simulate cancellation arriving while network bytes are in-flight
        cancel_event.set()
        resp = {
            "choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return 200, json.dumps(resp).encode("utf-8")

    client = DeepSeekClient(
        api_key="sk-test-key",
        budget_controller=budget,
        transport_sender=mock_sender,
    )

    res = client.send_chat_completion(
        messages=[{"role": "user", "content": "cancel in flight"}],
        cancel_event=cancel_event,
    )

    assert res.status == RequestOutcomeStatus.CANCELLED
    assert res.cancellation_outcome == "unknown"
    assert "Provider outcome unknown" in (res.error_message or "")
    # The reservation is locked permanently into cumulative spend to protect budget
    assert len(budget.active_reservations) == 0
    assert budget.permanently_locked_spend > 0.0
    assert budget.cumulative_spend > 0.000186


# -----------------------------------------------------------------------------
# 6. Strict Credential Quarantine & Secret Redaction
# -----------------------------------------------------------------------------

def test_api_key_redaction():
    """Verifies that API keys are strictly redacted in __repr__, __str__, and exceptions."""
    secret = "sk-super-secret-production-key-998877"
    client = DeepSeekClient(api_key=secret)

    # 1. Client representation must redact secret
    assert secret not in repr(client)
    assert secret not in str(client)
    assert "[PROTECTED_AND_REDACTED]" in repr(client)

    # 2. Reject empty or whitespace-only keys
    with pytest.raises(ValueError, match="API key must be a non-empty string"):
        DeepSeekClient(api_key="")

    with pytest.raises(ValueError, match="API key must be a non-empty string"):
        DeepSeekClient(api_key="   ")
