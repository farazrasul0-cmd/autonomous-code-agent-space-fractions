"""Mockable Autonomous Agent Loop Module.

Orchestrates multi-turn ReAct cycles with model-directed tool calls, strict schema
validation, observation truncation, repeated failure guards, budget reservation gates,
and deterministic termination.
"""

from __future__ import annotations

import ast
import json
import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from src.budget_controller import BudgetController, BudgetExceededError, BudgetException, ReservationState
from src.path_security import PathSecurityError, safe_read_file, safe_write_file, validate_workspace_path
from src.project_validator import ProjectValidator


class LoopTerminationStatus(str, Enum):
    COMPLETED = "COMPLETED"
    MAX_TURNS_REACHED = "MAX_TURNS_REACHED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    STUCK_LOOP_ABORTED = "STUCK_LOOP_ABORTED"
    MALFORMED_RESPONSE_ABORTED = "MALFORMED_RESPONSE_ABORTED"
    CANCELLED = "CANCELLED"
    REFUSAL_ABORTED = "REFUSAL_ABORTED"
    SAFETY_ABORTED = "SAFETY_ABORTED"
    INCOMPLETE_DELIVERABLES_ABORTED = "INCOMPLETE_DELIVERABLES_ABORTED"
    REPAIR_EXHAUSTED_ABORTED = "REPAIR_EXHAUSTED_ABORTED"


class DeliverableStatus(str, Enum):
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    VALIDATED = "VALIDATED"
    FAILED = "FAILED"
    REPAIR_REQUIRED = "REPAIR_REQUIRED"


@dataclass
class DeliverableManifestEntry:
    path: str
    status: DeliverableStatus = DeliverableStatus.PENDING
    attempts: int = 0
    failure_reasons: List[str] = field(default_factory=list)
    last_verified_size: int = 0


@dataclass
class ToolCall:
    tool_id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ModelTurnResponse:
    content: Optional[str]
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_usage: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None
    status: Optional[str] = None
    error_message: Optional[str] = None
    cancellation_outcome: Optional[str] = None


class DeepSeekAgentAdapter:
    """Adapts DeepSeekClient to LLMClientProtocol for AgentLoop execution.

    Translates send_chat_completion() results to ModelTurnResponse, safely parsing
    untrusted JSON tool-call arguments, mapping provider outcome statuses, and
    propagating cancellation and usage metadata without duplicating budget reservations.
    """

    def __init__(
        self,
        client: Any,
        max_output_tokens: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        client_tokens = getattr(client, "max_output_tokens", None)
        if max_output_tokens is not None:
            if not isinstance(max_output_tokens, int) or max_output_tokens <= 0 or max_output_tokens > 4096:
                raise ValueError(f"max_output_tokens must be an integer between 1 and 4096, got {max_output_tokens}")
            if client_tokens is not None and client_tokens != max_output_tokens:
                raise ValueError(
                    f"Adapter output limit ({max_output_tokens}) does not match client limit ({client_tokens})."
                )
            effective_tokens = max_output_tokens
        elif client_tokens is not None:
            effective_tokens = client_tokens
        else:
            effective_tokens = 1200

        self.client = client
        self.max_output_tokens = effective_tokens
        self.cancel_event = cancel_event

    def prepare_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """Exposes prepared request creation for AgentLoop budget reservation parity."""
        if hasattr(self.client, "prepare_request"):
            return self.client.prepare_request(
                messages=messages,
                tools=tools,
                max_tokens=self.max_output_tokens,
            )
        return None

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        reservation_id: Optional[str] = None,
        prepared_request: Optional[Any] = None,
    ) -> ModelTurnResponse:
        # Check cancellation before dispatch
        if self.cancel_event and self.cancel_event.is_set():
            return ModelTurnResponse(
                content=None,
                tool_calls=[],
                finish_reason="cancelled",
                status="CANCELLED",
                error_message="Agent loop cancelled by user signal.",
                cancellation_outcome="cancelled_before_transmission",
            )

        kwargs: Dict[str, Any] = {
            "messages": messages,
            "tools": tools,
            "max_tokens": self.max_output_tokens,
            "cancel_event": self.cancel_event,
            "skip_budget_reservation": True,
            "reservation_id": reservation_id,
        }
        if prepared_request is not None:
            import inspect
            sig = inspect.signature(self.client.send_chat_completion)
            if "prepared_request" in sig.parameters:
                kwargs["prepared_request"] = prepared_request

        # Call client with skip_budget_reservation=True (AgentLoop manages reservation)
        response = self.client.send_chat_completion(**kwargs)

        return self.adapt_response(response)

    @classmethod
    def adapt_response(cls, response: Any) -> ModelTurnResponse:
        """Converts DeepSeekResponse to ModelTurnResponse with strict tool schema parsing."""
        from src.deepseek_client import RequestOutcomeStatus

        status_val = response.status.value if hasattr(response.status, "value") else str(response.status)

        if response.status == RequestOutcomeStatus.SUCCESS:
            parsed_tools: List[ToolCall] = []
            if response.parsed_response and response.parsed_response.tool_calls:
                for idx, raw_tc in enumerate(response.parsed_response.tool_calls):
                    call_id = raw_tc.get("id") or f"call_{idx}"
                    fn = raw_tc.get("function") or {}
                    fn_name = fn.get("name", "unknown")
                    raw_args = fn.get("arguments", {})

                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                            if not isinstance(parsed_args, dict):
                                parsed_args = {
                                    "_parse_error": f"Tool arguments JSON must evaluate to a dictionary object, got {type(parsed_args).__name__}.",
                                    "_raw": raw_args,
                                }
                        except Exception as json_err:
                            parsed_args = {
                                "_parse_error": f"Malformed tool arguments JSON: {json_err}",
                                "_raw": raw_args,
                            }
                    elif isinstance(raw_args, dict):
                        parsed_args = raw_args
                    else:
                        parsed_args = {
                            "_parse_error": f"Tool arguments must be JSON string or dict, got {type(raw_args).__name__}.",
                            "_raw": str(raw_args),
                        }

                    parsed_tools.append(ToolCall(tool_id=call_id, name=fn_name, arguments=parsed_args))

            return ModelTurnResponse(
                content=response.content,
                tool_calls=parsed_tools,
                raw_usage=response.raw_usage,
                finish_reason=response.finish_reason,
                status="SUCCESS",
            )

        elif response.status == RequestOutcomeStatus.REFUSAL:
            return ModelTurnResponse(
                content="",
                tool_calls=[],
                raw_usage=response.raw_usage,
                finish_reason="refusal",
                status="REFUSAL",
                error_message=response.error_message or "Model refused request.",
            )

        elif response.status == RequestOutcomeStatus.TRUNCATED:
            return ModelTurnResponse(
                content=response.content,
                tool_calls=[],
                raw_usage=response.raw_usage,
                finish_reason="length",
                status="TRUNCATED",
                error_message="Model response truncated by token limit.",
            )

        elif response.status == RequestOutcomeStatus.EMPTY_CONTENT:
            return ModelTurnResponse(
                content="",
                tool_calls=[],
                raw_usage=response.raw_usage,
                finish_reason="empty",
                status="EMPTY_CONTENT",
                error_message="Model emitted empty content with no tool calls.",
            )

        elif response.status == RequestOutcomeStatus.CANCELLED:
            return ModelTurnResponse(
                content=None,
                tool_calls=[],
                raw_usage=None,
                finish_reason="cancelled",
                status="CANCELLED",
                error_message=response.error_message or "Operation cancelled.",
                cancellation_outcome=getattr(response, "cancellation_outcome", None),
            )

        elif response.status == RequestOutcomeStatus.BUDGET_REJECTED:
            return ModelTurnResponse(
                content=None,
                tool_calls=[],
                raw_usage=None,
                finish_reason="budget_rejected",
                status="BUDGET_REJECTED",
                error_message=response.error_message or "Request rejected by budget controller.",
            )

        elif response.status == RequestOutcomeStatus.TIMEOUT_ERROR:
            return ModelTurnResponse(
                content=None,
                tool_calls=[],
                raw_usage=None,
                finish_reason="timeout",
                status="TIMEOUT_ERROR",
                error_message=response.error_message or "Network socket timed out.",
            )

        elif response.status == RequestOutcomeStatus.HTTP_ERROR:
            return ModelTurnResponse(
                content=None,
                tool_calls=[],
                raw_usage=None,
                finish_reason="http_error",
                status="HTTP_ERROR",
                error_message=response.error_message or f"HTTP error {response.http_status}.",
            )

        elif response.status == RequestOutcomeStatus.MALFORMED_JSON:
            return ModelTurnResponse(
                content=None,
                tool_calls=[],
                raw_usage=None,
                finish_reason="malformed_json",
                status="MALFORMED_JSON",
                error_message=response.error_message or "Malformed response JSON from provider.",
            )

        else:
            return ModelTurnResponse(
                content=None,
                tool_calls=[],
                raw_usage=None,
                finish_reason="network_error",
                status="NETWORK_ERROR",
                error_message=response.error_message or f"Transport failure ({status_val}).",
            )


@dataclass
class TranscriptEntry:
    turn_index: int
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_result: Optional[str] = None


@dataclass
class AgentLoopResult:
    status: LoopTerminationStatus
    total_turns: int
    transcript: List[TranscriptEntry]
    final_response: Optional[str] = None
    error_message: Optional[str] = None


class CommandExecutorProtocol(Protocol):
    """Protocol for executing commands, allowing seamless test mocking."""
    def execute(self, command: str, workspace_root: str) -> Dict[str, Any]:
        ...


class LLMClientProtocol(Protocol):
    """Protocol for LLM interactions, allowing deterministic offline test doubles."""
    def chat_completion(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> ModelTurnResponse:
        ...


class AgentLoop:
    """Core ReAct Agent loop with strict contract enforcement and defensive execution."""

    # Eight canonical deliverables plus bounded recovery capacity for truncated or
    # schema-rejected writes.  Sixteen worst-case reserved turns remain comfortably
    # below the application's $0.80 ceiling at configured peak token rates.
    MAX_TURNS_DEFAULT = 24
    MAX_OBSERVATION_CHARS = 1500
    # Leave headroom for model/options fields added by DeepSeekClient.prepare_request.
    # Once this conservative request estimate is reached, older completed ReAct
    # envelopes are redundant because validated deliverables live on disk and the
    # current checklist directive is authoritative.
    CONTEXT_COMPACTION_TRIGGER = 7000
    MAX_CONSECUTIVE_IDENTICAL_FAILURES = 3

    # Formal tool schemas exposed to the model
    TOOLS_SCHEMA = [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write text content to a file inside the designated workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to file."},
                        "content": {"type": "string", "description": "Text content to write."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read text content from a file inside the designated workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to file."}
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List files in the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subpath": {"type": "string", "description": "Optional relative subpath.", "default": "."}
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Execute an allowed testing or validation command.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command string to run."}
                    },
                    "required": ["command"],
                },
            },
        },
    ]

    CANONICAL_SPACE_FRACTIONS_DELIVERABLES: List[str] = [
        "space_fractions/fraction_math.py",
        "space_fractions/game_engine.py",
        "space_fractions/question_bank.py",
        "space_fractions/ui_screens.py",
        "space_fractions/main.py",
        "tests/test_fraction_game.py",
        "requirements.txt",
        "README.md",
    ]

    MAX_FILE_WRITE_BYTES = 500_000  # 500 KB limit per file write

    def __init__(
        self,
        workspace_root: str,
        llm_client: LLMClientProtocol,
        command_executor: Optional[CommandExecutorProtocol] = None,
        budget_controller: Optional[BudgetController] = None,
        max_turns: int = MAX_TURNS_DEFAULT,
        cancel_event: Optional[threading.Event] = None,
        block_run_command: bool = True,
        session_id: Optional[str] = None,
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        authorized_target: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        deliverables_checklist: Optional[List[str]] = None,
        max_retries_per_deliverable: int = 3,
        max_repair_attempts: int = 2,
        strict_completeness: bool = False,
    ) -> None:
        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        self.llm_client = llm_client
        self.command_executor = command_executor
        self.budget_controller = budget_controller or BudgetController(ceiling=0.80)
        self.max_turns = max_turns
        self.cancel_event = cancel_event
        self.block_run_command = block_run_command
        self.session_id = session_id
        self.tools_schema = tools_schema if tools_schema is not None else self.TOOLS_SCHEMA
        self.authorized_target = authorized_target
        self.deliverables_checklist = list(deliverables_checklist) if deliverables_checklist is not None else None
        self.max_retries_per_deliverable = max_retries_per_deliverable
        self.max_repair_attempts = max_repair_attempts
        self.strict_completeness = strict_completeness

        self.deliverable_retries: Dict[str, int] = {}
        self._repair_attempts: int = 0
        self.deliverables_manifest: Dict[str, DeliverableManifestEntry] = {}
        if self.deliverables_checklist:
            for p in self.deliverables_checklist:
                self.deliverables_manifest[p] = DeliverableManifestEntry(path=p)
            self.update_deliverables_manifest()

        client_tokens = getattr(llm_client, "max_output_tokens", None)
        if max_output_tokens is not None:
            if not isinstance(max_output_tokens, int) or max_output_tokens <= 0 or max_output_tokens > 4096:
                raise ValueError(f"max_output_tokens must be an integer between 1 and 4096, got {max_output_tokens}")
            if client_tokens is not None and client_tokens != max_output_tokens:
                raise ValueError(
                    f"AgentLoop max_output_tokens ({max_output_tokens}) does not match client tokens ({client_tokens})."
                )
            self.max_output_tokens = max_output_tokens
        elif client_tokens is not None:
            self.max_output_tokens = client_tokens
        else:
            self.max_output_tokens = getattr(self.budget_controller, "DEFAULT_OUTPUT_TOKENS", 1200)

        self.transcript: List[TranscriptEntry] = []
        self._consecutive_failures = 0
        self._last_failing_signature = ""

    def validate_deliverable(self, rel_path: str) -> Tuple[bool, List[str]]:
        """Exhaustively validates deliverable existence, non-emptiness, syntax, and interfaces."""
        norm_path = os.path.normpath(rel_path).replace("\\", "/")
        full_path = os.path.join(self.workspace_root, norm_path)
        if not os.path.isfile(full_path):
            return False, [f"File '{norm_path}' does not exist on disk."]
        try:
            size = os.path.getsize(full_path)
            if size == 0:
                return False, [f"File '{norm_path}' is empty (0 bytes)."]
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            # Must not contain compaction placeholder or failure marker
            if (
                self.COMPACTED_CONTENT_MARKER in content
                or "[content omitted after local write]" in content
                or "[write failed:" in content
            ):
                return False, [f"File '{norm_path}' contains compaction placeholder or failure marker."]

            if norm_path.endswith(".py"):
                try:
                    tree = ast.parse(content, filename=norm_path)
                except SyntaxError as se:
                    return False, [f"Syntax error in '{norm_path}': {se}"]

                # Verify non-trivial AST body
                if not tree.body:
                    return False, [f"File '{norm_path}' contains zero code statements (comment/empty stub only)."]

                package_ok, package_errors = ProjectValidator.validate_package_imports(tree, norm_path)
                if not package_ok:
                    return False, package_errors

                interface_validators = {
                    "space_fractions/fraction_math.py": ProjectValidator.validate_fraction_math_interface,
                    "space_fractions/game_engine.py": ProjectValidator.validate_game_engine_interface,
                    "space_fractions/ui_screens.py": ProjectValidator.validate_ui_screens_interface,
                    "space_fractions/main.py": ProjectValidator.validate_gui_app_interface,
                    "tests/test_fraction_game.py": ProjectValidator.validate_test_suite_interface,
                }
                validator = interface_validators.get(norm_path)
                if validator is not None:
                    interface_ok, interface_errors = validator(tree)
                    if not interface_ok:
                        return False, interface_errors

            elif norm_path == "README.md":
                if size == 0:
                    return False, [f"README.md is empty (0 bytes)."]

            return True, []
        except Exception as err:
            return False, [f"Unexpected error validating '{norm_path}': {err}"]

    def is_deliverable_complete(self, rel_path: str) -> bool:
        """Determines if a required deliverable is complete and valid on disk."""
        valid, _ = self.validate_deliverable(rel_path)
        return valid

    def update_deliverables_manifest(self) -> None:
        """Updates manifest state for all checklist deliverables from current disk state."""
        if not self.deliverables_checklist:
            return
        for rel_path in self.deliverables_checklist:
            entry = self.deliverables_manifest.setdefault(rel_path, DeliverableManifestEntry(path=rel_path))
            full_path = os.path.join(self.workspace_root, os.path.normpath(rel_path))
            if os.path.isfile(full_path):
                entry.last_verified_size = os.path.getsize(full_path)
                valid, reasons = self.validate_deliverable(rel_path)
                if valid:
                    entry.status = DeliverableStatus.VALIDATED
                    entry.failure_reasons = []
                else:
                    entry.status = DeliverableStatus.FAILED
                    entry.failure_reasons = reasons
            else:
                entry.last_verified_size = 0
                if entry.attempts > 0:
                    entry.status = DeliverableStatus.FAILED
                    entry.failure_reasons = ["File not found on disk after write attempt."]
                else:
                    entry.status = DeliverableStatus.PENDING
                    entry.failure_reasons = []

    def get_manifest_summary(self) -> Dict[str, Dict[str, Any]]:
        """Returns structured summary of deliverables manifest."""
        self.update_deliverables_manifest()
        return {
            p: {
                "status": entry.status.value,
                "attempts": entry.attempts,
                "size_bytes": entry.last_verified_size,
                "failure_reasons": list(entry.failure_reasons),
            }
            for p, entry in self.deliverables_manifest.items()
        }

    def get_deliverables_status(self) -> Tuple[List[str], List[str], Optional[str]]:
        """Returns (completed_list, missing_list, next_target_rel_path) based on validated disk state."""
        if not self.deliverables_checklist:
            return [], [], None
        self.update_deliverables_manifest()
        completed = [p for p in self.deliverables_checklist if self.deliverables_manifest[p].status == DeliverableStatus.VALIDATED]
        missing = [p for p in self.deliverables_checklist if p not in completed]
        next_target = missing[0] if missing else None
        return completed, missing, next_target

    @classmethod
    def get_deliverable_hint(cls, target: str) -> str:
        """Returns concise targeted instructions to prevent model over-generation and output truncation."""
        if target == "space_fractions/fraction_math.py":
            return (
                "This is a package module: import siblings only with '.sibling' or 'space_fractions.sibling', never bare sibling imports. "
                "Use public numerator and denominator attributes. The constructor MUST preserve the supplied values without reducing them; "
                "simplify() performs reduction and must return a new Fraction without mutating the original; "
                "add() and subtract() MUST return simplified results by calling .simplify(); "
                "__eq__() MUST delegate to equals() for value equality; __str__() MUST call simplify() first and render "
                "whole-number values without a denominator (Fraction(6, 3) -> '2'); "
                "equals(non-Fraction) must return False. MathQuestionGenerator.generate() MUST return a dict "
                "with exactly these shared keys: prompt, options, correct_index, explanation; options MUST be a list "
                "of display strings (not Fraction objects), because the engine and Tkinter UI render them directly. "
                "Question and distractor generation MUST never construct a Fraction with denominator zero; "
                "guard randomized denominator arithmetic before constructing Fraction objects. Tests MUST call "
                "generate() repeatedly (at least 100 times) to verify every option is a string, every index is valid, "
                "and generation never raises."
            )
        if target == "space_fractions/game_engine.py":
            return (
                "This is a package module: import siblings only with '.sibling' or 'space_fractions.sibling', never bare sibling imports. "
                "Consume generate() as a dict with prompt, options, correct_index, explanation. Expose public score, "
                "lives, current_question and state; initialize in PLAYING with a current question. Implement play(), "
                "pause(), resume(), submit_answer(index)->bool, is_game_over(), view_score(), and viewScore()."
            )
        if target == "space_fractions/question_bank.py":
            return (
                "This is a package module: import siblings only with '.sibling' or 'space_fractions.sibling', never bare sibling imports. "
                "Provide Grade 6 fraction questions using the shared dict contract: "
                "prompt, options, correct_index, explanation."
            )
        if target == "space_fractions/ui_screens.py":
            return (
                "This is a package module: import siblings only with '.sibling' or 'space_fractions.sibling', never bare sibling imports. "
                "Render the shared question dict keys prompt, options, correct_index, explanation. "
                "Define FractionVisualizer and HUDFrame as reusable Tkinter widgets."
            )
        if target == "space_fractions/main.py":
            return (
                "Keep space_fractions/main.py minimal and focused (under 150 lines): define class SpaceFractionsApp(root) and def main(). "
                "Use package-safe relative imports: from .ui_screens import FractionVisualizer, HUDFrame and from .game_engine import GameEngine, GameState. "
                "Connect start, play, pause, and game-over screens without re-implementing existing widgets or math logic."
            )
        if target == "tests/test_fraction_game.py":
            return (
                "Write self-contained unittest tests matching the exact shared contract: generate() returns a dict with "
                "prompt/options/correct_index/explanation; GameEngine starts PLAYING with current_question and "
                "submit_answer(index) returns bool. Verify Fraction arithmetic and state transitions."
            )
        if target == "requirements.txt":
            return "Python standard library only. Write a comment header noting no third-party dependencies are required."
        if target == "README.md":
            return "Write README.md with project overview, Grade 6 educational objectives, Kruchten 4+1 architecture traceability, and launch instructions."
        return ""

    def run(self, initial_prompt: str, session_id: Optional[str] = None) -> AgentLoopResult:
        """Runs the multi-turn agent loop until completion or safety boundary is reached."""
        effective_session_id = session_id or self.session_id

        # Auto-configure checklist if matching Space Fractions task
        if self.deliverables_checklist is None:
            prompt_lower = initial_prompt.lower()
            if "space fraction" in prompt_lower or "grade 6" in prompt_lower or "fraction_math" in prompt_lower:
                self.deliverables_checklist = list(self.CANONICAL_SPACE_FRACTIONS_DELIVERABLES)

        user_content = initial_prompt
        if self.deliverables_checklist:
            completed, missing, next_target = self.get_deliverables_status()
            if missing and next_target:
                hint = self.get_deliverable_hint(next_target)
                hint_str = f" {hint}" if hint else ""
                user_content += (
                    f"\n\nCHECKLIST: {len(completed)}/{len(self.deliverables_checklist)} completed. "
                    f"DIRECTIVE FOR TURN 1: Write ONLY '{next_target}' in this turn using write_file.{hint_str}"
                )

        system_instruction = (
            "You are a disciplined coding agent. Write clean code and verify with tests.\n"
            "Note: In previous turns, file contents in completed write_file calls are compacted "
            "in conversation history as '[content omitted after local write]' to save context. "
            "Never output this placeholder yourself; always provide full, complete file content."
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_content},
        ]
        self.transcript.append(TranscriptEntry(turn_index=0, role="user", content=initial_prompt))

        turn_index = 0
        while turn_index < self.max_turns:
            # Check early cancellation before turn begins
            if self.cancel_event and self.cancel_event.is_set():
                return AgentLoopResult(
                    status=LoopTerminationStatus.CANCELLED,
                    total_turns=turn_index,
                    transcript=self.transcript,
                    error_message="Agent loop cancelled by user signal before turn execution.",
                )

            turn_index += 1

            # Successful file bodies are already compacted below, but retaining every
            # assistant/tool/directive envelope still grows linearly and previously
            # made the canonical eight-deliverable task fail before turn 8.  Keep the
            # immutable task plus the most recent ReAct exchange when the next request
            # approaches the hard input bound.  The full audit transcript is retained.
            messages = self._compact_message_history_if_needed(messages)

            if effective_session_id:
                reservation_id = f"run-{effective_session_id}-turn-{turn_index}"
            else:
                reservation_id = f"turn-{turn_index}"

            # 1. Budget reservation gate
            prepared_request = None
            if hasattr(self.llm_client, "prepare_request"):
                try:
                    prepared_request = self.llm_client.prepare_request(messages, self.tools_schema)
                except Exception as prep_err:
                    return AgentLoopResult(
                        status=LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED,
                        total_turns=turn_index - 1,
                        transcript=self.transcript,
                        error_message=f"Failed to prepare request: {prep_err}",
                    )

            if prepared_request is not None:
                reserve_payload = prepared_request.payload
                effective_max_tokens = prepared_request.max_output_tokens
            else:
                reserve_payload = json.dumps(messages)
                effective_max_tokens = self.max_output_tokens

            if effective_max_tokens > self.budget_controller.MAX_PERMITTED_OUTPUT_TOKENS:
                return AgentLoopResult(
                    status=LoopTerminationStatus.BUDGET_EXCEEDED,
                    total_turns=turn_index - 1,
                    transcript=self.transcript,
                    error_message=(
                        f"Output token limit mismatch: AgentLoop output tokens ({effective_max_tokens}) "
                        f"exceeds BudgetController limit ({self.budget_controller.MAX_PERMITTED_OUTPUT_TOKENS})."
                    ),
                )
            try:
                self.budget_controller.reserve(
                    reservation_id=reservation_id,
                    prompt_or_payload=reserve_payload,
                    max_output_tokens=effective_max_tokens,
                    description=f"Turn {turn_index}",
                )
            except (BudgetExceededError, BudgetException) as err:
                return AgentLoopResult(
                    status=LoopTerminationStatus.BUDGET_EXCEEDED,
                    total_turns=turn_index - 1,
                    transcript=self.transcript,
                    error_message=f"Budget ceiling exceeded before turn {turn_index}: {err}",
                )

            # 2. Query model (via injected client)
            try:
                import inspect
                sig = inspect.signature(self.llm_client.chat_completion)
                call_kwargs = {}
                if "reservation_id" in sig.parameters:
                    call_kwargs["reservation_id"] = reservation_id
                if "prepared_request" in sig.parameters and prepared_request is not None:
                    call_kwargs["prepared_request"] = prepared_request
                response = self.llm_client.chat_completion(
                    messages, self.tools_schema, **call_kwargs
                )
            except Exception as err:
                self.budget_controller.record_network_failure(reservation_id)
                return AgentLoopResult(
                    status=LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED,
                    total_turns=turn_index,
                    transcript=self.transcript,
                    error_message=f"Model query failed: {err}",
                )

            # 3. Check response status from adapter or model turn
            resp_status = getattr(response, "status", None)
            if resp_status == "CANCELLED" or (self.cancel_event and self.cancel_event.is_set()):
                canc_outcome = getattr(response, "cancellation_outcome", None)
                rec = self.budget_controller.active_reservations.get(reservation_id)
                if canc_outcome == "cancelled_before_transmission" and rec and rec.state == ReservationState.RESERVED:
                    self.budget_controller.release_reservation(reservation_id)
                else:
                    self.budget_controller.record_network_failure(reservation_id)

                return AgentLoopResult(
                    status=LoopTerminationStatus.CANCELLED,
                    total_turns=turn_index - 1,
                    transcript=self.transcript,
                    error_message=getattr(response, "error_message", None) or "Agent loop cancelled by user signal.",
                )

            if resp_status == "REFUSAL":
                self.budget_controller.reconcile(reservation_id, response.raw_usage)
                return AgentLoopResult(
                    status=LoopTerminationStatus.REFUSAL_ABORTED,
                    total_turns=turn_index,
                    transcript=self.transcript,
                    error_message=getattr(response, "error_message", None) or "Model refused request.",
                )

            if resp_status == "BUDGET_REJECTED":
                if reservation_id in self.budget_controller.active_reservations:
                    self.budget_controller.release_reservation(reservation_id)
                return AgentLoopResult(
                    status=LoopTerminationStatus.BUDGET_EXCEEDED,
                    total_turns=turn_index - 1,
                    transcript=self.transcript,
                    error_message=getattr(response, "error_message", None) or "Budget rejected.",
                )

            if resp_status in ("MALFORMED_JSON", "EMPTY_CONTENT", "TRUNCATED", "HTTP_ERROR", "TIMEOUT_ERROR", "NETWORK_ERROR"):
                self.budget_controller.record_network_failure(reservation_id)
                return AgentLoopResult(
                    status=LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED,
                    total_turns=turn_index,
                    transcript=self.transcript,
                    error_message=getattr(response, "error_message", None) or f"Model query aborted with status {resp_status}.",
                )

            # 4. Budget reconciliation
            self.budget_controller.reconcile(reservation_id, response.raw_usage)

            # 5. Handle response content
            if response.content:
                self.transcript.append(
                    TranscriptEntry(turn_index=turn_index, role="assistant", content=response.content)
                )

            # If no tool calls, check if model emitted markdown file code blocks in text content
            if not response.tool_calls:
                if response.content:
                    extracted = self._extract_markdown_file_blocks(response.content)
                    if extracted:
                        self.transcript.append(
                            TranscriptEntry(
                                turn_index=turn_index,
                                role="tool_result",
                                tool_result=f"Extracted and wrote {len(extracted)} file(s) from response markdown blocks.",
                            )
                        )

                # Check deliverable checklist completion
                if self.deliverables_checklist and self.strict_completeness:
                    completed, missing, next_target = self.get_deliverables_status()
                    if missing:
                        return AgentLoopResult(
                            status=LoopTerminationStatus.INCOMPLETE_DELIVERABLES_ABORTED,
                            total_turns=turn_index,
                            transcript=self.transcript,
                            error_message=(
                                f"Model terminated turn without tool calls, leaving {len(missing)} deliverable(s) incomplete: "
                                f"{missing}. Completed: {completed}."
                            ),
                            final_response=response.content,
                        )
                    else:
                        # Run integration validation if all checklist files are present
                        is_space_frac = any("space_fractions" in p for p in self.deliverables_checklist)
                        from src.project_validator import ProjectValidator
                        val_res = (
                            ProjectValidator.validate_project(self.workspace_root, require_full_game=True, require_manifest=False)
                            if is_space_frac
                            else ProjectValidator.validate_generic_project(self.workspace_root)
                        )
                        if not val_res.success:
                            if self._repair_attempts < self.max_repair_attempts and turn_index < self.max_turns:
                                self._repair_attempts += 1
                                err_bullets = "\n".join(f"- {e}" for e in val_res.errors[:4])
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        f"INTEGRATION VALIDATION FAILURE (Repair Attempt {self._repair_attempts}/{self.max_repair_attempts}):\n"
                                        f"The generated project failed static contract validation with errors:\n"
                                        f"{err_bullets}\n"
                                        f"DIRECTIVE: Use write_file to repair the affected files. Keep all other verified code intact."
                                    ),
                                })
                                continue
                            else:
                                return AgentLoopResult(
                                    status=LoopTerminationStatus.REPAIR_EXHAUSTED_ABORTED,
                                    total_turns=turn_index,
                                    transcript=self.transcript,
                                    error_message=f"Static integration contract validation failed after {self._repair_attempts} repair attempts: {'; '.join(val_res.errors[:3])}",
                                    final_response=response.content,
                                )

                return AgentLoopResult(
                    status=LoopTerminationStatus.COMPLETED,
                    total_turns=turn_index,
                    transcript=self.transcript,
                    final_response=response.content,
                )

            # Check cancellation before dispatching tool calls
            if self.cancel_event and self.cancel_event.is_set():
                return AgentLoopResult(
                    status=LoopTerminationStatus.CANCELLED,
                    total_turns=turn_index,
                    transcript=self.transcript,
                    error_message="Agent loop cancelled by user signal before tool dispatch.",
                )

            # 6. Process tool calls
            # First, record original assistant tool calls in transcript for audit integrity
            self.transcript.append(
                TranscriptEntry(
                    turn_index=turn_index,
                    role="assistant_tool_call",
                    tool_calls=[{"id": tc.tool_id, "name": tc.name, "args": tc.arguments} for tc in response.tool_calls],
                )
            )

            # Execute tool calls using original unmodified arguments
            tool_dispatches = []
            for tc in response.tool_calls:
                if self.cancel_event and self.cancel_event.is_set():
                    return AgentLoopResult(
                        status=LoopTerminationStatus.CANCELLED,
                        total_turns=turn_index,
                        transcript=self.transcript,
                        error_message="Agent loop cancelled by user signal during tool execution.",
                    )

                observation, is_error = self._dispatch_tool(tc)
                capped_observation = observation[: self.MAX_OBSERVATION_CHARS]

                # Stuck-loop circuit breaker
                call_sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                if is_error and call_sig == self._last_failing_signature:
                    self._consecutive_failures += 1
                else:
                    self._last_failing_signature = call_sig if is_error else ""
                    self._consecutive_failures = 1 if is_error else 0

                if self._consecutive_failures >= self.MAX_CONSECUTIVE_IDENTICAL_FAILURES:
                    return AgentLoopResult(
                        status=LoopTerminationStatus.STUCK_LOOP_ABORTED,
                        total_turns=turn_index,
                        transcript=self.transcript,
                        error_message=f"Circuit breaker tripped: tool '{tc.name}' failed identically {self._consecutive_failures} times.",
                    )

                tool_dispatches.append((tc, capped_observation, is_error))

            # If the turn failed due to output token truncation on a checklist deliverable:
            if response.finish_reason == "length" and self.deliverables_checklist and next_target:
                self.deliverable_retries[next_target] = self.deliverable_retries.get(next_target, 0) + 1
                if self.deliverable_retries[next_target] > self.max_retries_per_deliverable:
                    return AgentLoopResult(
                        status=LoopTerminationStatus.SAFETY_ABORTED,
                        total_turns=turn_index,
                        transcript=self.transcript,
                        error_message=(
                            f"Model response truncated due to output token ceiling (finish_reason='length') "
                            f"while generating '{next_target}'. "
                            f"Retry limit reached ({self.max_retries_per_deliverable} attempts). "
                            f"Halting to preserve valid disk state."
                        ),
                    )

            # Build compacted assistant tool calls for context history based on actual execution outcome
            tool_call_dicts = [
                {
                    "id": tc.tool_id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(self._compact_tool_arguments_for_context(tc, is_success=(not is_err))),
                    },
                }
                for tc, _, is_err in tool_dispatches
            ]

            # Bounded assistant text content for context history
            asst_content = response.content
            if asst_content and len(asst_content) > 1000:
                asst_content = asst_content[:1000] + "... [truncated historical response text]"

            messages.append({"role": "assistant", "content": asst_content, "tool_calls": tool_call_dicts})

            for tc, capped_obs, _ in tool_dispatches:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.tool_id,
                    "name": tc.name,
                    "content": capped_obs,
                })
                self.transcript.append(
                    TranscriptEntry(
                        turn_index=turn_index,
                        role="tool_result",
                        tool_id=tc.tool_id,
                        tool_name=tc.name,
                        tool_result=capped_obs,
                    )
                )

            # Post-turn checklist evaluation and directive generation for next turn
            if self.deliverables_checklist and turn_index < self.max_turns:
                completed, missing, next_target = self.get_deliverables_status()
                if missing and next_target:
                    retries = self.deliverable_retries.get(next_target, 0)
                    retry_prefix = f"RETRY DIRECTIVE ({retries}/{self.max_retries_per_deliverable}) " if retries > 0 else ""
                    hint = self.get_deliverable_hint(next_target)
                    hint_str = f" {hint}" if hint else ""
                    next_guidance = (
                        f"CHECKLIST: {len(completed)}/{len(self.deliverables_checklist)} completed. "
                        f"{retry_prefix}DIRECTIVE FOR TURN {turn_index + 1}: Write ONLY '{next_target}' in this turn using write_file.{hint_str}"
                    )
                    rejection_details = [obs for _, obs, is_error in tool_dispatches if is_error]
                    if rejection_details:
                        bounded_errors = "; ".join(
                            " ".join(detail.split())[:500] for detail in rejection_details[:2]
                        )
                        next_guidance += f" PREVIOUS WRITE REJECTION: {bounded_errors}"
                    messages.append({"role": "user", "content": next_guidance})
                elif not missing:
                    # All deliverables are present on disk; verify static contract
                    is_space_frac = any("space_fractions" in p for p in self.deliverables_checklist)
                    from src.project_validator import ProjectValidator
                    val_res = (
                        ProjectValidator.validate_project(self.workspace_root, require_full_game=True, require_manifest=False)
                        if is_space_frac
                        else ProjectValidator.validate_generic_project(self.workspace_root)
                    )
                    if val_res.success:
                        messages.append({
                            "role": "user",
                            "content": "CHECKLIST: All required deliverables verified on disk. Provide a brief summary to conclude.",
                        })
                    else:
                        if self._repair_attempts < self.max_repair_attempts:
                            self._repair_attempts += 1
                            err_bullets = "\n".join(f"- {e}" for e in val_res.errors[:4])
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"INTEGRATION VALIDATION FAILURE (Repair Attempt {self._repair_attempts}/{self.max_repair_attempts}):\n"
                                    f"The generated project failed static acceptance contract validation with errors:\n"
                                    f"{err_bullets}\n"
                                    f"DIRECTIVE: Use write_file to repair the affected files. Keep all other verified code intact."
                                ),
                            })
                        elif self.strict_completeness:
                            return AgentLoopResult(
                                status=LoopTerminationStatus.REPAIR_EXHAUSTED_ABORTED,
                                total_turns=turn_index,
                                transcript=self.transcript,
                                error_message=f"Static integration contract validation failed after {self._repair_attempts} repair attempts: {'; '.join(val_res.errors[:3])}",
                            )

        if self.deliverables_checklist:
            completed, missing, _ = self.get_deliverables_status()
            if missing and self.strict_completeness:
                return AgentLoopResult(
                    status=LoopTerminationStatus.INCOMPLETE_DELIVERABLES_ABORTED,
                    total_turns=self.max_turns,
                    transcript=self.transcript,
                    error_message=f"Agent loop reached maximum turn limit of {self.max_turns} with {len(missing)} incomplete deliverable(s): {missing}.",
                )

        return AgentLoopResult(
            status=LoopTerminationStatus.MAX_TURNS_REACHED,
            total_turns=self.max_turns,
            transcript=self.transcript,
            error_message=f"Agent loop reached maximum turn limit of {self.max_turns}.",
        )

    COMPACTED_CONTENT_MARKER: str = "[content omitted after local write]"

    def _compact_message_history_if_needed(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Bounds multi-turn request context without weakening task or audit state.

        The initial system/user pair carries the task contract.  The latest assistant
        exchange, tool observations, and following user directive carry all actionable
        recovery information.  Earlier exchanges describe files whose authoritative
        state is revalidated from disk by the deliverables manifest.
        """
        # Checklist generation is disk-driven. Prior assistant tool calls contain a
        # local compaction marker which providers may imitate verbatim. Once the
        # manifest has revalidated disk state, retain only the original task and the
        # newest actionable user directive/error.
        if (
            self.deliverables_checklist
            and isinstance(self.llm_client, DeepSeekAgentAdapter)
            and len(messages) > 3
        ):
            latest_user = next(
                (messages[index] for index in range(len(messages) - 1, 1, -1)
                 if messages[index].get("role") == "user"),
                None,
            )
            if latest_user is not None:
                return messages[:2] + [latest_user]

        estimate_payload = {"messages": messages, "tools": self.tools_schema}
        estimate = self.budget_controller.calculate_request_token_bound(estimate_payload)
        if estimate <= self.CONTEXT_COMPACTION_TRIGGER or len(messages) <= 4:
            return messages

        prefix = messages[:2]
        last_assistant = next(
            (index for index in range(len(messages) - 1, 1, -1)
             if messages[index].get("role") == "assistant"),
            None,
        )
        if last_assistant is None:
            return messages

        compacted = prefix + messages[last_assistant:]
        compacted_estimate = self.budget_controller.calculate_request_token_bound(
            {"messages": compacted, "tools": self.tools_schema}
        )
        if compacted_estimate <= self.CONTEXT_COMPACTION_TRIGGER:
            return compacted

        # Extremely verbose tool observations are diagnostic, not authoritative.
        # Bound only the retained historical copies; the transcript stays complete.
        bounded: List[Dict[str, Any]] = []
        for message in compacted:
            copied = dict(message)
            if copied.get("role") == "tool" and isinstance(copied.get("content"), str):
                copied["content"] = copied["content"][:600]
            bounded.append(copied)
        return bounded

    @classmethod
    def _compact_tool_arguments_for_context(
        cls, tool_call: ToolCall, is_success: bool = True
    ) -> Dict[str, Any]:
        """Returns bounded tool-call arguments for the next model turn.

        - For completed successful write_file calls, content is replaced with
          the compaction marker to avoid replaying large file source code.
        - For failed write_file calls, content is NOT claimed to be written.
        - For unparsed / malformed tool calls, dead-weight raw truncated strings
          (_raw) are stripped to prevent replaying thousands of garbage chars.
        """
        args = tool_call.arguments
        if not isinstance(args, dict):
            return {"_invalid_arguments": str(args)[:200]}

        # Strip unparsed / truncated _raw payloads from malformed tool calls
        if "_parse_error" in args:
            return {"_parse_error": str(args["_parse_error"])[:300]}

        if tool_call.name == "write_file":
            compact = dict(args)
            content_val = args.get("content")
            if is_success:
                if isinstance(content_val, str):
                    compact["content"] = cls.COMPACTED_CONTENT_MARKER
                return compact
            else:
                # Write failed: do NOT claim content was written locally
                if isinstance(content_val, str) and len(content_val) > 120:
                    compact["content"] = f"[write failed: {len(content_val)} chars omitted]"
                return compact

        return args

    def _dispatch_tool(self, tool_call: ToolCall) -> tuple[str, bool]:
        """Dispatches a validated tool call. Never crashes; returns error strings on failure."""
        name = tool_call.name
        args = tool_call.arguments

        # 1. Unconditionally block run_command if block_run_command is enabled
        if name == "run_command" and self.block_run_command:
            return (
                "Error: 'run_command' execution is permanently disabled for safety. "
                "Host process execution is not permitted.",
                True,
            )

        # 2. Enforce known and permitted tool names
        permitted_names = {t["function"]["name"] for t in self.tools_schema}
        if name not in permitted_names:
            return f"Error: Unknown tool '{name}' (not permitted in this execution mode). Permitted tools are: {sorted(list(permitted_names))}.", True

        # Check for untrusted argument parse errors
        if isinstance(args, dict) and "_parse_error" in args:
            return f"Error: {args['_parse_error']}", True

        # 2. Tool schema and argument validation
        try:
            if name == "write_file":
                if not isinstance(args, dict) or "path" not in args or "content" not in args:
                    return "Error: Tool 'write_file' requires string arguments 'path' and 'content'.", True
                if not isinstance(args["path"], str) or not isinstance(args["content"], str):
                    return "Error: Invalid argument types for 'write_file'. 'path' and 'content' must be strings.", True

                # Compaction placeholder rejection:
                # Reject writes attempting to output the internal compaction marker or placeholder
                raw_content = args["content"]
                stripped_content = raw_content.strip()
                if (
                    stripped_content == self.COMPACTED_CONTENT_MARKER
                    or stripped_content.startswith("[content omitted")
                    or "[content omitted after local write]" in raw_content
                ):
                    return (
                        "Error: Write rejected. Tool argument 'content' contains internal context compaction placeholder. "
                        "You must supply the actual, full file source code to write.",
                        True,
                    )

                # Null byte rejection
                if "\0" in args["path"] or "\0" in args["content"]:
                    return "SecurityError: Null bytes detected in path or content.", True

                # Canonical architecture specifications protection
                target_basename = os.path.basename(args["path"]).strip().lower()
                if target_basename in ("architecture_documentation.md", "architecture_view.md"):
                    return f"SecurityError: Modifying canonical architecture document '{args['path']}' is strictly prohibited.", True

                # Validate workspace path containment (guards against path traversal)
                target_full_path = validate_workspace_path(self.workspace_root, args["path"])

                # Authorized target restriction in bounded synthesis mode
                if self.authorized_target is not None:
                    norm_auth = os.path.normcase(os.path.normpath(self.authorized_target))
                    norm_req = os.path.normcase(os.path.normpath(args["path"]))
                    if norm_req != norm_auth:
                        return (
                            f"SecurityError: Write rejected. In bounded synthesis mode, writes are restricted "
                            f"exclusively to authorized target '{self.authorized_target}', got '{args['path']}'.",
                            True,
                        )

                # Bounded file write limit
                content_bytes = len(args["content"].encode("utf-8"))
                if content_bytes > self.MAX_FILE_WRITE_BYTES:
                    return f"Error: Content size ({content_bytes} bytes) exceeds maximum limit of {self.MAX_FILE_WRITE_BYTES} bytes.", True

                # Explicit overwrite validation
                overwrite = args.get("overwrite", True)
                if not isinstance(overwrite, bool):
                    return "Error: 'overwrite' parameter must be a boolean.", True

                file_existed = os.path.isfile(target_full_path)
                if file_existed and not overwrite:
                    return f"Error: File '{args['path']}' already exists and overwrite is set to False.", True

                # Avoid redundant re-writes of already completed and verified deliverables
                norm_rel = os.path.normpath(args["path"]).replace("\\", "/")
                force_overwrite = args.get("force_overwrite", False)
                if (
                    not force_overwrite
                    and self.deliverables_checklist
                    and norm_rel in self.deliverables_checklist
                    and self.is_deliverable_complete(norm_rel)
                ):
                    _, _, next_missing = self.get_deliverables_status()
                    next_hint = f" Please proceed to next missing deliverable: '{next_missing}'." if next_missing else " All deliverables complete."
                    return (
                        f"Notice: Deliverable '{args['path']}' is already completed and verified on disk. "
                        f"Skipped redundant re-write to preserve verified state.{next_hint}",
                        False,
                    )

                # For Python files, if the file already exists on disk, protect it against overwrite with truncated/broken syntax
                if file_existed and args["path"].endswith(".py"):
                    try:
                        ast.parse(args["content"], filename=args["path"])
                    except SyntaxError as syn_err:
                        return (
                            f"Error: Python syntax error in '{args['path']}': {syn_err}. "
                            f"The content appears truncated or incomplete. File was not written to disk.",
                            True,
                        )

                safe_write_file(self.workspace_root, args["path"], args["content"])
                action = "overwrote" if file_existed else "wrote"
                return f"Successfully {action} {len(args['content'])} characters to '{args['path']}'.", False

            elif name == "read_file":
                if not isinstance(args, dict) or "path" not in args:
                    return "Error: Tool 'read_file' requires string argument 'path'.", True
                if not isinstance(args["path"], str):
                    return "Error: Invalid argument type for 'read_file'. 'path' must be a string.", True

                if "\0" in args["path"]:
                    return "SecurityError: Null bytes detected in path.", True

                content = safe_read_file(self.workspace_root, args["path"])
                return content, False

            elif name == "list_directory":
                subpath = args.get("subpath", ".") if isinstance(args, dict) else "."
                if not isinstance(subpath, str):
                    return "Error: 'subpath' must be a string.", True

                if "\0" in subpath:
                    return "SecurityError: Null bytes detected in path.", True

                norm_target = validate_workspace_path(self.workspace_root, subpath)
                if not os.path.exists(norm_target):
                    return f"Error: Directory '{subpath}' does not exist.", True
                if not os.path.isdir(norm_target):
                    return f"Error: Path '{subpath}' is not a directory.", True

                items = sorted(os.listdir(norm_target))
                return json.dumps(items), False

            elif name == "run_command":
                if self.block_run_command:
                    return (
                        "Error: 'run_command' execution is permanently disabled for safety. "
                        "Host process execution is not permitted.",
                        True,
                    )

                if self.command_executor is None:
                    return "Error: 'run_command' execution disabled (no executor configured).", True

                if not isinstance(args, dict) or "command" not in args:
                    return "Error: Tool 'run_command' requires string argument 'command'.", True
                if not isinstance(args["command"], str):
                    return "Error: Invalid argument type for 'run_command'. 'command' must be a string.", True

                cmd = args["command"]
                result = self.command_executor.execute(cmd, self.workspace_root)
                return_code = result.get("return_code", -1)
                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")
                timed_out = result.get("timed_out", False)

                if timed_out:
                    return f"Command execution timed out after timeout limit. Stderr: {stderr}", True
                if return_code != 0:
                    return f"Command failed with exit code {return_code}. Stderr: {stderr}\nStdout: {stdout}", True

                return f"Command succeeded (exit code 0).\nStdout: {stdout}", False

        except PathSecurityError as err:
            return f"SecurityError: {err}", True
        except FileNotFoundError as err:
            return f"FileNotFoundError: {err}", True
        except Exception as err:
            return f"ToolExecutionError: Unexpected failure executing '{name}': {err}", True

        return "Error: Unhandled execution state.", True

    def _extract_markdown_file_blocks(self, text: Optional[str]) -> List[str]:
        """Extracts and writes files declared in markdown code blocks as a safe fallback.
        
        Supports common patterns:
        - ```python:path/to/file.py ... ```
        - ```python filepath="path/to/file.py" ... ```
        - File: `path/to/file.py` followed by ```python ... ```
        - **path/to/file.py** followed by ```python ... ```
        """
        if not text or not isinstance(text, str):
            return []

        written: List[str] = []
        import re

        # Pattern 1: ```python:path/to/file.py or ```python filepath="path/to/file.py" or ```python filename="path/to/file.py"
        pat1 = re.compile(
            r'```[a-zA-Z0-9_\-\.]*[:\s]+(?:filepath=|filename=)?["`\']?([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_]+)["`\']?\s*\n(.*?)```',
            re.DOTALL,
        )
        for m in pat1.finditer(text):
            rel_p = m.group(1).strip()
            content = m.group(2)
            if self._safe_write_extracted_block(rel_p, content):
                written.append(rel_p)

        # Pattern 2: (?:###|##|#|\*\*|File:)\s*`?([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_]+)`?\s*\n+```[a-zA-Z0-9_\-\.]*\s*\n(.*?)```
        pat2 = re.compile(
            r'(?:###|##|#|\*\*|File:)\s*`?([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_]+)`?[:\s]*\n+```[a-zA-Z0-9_\-\.]*\s*\n(.*?)```',
            re.DOTALL,
        )
        for m in pat2.finditer(text):
            rel_p = m.group(1).strip()
            content = m.group(2)
            if rel_p not in written and self._safe_write_extracted_block(rel_p, content):
                written.append(rel_p)

        return written

    def _safe_write_extracted_block(self, rel_path: str, content: str) -> bool:
        """Validates path security and writes extracted content to workspace."""
        try:
            norm_rel = rel_path.strip().lstrip("/\\")
            if not norm_rel or "\0" in norm_rel:
                return False
            # Canonical architecture specifications protection
            target_basename = os.path.basename(norm_rel).strip().lower()
            if target_basename in ("architecture_documentation.md", "architecture_view.md"):
                return False
            # Compaction placeholder rejection
            stripped_content = content.strip()
            if (
                stripped_content == self.COMPACTED_CONTENT_MARKER
                or stripped_content.startswith("[content omitted")
                or "[content omitted after local write]" in content
            ):
                return False
            # Path containment validation
            validate_workspace_path(self.workspace_root, norm_rel)
            if len(content.encode("utf-8")) > self.MAX_FILE_WRITE_BYTES:
                return False
            safe_write_file(self.workspace_root, norm_rel, content)
            return True
        except Exception:
            return False

