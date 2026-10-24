"""Unit tests for BudgetController.

Validates peak rate calculations, $0.80 ceiling enforcement, pre-call refusal,
usage reconciliation, lock behavior on ambiguous telemetry, and concurrency safety.
"""

import threading
import pytest
from src.budget_controller import (
    BudgetController,
    BudgetExceededError,
    BudgetException,
)


def test_token_bound_calculation():
    """Validates conservative upper bound calculation."""
    # ASCII text
    text_ascii = "Hello world! This is a test."
    bound = BudgetController.calculate_token_bound(text_ascii)
    assert bound >= len(text_ascii)

    # Multi-byte UTF-8 characters (each char is 3 bytes in UTF-8)
    text_unicode = "你好世界，宇宙分数！"
    bound_unicode = BudgetController.calculate_token_bound(text_unicode)
    assert bound_unicode == len(text_unicode.encode("utf-8"))
    assert bound_unicode > len(text_unicode)

    # Empty text
    assert BudgetController.calculate_token_bound("") == 1


def test_payload_token_bound_discrepancy_34_vs_92():
    """CRITICAL TEST: Validates fix for the Gate E 34-char vs 92-token discrepancy.
    
    A prompt of 34 characters inside a complete HTTP chat completion request payload
    was measured by DeepSeek at 92 prompt tokens due to JSON and ChatML envelope framing.
    The new calculate_token_bound() MUST evaluate the complete serialized payload and
    guarantee that the bound is strictly >= 92 tokens.
    """
    raw_prompt = "Reply with exactly one word: PONG."
    assert len(raw_prompt) == 34

    # Full serialized request payload as sent over HTTP
    payload = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "user", "content": raw_prompt}
        ],
        "max_tokens": 10,
        "temperature": 0.0,
        "stream": False,
        "thinking": {"type": "disabled"}
    }

    # Bounding the complete serialized payload
    bound = BudgetController.calculate_token_bound(payload)

    # Provider reported 92 tokens. Bound MUST be >= 92 tokens!
    provider_measured_tokens = 92
    assert bound >= provider_measured_tokens, f"Bound {bound} failed to cover provider tokens {provider_measured_tokens}"

    # Verify reservation on payload covers provider actual cost
    controller = BudgetController(ceiling=0.80)
    reserved = controller.reserve("test_discrepancy", payload, max_output_tokens=10)

    # Actual cost measured in Gate E was $0.00016104
    actual_measured_cost = (92 * BudgetController.PEAK_INPUT_CACHE_MISS_RATE) + (10 * BudgetController.PEAK_OUTPUT_RATE)
    assert reserved >= actual_measured_cost, f"Reserved ${reserved:.6f} must be >= actual ${actual_measured_cost:.6f}"


def test_underestimation_guard_with_tools_and_history():
    """Validates that multi-turn history with tool schemas is conservatively bounded."""
    payload = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "You are a code agent."},
            {"role": "user", "content": "Write fraction solver."},
            {"role": "assistant", "content": "Writing file..."},
            {"role": "tool", "content": "File written successfully."}
        ],
        "tools": [
            {"type": "function", "function": {"name": "write_file", "parameters": {"type": "object"}}}
        ],
        "max_tokens": 1200
    }
    bound = BudgetController.calculate_token_bound(payload)
    # Payload string is ~350 chars; with framing overhead bound must be >= 400
    assert bound >= 400


def test_single_reservation_within_ceiling():
    """Validates reservation amount using exact peak rates."""
    controller = BudgetController(ceiling=0.80)
    prompt = "A" * 1000  # 1000 tokens bound
    # Peak miss rate: $1.32/1M, Peak output: $3.96/1M
    # Reserved = (1000 * 0.00000132) + (1200 * 0.00000396) = 0.00132 + 0.004752 = 0.006072
    res_amount = controller.reserve("req-1", prompt, max_output_tokens=1200)
    assert pytest.approx(res_amount, abs=1e-6) == 0.006072
    assert "req-1" in controller.active_reservations
    assert pytest.approx(controller.get_total_committed(), abs=1e-6) == 0.006072


def test_refusal_when_exceeding_ceiling():
    """Validates that reservation is refused before API call if ceiling would be breached."""
    # Set tight ceiling of $0.01
    controller = BudgetController(ceiling=0.01)
    prompt = "A" * 1000  # Cost will be ~0.006072

    # First request fits within $0.01
    controller.reserve("req-1", prompt, max_output_tokens=1200)

    # Second request of 0.006072 would bring total to 0.012144 > 0.01: must raise BudgetExceededError
    with pytest.raises(BudgetExceededError) as exc_info:
        controller.reserve("req-2", prompt, max_output_tokens=1200)

    assert "exceeds internal ceiling of $0.01" in str(exc_info.value)
    # req-2 must not be added to active holds
    assert "req-2" not in controller.active_reservations


def test_reconciliation_with_verified_usage():
    """Validates reconciliation when API returns complete cache and completion usage."""
    controller = BudgetController(ceiling=0.80)
    prompt = "A" * 2000
    controller.reserve("req-1", prompt, max_output_tokens=1200)

    # Simulated verified DeepSeek telemetry
    usage_data = {
        "prompt_tokens": 2000,
        "completion_tokens": 300,
        "prompt_cache_hit_tokens": 1500,
        "prompt_cache_miss_tokens": 500,
    }

    # Peak miss: 500 * $0.00000132 = 0.000660
    # Peak hit: 1500 * $0.000000044 = 0.000066
    # Peak out: 300 * $0.00000396 = 0.001188
    # Total actual = 0.000660 + 0.000066 + 0.001188 = 0.001914
    actual_cost = controller.reconcile("req-1", usage_data, is_peak=True)

    assert pytest.approx(actual_cost, abs=1e-6) == 0.001914
    assert pytest.approx(controller.cumulative_spend, abs=1e-6) == 0.001914
    # Reservation must be released
    assert "req-1" not in controller.active_reservations
    assert pytest.approx(controller.get_total_committed(), abs=1e-6) == 0.001914


def test_ambiguous_telemetry_locks_reservation():
    """Validates that missing or corrupted usage locks the reservation into cumulative spend."""
    controller = BudgetController(ceiling=0.80)
    prompt = "A" * 1000
    reserved = controller.reserve("req-ambiguous", prompt, max_output_tokens=1200)

    # Ambiguous outcome: usage is None or missing
    locked_amount = controller.reconcile("req-ambiguous", None)

    assert pytest.approx(locked_amount, abs=1e-6) == reserved
    assert pytest.approx(controller.cumulative_spend, abs=1e-6) == reserved
    assert pytest.approx(controller.permanently_locked_spend, abs=1e-6) == reserved
    assert "req-ambiguous" not in controller.active_reservations


def test_network_failure_locks_reservation():
    """Validates that timeout or network drops lock the reservation permanently."""
    controller = BudgetController(ceiling=0.80)
    prompt = "A" * 1000
    reserved = controller.reserve("req-timeout", prompt, max_output_tokens=1200)

    locked_amount = controller.record_network_failure("req-timeout")

    assert pytest.approx(locked_amount, abs=1e-6) == reserved
    assert pytest.approx(controller.cumulative_spend, abs=1e-6) == reserved
    assert pytest.approx(controller.permanently_locked_spend, abs=1e-6) == reserved


def test_concurrent_reservations_thread_safety():
    """Validates thread-safe concurrent reservation attempts against budget ceiling."""
    controller = BudgetController(ceiling=0.05)
    successful_reservations = []
    failed_reservations = []
    lock = threading.Lock()

    def worker(worker_id: int):
        prompt = "A" * 1000  # Cost ~0.006072 each
        try:
            controller.reserve(f"worker-{worker_id}", prompt, max_output_tokens=1200)
            with lock:
                successful_reservations.append(worker_id)
        except BudgetExceededError:
            with lock:
                failed_reservations.append(worker_id)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Ceiling is 0.05; each reservation is ~0.006072. Exactly 8 reservations fit (8 * 0.006072 = 0.048576 <= 0.05)
    # The 9th would be 0.054648 > 0.05, so exactly 8 must succeed and 7 must fail.
    assert len(successful_reservations) == 8
    assert len(failed_reservations) == 7
    assert controller.get_total_committed() <= 0.05


def test_overall_hard_ceiling_enforcement_0_80():
    """Validates that the project-wide $0.80 ceiling is strictly enforced."""
    controller = BudgetController(ceiling=0.80)
    # Simulate cumulative spend reaching $0.795
    controller.cumulative_spend = 0.795

    # Any new reservation (even a tiny one of $0.006072) would bring total to 0.801072 > 0.80
    prompt = "A" * 1000
    with pytest.raises(BudgetExceededError) as exc:
        controller.reserve("breach_attempt", prompt, max_output_tokens=1200)

    assert "exceeds internal ceiling of $0.80" in str(exc.value)
    assert controller.get_total_committed() == 0.795
