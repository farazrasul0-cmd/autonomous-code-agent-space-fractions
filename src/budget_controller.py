"""Budget Controller Module.

Enforces strict financial ceilings, conservative pre-call token reservations at
official DeepSeek peak rates, and exact spend reconciliation upon receiving verified
API usage telemetry.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


from enum import Enum


class BudgetException(Exception):
    """Base exception for budget controller errors."""
    pass


class BudgetExceededError(BudgetException):
    """Raised when an operation would exceed the configured budget ceiling."""
    pass


class InvalidUsageDataError(BudgetException):
    """Raised when API usage reporting is corrupt or untrustworthy."""
    pass


class ReservationState(str, Enum):
    """State of an API budget reservation across its lifecycle."""
    RESERVED = "RESERVED"              # Active hold, definitely not transmitted
    TRANSMITTING = "TRANSMITTING"      # Active hold, network transmission initiated/in-flight
    RECONCILED = "RECONCILED"          # Terminal, reconciled with verified provider telemetry
    PERMANENTLY_LOCKED = "PERMANENTLY_LOCKED"  # Terminal, locked conservative spend due to failure/ambiguity
    RELEASED = "RELEASED"              # Terminal, safely released before transmission (zero spend)


@dataclass
class ReservationRecord:
    reservation_id: str
    token_bound: int
    max_output_tokens: int
    reserved_amount: float
    description: str = ""
    state: ReservationState = ReservationState.RESERVED


class BudgetController:
    """Deterministic, thread-safe budget controller for official DeepSeek API billing."""

    # Official DeepSeek Rates (USD per 1,000,000 tokens)
    # Source: https://api-docs.deepseek.com/quick_start/pricing (Verified October 2026)
    PEAK_INPUT_CACHE_MISS_RATE = 1.32 / 1_000_000      # $0.00000132 / token
    PEAK_INPUT_CACHE_HIT_RATE = 0.044 / 1_000_000       # $0.000000044 / token
    PEAK_OUTPUT_RATE = 3.96 / 1_000_000                # $0.00000396 / token

    OFF_PEAK_INPUT_CACHE_MISS_RATE = 0.66 / 1_000_000  # $0.00000066 / token
    OFF_PEAK_INPUT_CACHE_HIT_RATE = 0.022 / 1_000_000   # $0.000000022 / token
    OFF_PEAK_OUTPUT_RATE = 1.98 / 1_000_000            # $0.00000198 / token

    # Default limits
    INTERNAL_CEILING: float = 0.80
    HARD_MAX_CEILING: float = 1.00
    MAX_PERMITTED_OUTPUT_TOKENS: int = 1200
    MAX_INPUT_TOKEN_BOUND: int = 8000
    MAX_PAYLOAD_CHAR_LIMIT: int = 24000

    def __init__(self, ceiling: float = INTERNAL_CEILING) -> None:
        if ceiling <= 0.0 or ceiling > self.HARD_MAX_CEILING:
            raise ValueError(f"Ceiling must be between 0 and {self.HARD_MAX_CEILING}, got {ceiling}")
        self.ceiling = ceiling
        self.cumulative_spend: float = 0.0
        self.active_reservations: Dict[str, ReservationRecord] = {}
        self._all_reservations: Dict[str, ReservationRecord] = {}
        self.permanently_locked_spend: float = 0.0
        self._lock = threading.Lock()

    @classmethod
    def calculate_token_bound(cls, prompt_or_payload: Any, include_framing: bool = False) -> int:
        """Calculates a strict, mathematically conservative token upper bound.
        
        If payload is a dict or list (e.g. structured request), routes to calculate_request_token_bound.
        If payload is raw text, calculates max(byte_len, char_len).
        """
        if isinstance(prompt_or_payload, (dict, list)):
            return cls.calculate_request_token_bound(prompt_or_payload)

        if not prompt_or_payload:
            return 1 if not include_framing else 65

        text_str = str(prompt_or_payload)
        byte_len = len(text_str.encode("utf-8"))
        char_len = len(text_str)
        base = max(byte_len, char_len)
        if include_framing:
            return max(base + 64, 120)
        return base

    @classmethod
    def calculate_request_token_bound(cls, payload: Any) -> int:
        """Calculates a strict conservative token bound for complete serialized API request payloads.
        
        Accounts for ChatML envelope markers, role tags, and tool schemas.
        Guarantees that a trivial prompt inside an HTTP envelope (such as 34 chars)
        strictly exceeds the provider's actual measured input tokens (e.g. 92 tokens).
        """
        import json
        framing_overhead = 64
        if isinstance(payload, (dict, list)):
            serialized = json.dumps(payload, ensure_ascii=False)
            if isinstance(payload, dict):
                messages = payload.get("messages", [])
                tools = payload.get("tools", [])
                framing_overhead += (len(messages) * 8) + (len(tools) * 16)
            elif isinstance(payload, list):
                framing_overhead += len(payload) * 8
        else:
            serialized = str(payload)

        if not serialized:
            return 120

        byte_len = len(serialized.encode("utf-8"))
        char_len = len(serialized)
        return max(max(byte_len, char_len) + framing_overhead, 120)

    def get_total_committed(self) -> float:
        """Returns total allocated funds: cumulative spend + active holds."""
        with self._lock:
            active_holds = sum(r.reserved_amount for r in self.active_reservations.values())
            return self.cumulative_spend + active_holds

    def reserve(
        self,
        reservation_id: str,
        prompt_or_payload: Any = None,
        max_output_tokens: int = MAX_PERMITTED_OUTPUT_TOKENS,
        description: str = "",
        prompt_text: Any = None,
    ) -> float:
        """Reserves worst-case funds at peak uncached rates before an API request.

        Composite Budget Reservation Calculation:
            R = (input_token_bound * PEAK_INPUT_CACHE_MISS_RATE)
              + (max_output_tokens * PEAK_OUTPUT_RATE)

        Where:
            - input_token_bound is calculated via ChatML envelope or text bounding.
            - PEAK_INPUT_CACHE_MISS_RATE = $0.00000132 / token ($1.32 / M tokens).
            - max_output_tokens is bounded by MAX_PERMITTED_OUTPUT_TOKENS (1,200).
            - PEAK_OUTPUT_RATE = $0.00000396 / token ($3.96 / M tokens).

        Worked Numerical Example (Illustrative only; based on current configured rates):
            For an input payload bounded at 2,180 tokens and max_output_tokens = 1,200:
              - Input hold:  2,180 tokens * $0.00000132 = $0.00287760
              - Output hold: 1,200 tokens * $0.00000396 = $0.00475200
              - Total composite reservation hold R:       $0.00762960
            Note: This worked calculation illustrates the pre-flight worst-case hold
            under current in-code pricing constants. It is an internal safety ceiling
            and not a live provider billing guarantee or fixed per-turn invoice.

        Accepts either prompt_or_payload or prompt_text (for backwards compatibility).
        Uses request-level ChatML envelope bounding for structured payloads.
        Raises BudgetExceededError if the reservation would breach the internal ceiling.
        """
        import json
        if not reservation_id:
            raise ValueError("Reservation ID must not be empty")

        target_input = prompt_text if prompt_or_payload is None else prompt_or_payload
        if target_input is None:
            raise ValueError("Must provide prompt_or_payload or prompt_text")

        payload_str = json.dumps(target_input, ensure_ascii=False) if isinstance(target_input, (dict, list)) else str(target_input)

        if len(payload_str) > self.MAX_PAYLOAD_CHAR_LIMIT:
            raise BudgetException(
                f"Prompt payload ({len(payload_str)} chars) exceeds maximum limit of "
                f"{self.MAX_PAYLOAD_CHAR_LIMIT} chars."
            )

        # Distinguish structured payloads from raw text strings
        if isinstance(target_input, (dict, list)) or (isinstance(target_input, str) and target_input.strip().startswith(("{", "["))):
            token_bound = self.calculate_request_token_bound(target_input)
        else:
            token_bound = self.calculate_token_bound(target_input)

        if token_bound > self.MAX_INPUT_TOKEN_BOUND:
            raise BudgetException(
                f"Calculated token bound ({token_bound}) exceeds maximum input bound of "
                f"{self.MAX_INPUT_TOKEN_BOUND} tokens."
            )

        if max_output_tokens > self.MAX_PERMITTED_OUTPUT_TOKENS:
            raise BudgetException(
                f"Requested output tokens ({max_output_tokens}) exceeds maximum permitted "
                f"of {self.MAX_PERMITTED_OUTPUT_TOKENS} tokens."
            )

        # Conservative reservation assuming 100% cache miss and 100% peak pricing
        reserved_amount = (
            (token_bound * self.PEAK_INPUT_CACHE_MISS_RATE)
            + (max_output_tokens * self.PEAK_OUTPUT_RATE)
        )

        with self._lock:
            if reservation_id in self.active_reservations:
                raise BudgetException(f"Reservation ID '{reservation_id}' is already active.")

            active_holds = sum(r.reserved_amount for r in self.active_reservations.values())
            potential_total = self.cumulative_spend + active_holds + reserved_amount

            # Strict pre-flight budget check
            if potential_total > self.ceiling:
                raise BudgetExceededError(
                    f"Reservation of ${reserved_amount:.6f} refused. Total committed would be "
                    f"${potential_total:.6f}, which exceeds internal ceiling of ${self.ceiling:.2f}. "
                    f"(Current spend: ${self.cumulative_spend:.6f}, Active holds: ${active_holds:.6f})"
                )

            record = ReservationRecord(
                reservation_id=reservation_id,
                token_bound=token_bound,
                max_output_tokens=max_output_tokens,
                reserved_amount=reserved_amount,
                description=description,
                state=ReservationState.RESERVED,
            )
            self.active_reservations[reservation_id] = record
            self._all_reservations[reservation_id] = record

            return reserved_amount

    def mark_transmission_started(self, reservation_id: str) -> None:
        """Transitions an active reservation from RESERVED to TRANSMITTING state.

        Once transmission has initiated or may have started, releasing the reservation
        via release_reservation() is strictly prohibited to prevent uncharged spend leaks.
        """
        with self._lock:
            if reservation_id not in self.active_reservations:
                if reservation_id in self._all_reservations:
                    rec = self._all_reservations[reservation_id]
                    raise BudgetException(
                        f"Cannot mark transmission for reservation '{reservation_id}': "
                        f"already terminal in state {rec.state.value}."
                    )
                raise KeyError(f"No active reservation found with ID '{reservation_id}'")

            record = self.active_reservations[reservation_id]
            if record.state != ReservationState.RESERVED:
                raise BudgetException(
                    f"Cannot mark transmission for reservation '{reservation_id}': "
                    f"current state is {record.state.value}."
                )
            record.state = ReservationState.TRANSMITTING

    def release_reservation(self, reservation_id: str) -> float:
        """Safely releases a pre-transmission reservation back to available budget.

        Requirements:
        1. Only releases when transmission has definitely NOT started (state == RESERVED).
        2. Idempotent: repeated calls on an already-released reservation safely return the
           released amount without double-adjusting accounting.
        3. Leaves cumulative_spend and permanently_locked_spend unchanged.
        4. Removes the hold from active_reservations, reducing active holds.
        5. Raises KeyError if reservation_id is unknown.
        6. Raises BudgetException if transmission has already started (TRANSMITTING) or if
           already reconciled/locked.
        """
        with self._lock:
            if reservation_id in self.active_reservations:
                record = self.active_reservations[reservation_id]
                if record.state == ReservationState.TRANSMITTING:
                    raise BudgetException(
                        f"Cannot release reservation '{reservation_id}': transmission may have already "
                        f"started. Ambiguous outcomes must be resolved via record_network_failure()."
                    )
                if record.state != ReservationState.RESERVED:
                    raise BudgetException(
                        f"Cannot release reservation '{reservation_id}': unexpected state {record.state.value}."
                    )

                self.active_reservations.pop(reservation_id)
                record.state = ReservationState.RELEASED
                return record.reserved_amount

            # Check if already released (idempotency support)
            if reservation_id in self._all_reservations:
                record = self._all_reservations[reservation_id]
                if record.state == ReservationState.RELEASED:
                    return record.reserved_amount
                raise BudgetException(
                    f"Cannot release reservation '{reservation_id}': reservation is already terminal "
                    f"in state {record.state.value}."
                )

            raise KeyError(f"No reservation found with ID '{reservation_id}'")

    def get_active_holds(self) -> float:
        """Returns the total sum of all currently active reservation amounts."""
        with self._lock:
            return sum(r.reserved_amount for r in self.active_reservations.values())

    def reconcile(
        self,
        reservation_id: str,
        usage_data: Optional[Dict[str, Any]],
        is_peak: bool = True,
    ) -> float:
        """Reconciles an active reservation against verified API usage telemetry.
        
        If telemetry is missing or untrustworthy, the reservation is permanently committed.
        """
        with self._lock:
            if reservation_id not in self.active_reservations:
                raise KeyError(f"No active reservation found with ID '{reservation_id}'")

            record = self.active_reservations.pop(reservation_id)

            # Guard against missing, None, or non-dict usage reporting
            if not isinstance(usage_data, dict):
                # Ambiguous outcome: lock full reservation permanently to protect budget
                record.state = ReservationState.PERMANENTLY_LOCKED
                self.cumulative_spend += record.reserved_amount
                self.permanently_locked_spend += record.reserved_amount
                return record.reserved_amount

            # Validate that usage numbers are trustworthy and non-negative
            try:
                prompt_tokens = int(usage_data.get("prompt_tokens", -1))
                completion_tokens = int(usage_data.get("completion_tokens", -1))

                # DeepSeek provides prompt_cache_hit_tokens and prompt_cache_miss_tokens
                cache_hit_tokens = int(usage_data.get("prompt_cache_hit_tokens", 0))
                cache_miss_tokens = usage_data.get("prompt_cache_miss_tokens")
                if cache_miss_tokens is None:
                    # Fallback if miss is not explicitly given: miss = prompt_tokens - hit
                    cache_miss_tokens = max(0, prompt_tokens - cache_hit_tokens)
                else:
                    cache_miss_tokens = int(cache_miss_tokens)

                if prompt_tokens < 0 or completion_tokens < 0 or cache_hit_tokens < 0 or cache_miss_tokens < 0:
                    raise ValueError("Negative token counts are invalid")

            except (TypeError, ValueError) as err:
                # Corrupt telemetry: lock full reservation permanently
                record.state = ReservationState.PERMANENTLY_LOCKED
                self.cumulative_spend += record.reserved_amount
                self.permanently_locked_spend += record.reserved_amount
                return record.reserved_amount

            # Determine rate tier
            miss_rate = self.PEAK_INPUT_CACHE_MISS_RATE if is_peak else self.OFF_PEAK_INPUT_CACHE_MISS_RATE
            hit_rate = self.PEAK_INPUT_CACHE_HIT_RATE if is_peak else self.OFF_PEAK_INPUT_CACHE_HIT_RATE
            out_rate = self.PEAK_OUTPUT_RATE if is_peak else self.OFF_PEAK_OUTPUT_RATE

            actual_cost = (
                (cache_miss_tokens * miss_rate)
                + (cache_hit_tokens * hit_rate)
                + (completion_tokens * out_rate)
            )

            # Add actual cost to cumulative spend
            record.state = ReservationState.RECONCILED
            self.cumulative_spend += actual_cost
            return actual_cost

    def record_network_failure(self, reservation_id: str) -> float:
        """Locks reservation into cumulative spend upon ambiguous network timeout or failure."""
        with self._lock:
            if reservation_id not in self.active_reservations:
                # Idempotency support: if already permanently locked, safely return amount without double-locking
                if reservation_id in self._all_reservations:
                    record = self._all_reservations[reservation_id]
                    if record.state == ReservationState.PERMANENTLY_LOCKED:
                        return record.reserved_amount
                raise KeyError(f"No active reservation found with ID '{reservation_id}'")

            record = self.active_reservations.pop(reservation_id)
            record.state = ReservationState.PERMANENTLY_LOCKED
            self.cumulative_spend += record.reserved_amount
            self.permanently_locked_spend += record.reserved_amount
            return record.reserved_amount
