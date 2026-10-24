"""Response Parser Module.

Parses and strictly classifies LLM API responses, distinguishing between non-empty text,
valid tool calls, empty or incomplete responses, truncated completions, and provider errors.
Guarantees that an empty assistant response is never treated as a valid generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ResponseClassification(str, Enum):
    NON_EMPTY_TEXT = "NON_EMPTY_TEXT"
    VALID_TOOL_CALL = "VALID_TOOL_CALL"
    EMPTY_OR_INCOMPLETE = "EMPTY_OR_INCOMPLETE"
    TRUNCATED = "TRUNCATED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    REFUSAL = "REFUSAL"


@dataclass
class ParsedResponse:
    classification: ResponseClassification
    is_valid_generation: bool
    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    finish_reason: Optional[str] = None
    reasoning_tokens: int = 0
    error_message: Optional[str] = None
    raw_usage: Optional[Dict[str, Any]] = None


class ResponseParser:
    """Deterministic parser for OpenAI-compatible chat completion responses."""

    @classmethod
    def parse(cls, response_data: Any, http_status: int = 200) -> ParsedResponse:
        """Parses API response payload and classifies execution state."""
        if http_status != 200 or not isinstance(response_data, dict):
            error_msg = f"HTTP {http_status}"
            if isinstance(response_data, dict) and "error" in response_data:
                err_obj = response_data.get("error", {})
                error_msg = err_obj.get("message", str(err_obj)) if isinstance(err_obj, dict) else str(err_obj)
            elif isinstance(response_data, str):
                error_msg = response_data
            return ParsedResponse(
                classification=ResponseClassification.PROVIDER_ERROR,
                is_valid_generation=False,
                error_message=error_msg,
            )

        # Check for provider error key within HTTP 200 payload
        if "error" in response_data:
            err = response_data["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return ParsedResponse(
                classification=ResponseClassification.PROVIDER_ERROR,
                is_valid_generation=False,
                error_message=msg,
            )

        choices = response_data.get("choices", [])
        usage = response_data.get("usage", {})
        completion_details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
        reasoning_tokens = int(completion_details.get("reasoning_tokens", 0)) if isinstance(completion_details, dict) else 0

        if not choices or not isinstance(choices, list):
            return ParsedResponse(
                classification=ResponseClassification.EMPTY_OR_INCOMPLETE,
                is_valid_generation=False,
                error_message="Response contained no choices.",
                reasoning_tokens=reasoning_tokens,
                raw_usage=usage,
            )

        first_choice = choices[0]
        finish_reason = first_choice.get("finish_reason")
        message = first_choice.get("message", {})
        raw_content = message.get("content") or ""
        content = raw_content.strip() if isinstance(raw_content, str) else ""
        tool_calls = message.get("tool_calls") or []

        # 0. Check for explicit refusal or safety filter
        refusal_val = message.get("refusal")
        if refusal_val or finish_reason in ("refusal", "content_filter"):
            reason_text = refusal_val if refusal_val else f"Provider finished with reason '{finish_reason}'."
            return ParsedResponse(
                classification=ResponseClassification.REFUSAL,
                is_valid_generation=False,
                content="",
                finish_reason=finish_reason,
                reasoning_tokens=reasoning_tokens,
                error_message=f"Model refused request: {reason_text}",
                raw_usage=usage,
            )

        # 1. Valid tool call takes precedence if tool calls exist
        if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
            return ParsedResponse(
                classification=ResponseClassification.VALID_TOOL_CALL,
                is_valid_generation=True,
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                reasoning_tokens=reasoning_tokens,
                raw_usage=usage,
            )

        # 2. Non-empty text response
        if content:
            if finish_reason == "length":
                # Truncated but produced partial content
                return ParsedResponse(
                    classification=ResponseClassification.TRUNCATED,
                    is_valid_generation=True,
                    content=content,
                    finish_reason=finish_reason,
                    reasoning_tokens=reasoning_tokens,
                    raw_usage=usage,
                )
            return ParsedResponse(
                classification=ResponseClassification.NON_EMPTY_TEXT,
                is_valid_generation=True,
                content=content,
                finish_reason=finish_reason,
                reasoning_tokens=reasoning_tokens,
                raw_usage=usage,
            )

        # 3. Content is completely empty with zero tool calls (e.g. max_tokens consumed by reasoning)
        if finish_reason == "length":
            error_reason = f"Max tokens exhausted before content was generated (reasoning tokens consumed: {reasoning_tokens})."
        else:
            error_reason = "Model emitted empty content with no tool calls."

        return ParsedResponse(
            classification=ResponseClassification.EMPTY_OR_INCOMPLETE,
            is_valid_generation=False,
            content="",
            finish_reason=finish_reason,
            reasoning_tokens=reasoning_tokens,
            error_message=error_reason,
            raw_usage=usage,
        )
