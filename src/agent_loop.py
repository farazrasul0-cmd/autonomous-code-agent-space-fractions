"""Mockable Autonomous Agent Loop Module.

Orchestrates multi-turn ReAct cycles with model-directed tool calls, strict schema
validation, observation truncation, repeated failure guards, budget reservation gates,
and deterministic termination.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from src.budget_controller import BudgetController, BudgetExceededError, BudgetException, ReservationState
from src.path_security import PathSecurityError, safe_read_file, safe_write_file, validate_workspace_path


class LoopTerminationStatus(str, Enum):
    COMPLETED = "COMPLETED"
    MAX_TURNS_REACHED = "MAX_TURNS_REACHED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    STUCK_LOOP_ABORTED = "STUCK_LOOP_ABORTED"
    MALFORMED_RESPONSE_ABORTED = "MALFORMED_RESPONSE_ABORTED"
    CANCELLED = "CANCELLED"
    REFUSAL_ABORTED = "REFUSAL_ABORTED"


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

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        reservation_id: Optional[str] = None,
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

        # Call client with skip_budget_reservation=True (AgentLoop manages reservation)
        response = self.client.send_chat_completion(
            messages=messages,
            tools=tools,
            max_tokens=self.max_output_tokens,
            cancel_event=self.cancel_event,
            skip_budget_reservation=True,
            reservation_id=reservation_id,
        )

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

    MAX_TURNS_DEFAULT = 12
    MAX_OBSERVATION_CHARS = 1500
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

    def run(self, initial_prompt: str, session_id: Optional[str] = None) -> AgentLoopResult:
        """Runs the multi-turn agent loop until completion or safety boundary is reached."""
        effective_session_id = session_id or self.session_id
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": "You are a disciplined coding agent. Write clean code and verify with tests."},
            {"role": "user", "content": initial_prompt},
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
            if effective_session_id:
                reservation_id = f"run-{effective_session_id}-turn-{turn_index}"
            else:
                reservation_id = f"turn-{turn_index}"

            # 1. Budget reservation gate
            prompt_context = json.dumps(messages)
            if self.max_output_tokens > self.budget_controller.MAX_PERMITTED_OUTPUT_TOKENS:
                return AgentLoopResult(
                    status=LoopTerminationStatus.BUDGET_EXCEEDED,
                    total_turns=turn_index - 1,
                    transcript=self.transcript,
                    error_message=(
                        f"Output token limit mismatch: AgentLoop output tokens ({self.max_output_tokens}) "
                        f"exceeds BudgetController limit ({self.budget_controller.MAX_PERMITTED_OUTPUT_TOKENS})."
                    ),
                )
            try:
                self.budget_controller.reserve(
                    reservation_id=reservation_id,
                    prompt_text=prompt_context,
                    max_output_tokens=self.max_output_tokens,
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
                if "reservation_id" in sig.parameters:
                    response = self.llm_client.chat_completion(
                        messages, self.tools_schema, reservation_id=reservation_id
                    )
                else:
                    response = self.llm_client.chat_completion(messages, self.tools_schema)
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

            # If no tool calls, model indicates it has finished
            if not response.tool_calls:
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
            tool_call_dicts = [
                {"id": tc.tool_id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in response.tool_calls
            ]
            messages.append({"role": "assistant", "content": response.content, "tool_calls": tool_call_dicts})
            self.transcript.append(
                TranscriptEntry(
                    turn_index=turn_index,
                    role="assistant_tool_call",
                    tool_calls=[{"id": tc.tool_id, "name": tc.name, "args": tc.arguments} for tc in response.tool_calls],
                )
            )

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

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.tool_id,
                    "name": tc.name,
                    "content": capped_observation,
                })
                self.transcript.append(
                    TranscriptEntry(
                        turn_index=turn_index,
                        role="tool_result",
                        tool_id=tc.tool_id,
                        tool_name=tc.name,
                        tool_result=capped_observation,
                    )
                )

        return AgentLoopResult(
            status=LoopTerminationStatus.MAX_TURNS_REACHED,
            total_turns=self.max_turns,
            transcript=self.transcript,
            error_message=f"Agent loop reached maximum turn limit of {self.max_turns}.",
        )

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

