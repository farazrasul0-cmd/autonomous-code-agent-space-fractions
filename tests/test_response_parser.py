"""Unit tests for ResponseParser module.

Tests classification of non-empty text, valid tool calls, empty responses
with reasoning token exhaustion, provider errors, and truncation.
Ensures empty assistant responses are never reported as successful generations.
"""

import pytest
from src.response_parser import ResponseClassification, ResponseParser


def test_classify_valid_non_empty_text():
    """Validates classification of standard text completion."""
    payload = {
        "id": "chatcmpl-123",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "PONG"},
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 5},
    }
    parsed = ResponseParser.parse(payload, http_status=200)
    assert parsed.classification == ResponseClassification.NON_EMPTY_TEXT
    assert parsed.is_valid_generation is True
    assert parsed.content == "PONG"
    assert parsed.finish_reason == "stop"


def test_classify_valid_tool_call():
    """Validates classification of model-directed tool call."""
    payload = {
        "id": "chatcmpl-tool",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "write_file", "arguments": '{"path": "a.txt"}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 60, "completion_tokens": 15},
    }
    parsed = ResponseParser.parse(payload, http_status=200)
    assert parsed.classification == ResponseClassification.VALID_TOOL_CALL
    assert parsed.is_valid_generation is True
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0]["function"]["name"] == "write_file"


def test_classify_empty_content_with_reasoning_exhaustion():
    """CRITICAL TEST: Matches previous Gate E scenario where max_tokens=10 was consumed
    entirely by reasoning tokens, resulting in empty content and zero tool calls.
    Must be classified as EMPTY_OR_INCOMPLETE and is_valid_generation MUST be False.
    """
    payload = {
        "id": "40d9532d-ee61-4e7f-a735-068ff74f008b",
        "choices": [
            {
                "finish_reason": "length",
                "message": {"role": "assistant", "content": ""},
            }
        ],
        "usage": {
            "prompt_tokens": 92,
            "completion_tokens": 10,
            "completion_tokens_details": {"reasoning_tokens": 10},
        },
    }
    parsed = ResponseParser.parse(payload, http_status=200)
    assert parsed.classification == ResponseClassification.EMPTY_OR_INCOMPLETE
    assert parsed.is_valid_generation is False
    assert parsed.content == ""
    assert parsed.reasoning_tokens == 10
    assert "Max tokens exhausted" in (parsed.error_message or "")


def test_classify_provider_error():
    """Validates classification when API returns non-200 or error JSON."""
    error_payload = {
        "error": {
            "message": "Model 'unsupported-model' not found",
            "type": "invalid_request_error",
        }
    }
    parsed = ResponseParser.parse(error_payload, http_status=400)
    assert parsed.classification == ResponseClassification.PROVIDER_ERROR
    assert parsed.is_valid_generation is False
    assert "not found" in (parsed.error_message or "")


def test_classify_truncated_partial_text():
    """Validates classification when text generation is truncated mid-stream."""
    payload = {
        "id": "chatcmpl-trunc",
        "choices": [
            {
                "finish_reason": "length",
                "message": {"role": "assistant", "content": "def solve_fraction():"},
            }
        ],
        "usage": {"prompt_tokens": 80, "completion_tokens": 10},
    }
    parsed = ResponseParser.parse(payload, http_status=200)
    assert parsed.classification == ResponseClassification.TRUNCATED
    assert parsed.is_valid_generation is True
    assert parsed.content == "def solve_fraction():"
    assert parsed.finish_reason == "length"
