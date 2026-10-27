"""Gate E Final Verification: Exactly One Live Request.

Dispatches exactly ONE Chat Completions request to verify non-empty text generation
under non-thinking mode with max_tokens=32.
No retries, no secondary requests, strict $0.02 and $0.80 ceiling checks,
full request-payload token reservation, and zero credential logging.
"""

import json
import os
import sys

import httpx

from src.budget_controller import BudgetController, BudgetExceededError
from src.response_parser import ResponseClassification, ResponseParser


def load_api_key() -> str:
    """Securely loads the API key from environment or .env without printing."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key

    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip("\"' \r\n")
                    if key:
                        return key

    env_txt = os.path.join(os.path.dirname(__file__), "..", ".env.txt")
    if os.path.exists(env_txt):
        with open(env_txt, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip("\"' \r\n")
                    if key:
                        return key

    return ""


def run_gate_e_final() -> None:
    print("=== Gate E Final Live Verification: Single Request Only ===")

    # 1. Preflight Credential Check (Zero secret logging)
    api_key = load_api_key()
    if not api_key:
        print("[BLOCKER] DEEPSEEK_API_KEY is missing from environment and .env.")
        sys.exit(1)

    print("[1] API Key detected: [PROTECTED_AND_QUARANTINED]. Value, prefix, suffix, and length are masked.")

    # 2. Preflight Configuration & Model Selection
    target_model = "deepseek-v4-pro"
    base_url = "https://api.deepseek.com"
    test_prompt = "Reply with exactly one word: PONG."
    MAX_OUTPUT_TOKENS = 32

    payload = {
        "model": target_model,
        "messages": [
            {"role": "user", "content": test_prompt}
        ],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.0,
        "stream": False,
        "thinking": {"type": "disabled"},  # Explicit non-thinking mode per documentation
    }

    # 3. Preflight Budget Check: Enforce both $0.80 project ceiling and $0.02 test ceiling
    TEST_CEILING = 0.02
    PROJECT_HARD_CEILING = 0.80

    # Ensure cumulative spend from prior test ($0.000161) is preserved
    prior_cumulative_spend = 0.000161
    controller = BudgetController(ceiling=TEST_CEILING)
    controller.cumulative_spend = prior_cumulative_spend

    test_reservation_id = "gate_e_final_single_request"

    # Calculate conservative reservation encompassing full serialized payload
    token_bound = controller.calculate_request_token_bound(payload)
    try:
        reserved_cost = controller.reserve(
            reservation_id=test_reservation_id,
            prompt_or_payload=payload,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            description="Gate E final live request",
        )
    except BudgetExceededError as err:
        print(f"[BLOCKER] Budget reservation refused: {err}")
        sys.exit(1)

    total_projected_spend = controller.get_total_committed()
    if total_projected_spend > TEST_CEILING or total_projected_spend > PROJECT_HARD_CEILING:
        print(f"[BLOCKER] Projected spend ${total_projected_spend:.6f} exceeds budget ceilings.")
        sys.exit(1)

    print(f"[2] Model Confirmed: '{target_model}'")
    print(f"[2] Parameters: thinking={{'type': 'disabled'}}, max_tokens={MAX_OUTPUT_TOKENS}")
    print(f"[2] Input Token Upper Bound: {token_bound} tokens (bounds full request payload)")
    print(f"[2] Peak Rate Assumptions: Input Miss=$1.32/1M, Output=$3.96/1M")
    print(f"[2] Pre-Call Conservative Reservation: ${reserved_cost:.6f}")
    print(f"[2] Pre-Call Total Committed: ${total_projected_spend:.6f} (Test Ceiling: ${TEST_CEILING:.2f}, Project: ${PROJECT_HARD_CEILING:.2f})")

    # 4. Dispatch EXACTLY ONE Request (No retries, no fallbacks)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    print(f"[3] Dispatching exactly ONE HTTP request to {base_url}/chat/completions...")

    http_status = None
    response_json = None

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            http_status = response.status_code
            print(f"[4] HTTP Response Status: {http_status}")

            if http_status != 200:
                print(f"[FAILED] API returned HTTP {http_status}: {response.text}")
                controller.record_network_failure(test_reservation_id)
                sys.exit(2)

            response_json = response.json()

    except Exception as err:
        print(f"[FAILED] Network exception occurred: {err}")
        controller.record_network_failure(test_reservation_id)
        sys.exit(3)

    # 5. Parse, Classify, and Verify Response Content
    parsed = ResponseParser.parse(response_json, http_status=http_status)
    print(f"[5] Response Classification: {parsed.classification.value}")
    print(f"[5] Returned Model: {response_json.get('model', 'N/A')}")
    print(f"[5] Finish Reason: {parsed.finish_reason}")
    print(f"[5] Assistant Text: \"{parsed.content}\"")
    print(f"[5] Reasoning Tokens: {parsed.reasoning_tokens}")
    print(f"[5] Usage Telemetry Received: {json.dumps(parsed.raw_usage)}")

    # Strict content assertion: must be non-empty and exactly 'PONG'
    clean_text = parsed.content.strip().strip(".!\"'")
    is_exact_pong = (clean_text == "PONG")

    if not parsed.is_valid_generation or not is_exact_pong:
        print(f"[FAILED] Verification failed. Expected text exactly 'PONG', got: \"{parsed.content}\" (is_valid: {parsed.is_valid_generation})")
        controller.record_network_failure(test_reservation_id)
        sys.exit(4)

    print("[5] Assistant Text Verification: PASSED (Non-empty and exactly 'PONG').")

    # 6. Reconcile Usage Telemetry
    actual_cost = controller.reconcile(test_reservation_id, parsed.raw_usage, is_peak=True)
    print(f"[6] Exact Reconciled Cost of Request: ${actual_cost:.6f}")
    print(f"[6] Cumulative Session Spend: ${controller.cumulative_spend:.6f} / ${TEST_CEILING:.2f}")
    print("[SUCCESS] Gate E final live test PASSED completely.")


if __name__ == "__main__":
    run_gate_e_final()
