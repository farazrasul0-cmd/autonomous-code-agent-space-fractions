"""DeepSeek V4 Pro API Client Module.

Provides a synchronous, standard-library-based HTTP client for the official
DeepSeek API (https://api.deepseek.com/chat/completions) with conservative
budget reservations, non-thinking mode enforcement, defensive cancellation
semantics, distinct error classification, zero automatic retries, and strict
credential quarantine.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.budget_controller import BudgetController, BudgetExceededError, BudgetException
from src.response_parser import ParsedResponse, ResponseClassification, ResponseParser

logger = logging.getLogger("DeepSeekClient")


class RequestOutcomeStatus(str, Enum):
    """Distinct outcome statuses for DeepSeek API requests."""
    SUCCESS = "SUCCESS"
    REFUSAL = "REFUSAL"
    TRUNCATED = "TRUNCATED"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    HTTP_ERROR = "HTTP_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    MALFORMED_JSON = "MALFORMED_JSON"
    CANCELLED = "CANCELLED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    BUDGET_REJECTED = "BUDGET_REJECTED"


class DeepSeekClientError(Exception):
    """Base exception for DeepSeek client errors. Strictly redacts API keys."""
    pass


class DeepSeekBudgetExceededError(DeepSeekClientError):
    """Raised when conservative pre-call reservation exceeds remaining budget."""
    pass


class DeepSeekCancellationError(DeepSeekClientError):
    """Raised when request is cancelled by user signal."""
    def __init__(self, message: str, is_ambiguous: bool = False) -> None:
        super().__init__(message)
        self.is_ambiguous = is_ambiguous


@dataclass(frozen=True)
class PreparedRequest:
    """Immutable pre-serialized request bundle guaranteeing parity between reservation and transmission."""
    serialized_bytes: bytes
    payload: Dict[str, Any]
    max_output_tokens: int
    token_bound: int


@dataclass
class DeepSeekResponse:
    """Structured result returned by DeepSeekClient."""
    status: RequestOutcomeStatus
    is_success: bool
    content: Optional[str] = None
    parsed_response: Optional[ParsedResponse] = None
    finish_reason: Optional[str] = None
    reasoning_tokens: int = 0
    raw_usage: Optional[Dict[str, Any]] = None
    http_status: Optional[int] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    reconciled_cost: Optional[float] = None
    reservation_id: Optional[str] = None
    cancellation_outcome: Optional[str] = None  # None, "cancelled_before_transmission", "unknown"


class DeepSeekClient:
    """Synchronous Python standard-library client for DeepSeek V4 Pro."""

    DEFAULT_BASE_URL: str = "https://api.deepseek.com"
    DEFAULT_MODEL: str = "deepseek-v4-pro"
    DEFAULT_TIMEOUT_SECS: float = 30.0
    DEFAULT_MAX_OUTPUT_TOKENS: int = 1200

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        budget_controller: Optional[BudgetController] = None,
        transport_sender: Optional[Callable[[urllib.request.Request, float], Tuple[int, bytes]]] = None,
        audit_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        if not api_key or not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("API key must be a non-empty string.")

        # API key is quarantined in volatile memory only
        self._api_key: str = api_key.strip()
        self.base_url: str = base_url.rstrip("/")
        self.model: str = model
        self.timeout: float = max(1.0, float(timeout))
        if not isinstance(max_output_tokens, int) or max_output_tokens <= 0 or max_output_tokens > 4096:
            raise ValueError(f"max_output_tokens must be an integer between 1 and 4096, got {max_output_tokens}")
        self.max_output_tokens: int = max_output_tokens
        self.budget_controller: Optional[BudgetController] = budget_controller
        self.audit_callback: Optional[Callable[[str, Dict[str, Any]], None]] = audit_callback

        # Custom transport hook for unit test mocking without socket connection
        self._transport_sender: Optional[
            Callable[[urllib.request.Request, float], Tuple[int, bytes]]
        ] = transport_sender

    def __repr__(self) -> str:
        """Safe representation with redacted API credentials."""
        return (
            f"DeepSeekClient(base_url='{self.base_url}', model='{self.model}', "
            f"timeout={self.timeout}, max_output_tokens={self.max_output_tokens}, "
            f"api_key='[PROTECTED_AND_REDACTED]')"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def prepare_payload(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        """Constructs an explicit, non-thinking ChatML request payload."""
        limit = max_tokens if max_tokens is not None else self.max_output_tokens
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": limit,
            "temperature": temperature,
            "stream": False,
            "thinking": {"type": "disabled"},  # Enforce non-thinking mode per documentation
        }
        if tools and isinstance(tools, list):
            payload["tools"] = tools
        return payload

    def prepare_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
    ) -> PreparedRequest:
        """Prepares and serializes request payload once into immutable UTF-8 bytes.

        Reconstructs payload from exact serialized bytes and bounds input tokens
        conservatively. Guarantees 100% parity between budget reservation and
        wire transmission.
        """
        payload = self.prepare_payload(messages, tools, max_tokens, temperature)
        serialized_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        reconstructed = json.loads(serialized_bytes.decode("utf-8"))
        if self.budget_controller is not None:
            token_bound = self.budget_controller.calculate_request_token_bound(reconstructed)
        else:
            token_bound = BudgetController.calculate_request_token_bound(reconstructed)

        return PreparedRequest(
            serialized_bytes=serialized_bytes,
            payload=reconstructed,
            max_output_tokens=payload["max_tokens"],
            token_bound=token_bound,
        )

    def send_chat_completion(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
        reservation_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        skip_budget_reservation: bool = False,
        prepared_request: Optional[PreparedRequest] = None,
    ) -> DeepSeekResponse:
        """Submits a bounded Chat Completions request to the official DeepSeek API.

        Guarantees:
        - Checks cancellation before reservation and before socket transmission.
        - Conservative pre-call budget reservation encompassing full payload + max output tokens.
        - Strict token-bound and output-token parity verification before transmission.
        - Immutable serialized bytes sent without re-serialization or mutation.
        - Rejects requests exceeding remaining budget before network transmission.
        - No automatic retries under any error.
        - Zero credential leakage in exceptions or returned data structures.
        """
        start_time = time.monotonic()
        req_id = reservation_id or f"req-{int(start_time * 1000)}"

        # ---------------------------------------------------------------------
        # 1. Cancellation Check 1: Before Starting
        # ---------------------------------------------------------------------
        if cancel_event and cancel_event.is_set():
            return DeepSeekResponse(
                status=RequestOutcomeStatus.CANCELLED,
                is_success=False,
                error_message="Operation cancelled by user before starting.",
                cancellation_outcome="cancelled_before_transmission",
                reservation_id=req_id,
            )

        if prepared_request is not None:
            payload = prepared_request.payload
            serialized_body = prepared_request.serialized_bytes
            req_token_bound = prepared_request.token_bound
        else:
            if messages is None:
                raise ValueError("Must provide either messages or prepared_request.")
            prep = self.prepare_request(messages, tools, max_tokens, temperature)
            payload = prep.payload
            serialized_body = prep.serialized_bytes
            req_token_bound = prep.token_bound

        # ---------------------------------------------------------------------
        # 2. Budget Preflight Reservation Gate
        # ---------------------------------------------------------------------
        reserved_amount = 0.0
        if not skip_budget_reservation and self.budget_controller is not None:
            try:
                reserved_amount = self.budget_controller.reserve(
                    reservation_id=req_id,
                    prompt_or_payload=payload,
                    max_output_tokens=payload["max_tokens"],
                    description=f"DeepSeek call ({self.model})",
                )
                if self.audit_callback:
                    self.audit_callback("RESERVATION_ACCEPTED", {
                        "event": "RESERVATION_ACCEPTED",
                        "reservation_id": req_id,
                        "token_bound": req_token_bound,
                        "max_output_tokens": payload["max_tokens"],
                        "reserved_usd": round(reserved_amount, 6),
                    })
            except BudgetExceededError as err:
                if self.audit_callback:
                    self.audit_callback("RESERVATION_REJECTED", {
                        "event": "RESERVATION_REJECTED",
                        "reservation_id": req_id,
                        "reason": "BudgetExceededError",
                    })
                return DeepSeekResponse(
                    status=RequestOutcomeStatus.BUDGET_REJECTED,
                    is_success=False,
                    error_message=f"Request rejected by budget controller: {err}",
                    reservation_id=req_id,
                )
        elif skip_budget_reservation and self.budget_controller is not None:
            if not req_id:
                raise BudgetException("Pre-transmission check failed: reservation_id is required when skip_budget_reservation=True.")
            active_rec = self.budget_controller.active_reservations.get(req_id)
            if active_rec is None:
                raise BudgetException(f"Pre-transmission check failed: No active reservation found for '{req_id}'.")
            if active_rec.max_output_tokens != payload["max_tokens"]:
                raise BudgetException(
                    f"Pre-transmission token mismatch: reservation '{req_id}' holds {active_rec.max_output_tokens} "
                    f"output tokens, but request payload specifies {payload['max_tokens']}."
                )
            if active_rec.token_bound != req_token_bound:
                raise BudgetException(
                    f"Pre-transmission token bound mismatch: reservation '{req_id}' holds {active_rec.token_bound} "
                    f"input tokens bound, but request payload evaluates to {req_token_bound}."
                )
            if self.audit_callback:
                self.audit_callback("RESERVATION_ACCEPTED", {
                    "event": "RESERVATION_ACCEPTED",
                    "reservation_id": req_id,
                    "token_bound": req_token_bound,
                    "max_output_tokens": payload["max_tokens"],
                    "reserved_usd": round(active_rec.reserved_amount, 6),
                })

        # ---------------------------------------------------------------------
        # 3. Cancellation Check 2: Immediately Before Network Transmission
        # ---------------------------------------------------------------------
        if cancel_event and cancel_event.is_set():
            if not skip_budget_reservation and self.budget_controller is not None:
                # Cancel reservation cleanly using controller API because no network bytes were sent
                self.budget_controller.release_reservation(req_id)
            return DeepSeekResponse(
                status=RequestOutcomeStatus.CANCELLED,
                is_success=False,
                error_message="Operation cancelled by user before network transmission.",
                cancellation_outcome="cancelled_before_transmission",
                reservation_id=req_id,
            )

        # ---------------------------------------------------------------------
        # 4. Construct Request with Strictly Redacted Headers
        # ---------------------------------------------------------------------
        url = f"{self.base_url}/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "DeepSeek-CodeAgent-Client/1.0",
        }

        request_obj = urllib.request.Request(
            url=url,
            data=serialized_body,
            headers=headers,
            method="POST",
        )

        # ---------------------------------------------------------------------
        # 5. Network Transmission (Finite Timeout, Zero Automatic Retries)
        # ---------------------------------------------------------------------
        http_status: Optional[int] = None
        raw_bytes: bytes = b""
        transmission_started = True

        if self.budget_controller is not None:
            target_res_id = req_id if (not skip_budget_reservation) else reservation_id
            if target_res_id and target_res_id in self.budget_controller.active_reservations:
                self.budget_controller.mark_transmission_started(target_res_id)

        if self.audit_callback:
            self.audit_callback("LOCAL_TRANSPORT_ENTRY", {
                "event": "LOCAL_TRANSPORT_ENTRY",
                "method": "POST",
                "url_path": "/chat/completions",
                "payload_bytes": len(serialized_body),
            })

        try:
            if self._transport_sender is not None:
                # Mock transport execution for offline testing
                http_status, raw_bytes = self._transport_sender(request_obj, self.timeout)
            else:
                # Standard library HTTPS transmission
                with urllib.request.urlopen(request_obj, timeout=self.timeout) as response:
                    http_status = response.getcode()
                    raw_bytes = response.read()

        except urllib.error.HTTPError as err:
            http_status = err.code
            try:
                error_body = err.read().decode("utf-8", errors="replace")
            except Exception:
                error_body = str(err)

            # Reconcile or lock reservation upon HTTP error
            if not skip_budget_reservation and self.budget_controller is not None:
                self.budget_controller.record_network_failure(req_id)

            latency = (time.monotonic() - start_time) * 1000.0
            if self.audit_callback:
                self.audit_callback("LOCAL_TRANSPORT_RETURN", {
                    "event": "LOCAL_TRANSPORT_RETURN",
                    "http_status": http_status,
                    "elapsed_ms": round(latency, 2),
                    "response_bytes": len(error_body.encode("utf-8")),
                })
                self.audit_callback("HTTP_RESPONSE_RECEIVED", {
                    "event": "HTTP_RESPONSE_RECEIVED",
                    "http_status": http_status,
                    "elapsed_ms": round(latency, 2),
                })
            return DeepSeekResponse(
                status=RequestOutcomeStatus.HTTP_ERROR,
                is_success=False,
                http_status=http_status,
                error_message=f"DeepSeek API returned HTTP {http_status}: {error_body}",
                latency_ms=latency,
                reservation_id=req_id,
            )

        except (socket.timeout, TimeoutError, urllib.error.URLError) as err:
            latency = (time.monotonic() - start_time) * 1000.0
            # Network failure after transmission is ambiguous: mark as locked
            if not skip_budget_reservation and self.budget_controller is not None:
                self.budget_controller.record_network_failure(req_id)

            is_timeout = isinstance(err, (socket.timeout, TimeoutError)) or "timed out" in str(err).lower()
            if self.audit_callback:
                self.audit_callback("LOCAL_TRANSPORT_RETURN", {
                    "event": "LOCAL_TRANSPORT_RETURN",
                    "http_status": 0,
                    "elapsed_ms": round(latency, 2),
                    "response_bytes": 0,
                })
            return DeepSeekResponse(
                status=RequestOutcomeStatus.TIMEOUT_ERROR if is_timeout else RequestOutcomeStatus.NETWORK_ERROR,
                is_success=False,
                error_message=f"Network transport error: {type(err).__name__} (timeout={self.timeout}s)",
                latency_ms=latency,
                reservation_id=req_id,
            )

        except RuntimeError:
            # Re-raise test guard exceptions so unmocked network attempts fail tests visibly
            raise

        except Exception as err:
            latency = (time.monotonic() - start_time) * 1000.0
            if not skip_budget_reservation and self.budget_controller is not None:
                self.budget_controller.record_network_failure(req_id)

            if self.audit_callback:
                self.audit_callback("LOCAL_TRANSPORT_RETURN", {
                    "event": "LOCAL_TRANSPORT_RETURN",
                    "http_status": 0,
                    "elapsed_ms": round(latency, 2),
                    "response_bytes": 0,
                })
            return DeepSeekResponse(
                status=RequestOutcomeStatus.NETWORK_ERROR,
                is_success=False,
                error_message=f"Unexpected transport failure: {type(err).__name__}",
                latency_ms=latency,
                reservation_id=req_id,
            )

        latency = (time.monotonic() - start_time) * 1000.0
        if self.audit_callback:
            self.audit_callback("LOCAL_TRANSPORT_RETURN", {
                "event": "LOCAL_TRANSPORT_RETURN",
                "http_status": http_status or 0,
                "elapsed_ms": round(latency, 2),
                "response_bytes": len(raw_bytes),
            })
            self.audit_callback("HTTP_RESPONSE_RECEIVED", {
                "event": "HTTP_RESPONSE_RECEIVED",
                "http_status": http_status or 0,
                "elapsed_ms": round(latency, 2),
            })

        # ---------------------------------------------------------------------
        # 6. Cancellation Check 3: After Network Transmission (Ambiguous Outcome)
        # ---------------------------------------------------------------------
        if cancel_event and cancel_event.is_set():
            # Transmission completed or in-flight when cancelled; provider may have billed tokens
            if not skip_budget_reservation and self.budget_controller is not None:
                self.budget_controller.record_network_failure(req_id)
            return DeepSeekResponse(
                status=RequestOutcomeStatus.CANCELLED,
                is_success=False,
                http_status=http_status,
                error_message="Request was transmitted but cancelled before processing response. Provider outcome unknown.",
                latency_ms=latency,
                reservation_id=req_id,
                cancellation_outcome="unknown",
            )

        # ---------------------------------------------------------------------
        # 7. Response JSON Deserialization
        # ---------------------------------------------------------------------
        try:
            response_json = json.loads(raw_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            if not skip_budget_reservation and self.budget_controller is not None:
                self.budget_controller.record_network_failure(req_id)
            return DeepSeekResponse(
                status=RequestOutcomeStatus.MALFORMED_JSON,
                is_success=False,
                http_status=http_status,
                error_message=f"Failed to parse provider response JSON: {err}",
                latency_ms=latency,
                reservation_id=req_id,
            )

        # ---------------------------------------------------------------------
        # 8. Classification and Parsing via ResponseParser
        # ---------------------------------------------------------------------
        parsed = ResponseParser.parse(response_json, http_status=http_status or 200)

        # Map ResponseParser classification to RequestOutcomeStatus
        if parsed.classification == ResponseClassification.REFUSAL:
            status = RequestOutcomeStatus.REFUSAL
        elif parsed.classification == ResponseClassification.TRUNCATED:
            status = RequestOutcomeStatus.TRUNCATED
        elif parsed.classification == ResponseClassification.EMPTY_OR_INCOMPLETE:
            status = RequestOutcomeStatus.EMPTY_CONTENT
        elif parsed.classification == ResponseClassification.PROVIDER_ERROR:
            status = RequestOutcomeStatus.HTTP_ERROR
        else:
            status = RequestOutcomeStatus.SUCCESS

        if self.audit_callback:
            tc_count = len(parsed.tool_calls) if (parsed and parsed.tool_calls) else 0
            self.audit_callback("PARSER_CLASSIFICATION", {
                "event": "PARSER_CLASSIFICATION",
                "classification": status.value,
                "finish_reason": str(parsed.finish_reason or "none"),
                "tool_calls_count": tc_count,
            })

        # ---------------------------------------------------------------------
        # 9. Budget Reconciliation
        # ---------------------------------------------------------------------
        reconciled_cost: Optional[float] = None
        if not skip_budget_reservation and self.budget_controller is not None:
            if status == RequestOutcomeStatus.SUCCESS and parsed.raw_usage:
                try:
                    reconciled_cost = self.budget_controller.reconcile(
                        reservation_id=req_id,
                        usage_data=parsed.raw_usage,
                        is_peak=True,
                    )
                except Exception as rec_err:
                    self.budget_controller.record_network_failure(req_id)
                    return DeepSeekResponse(
                        status=RequestOutcomeStatus.UNKNOWN_OUTCOME,
                        is_success=False,
                        error_message=f"Budget reconciliation discrepancy: {rec_err}",
                        latency_ms=latency,
                        reservation_id=req_id,
                    )
            else:
                self.budget_controller.record_network_failure(req_id)

        return DeepSeekResponse(
            status=status,
            is_success=(status == RequestOutcomeStatus.SUCCESS),
            content=parsed.content,
            parsed_response=parsed,
            finish_reason=parsed.finish_reason,
            reasoning_tokens=parsed.reasoning_tokens,
            raw_usage=parsed.raw_usage,
            http_status=http_status,
            error_message=parsed.error_message,
            latency_ms=latency,
            reconciled_cost=reconciled_cost,
            reservation_id=req_id,
            cancellation_outcome=None,
        )
