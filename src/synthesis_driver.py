"""Synthesis Driver Module (Stage F5F).

Provides an offline-testable, budget-governed synthesis driver connecting:
1. SpecificationPreprocessor (Canonical doc ingestion and conflict detection)
2. Task-specific compact prompt construction (bounded < 8,000 tokens)
3. AgentLoop & DeepSeekAgentAdapter (Multi-turn ReAct orchestration)
4. BudgetController (Reservation preflight and usage reconciliation)
5. PathSecurity (Workspace containment, canonical doc protection, size bounding)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from src.agent_loop import (
    AgentLoop,
    AgentLoopResult,
    DeepSeekAgentAdapter,
    LLMClientProtocol,
    LoopTerminationStatus,
    ModelTurnResponse,
    ToolCall,
)
from src.budget_controller import (
    BudgetController,
    BudgetExceededError,
    BudgetException,
    ReservationState,
)
from src.path_security import PathSecurityError, safe_read_file, safe_write_file, validate_workspace_path
from src.spec_preprocessor import (
    ArchitecturalConflict,
    ExplicitRequirement,
    SpecificationExtractionError,
    SpecificationPreprocessor,
    SpecificationPreprocessResult,
)


class CommitState(str, Enum):
    """Lifecycle states of the atomic commit arbitration protocol."""
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class SynthesisStatus(str, Enum):
    COMPLETED = "COMPLETED"
    MAX_TURNS_REACHED = "MAX_TURNS_REACHED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    PREFLIGHT_REJECTED = "PREFLIGHT_REJECTED"


class SynthesisPreflightError(Exception):
    """Raised when a synthesis request fails preflight budget or sizing checks."""
    pass


@dataclass
class SynthesisResult:
    """Structured output contract reporting the result of a synthesis run."""
    artifact_name: str
    target_path: str
    requirement_ids: List[str]
    total_turns: int
    status: SynthesisStatus
    files_written: List[str] = field(default_factory=list)
    files_failed: List[str] = field(default_factory=list)
    blocked_tool_calls: List[str] = field(default_factory=list)
    cumulative_spend: float = 0.0
    permanently_locked_spend: float = 0.0
    active_holds: float = 0.0
    error_message: Optional[str] = None
    final_response: Optional[str] = None
    is_complete: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


class SynthesisDriver:
    """Orchestrates bounded, reviewable code generation from architecture specifications.

    Budget Reservation Mechanics:
        Before each synthesis turn, SynthesisDriver initiates an API request reservation
        via BudgetController.reserve(reservation_id=..., prompt_or_payload=..., max_output_tokens=...).
        The reservation holds funds for the worst-case turn cost combining both bounded input
        tokens at peak cache-miss rate and maximum output tokens at peak generation rate:
            R = (input_token_bound * PEAK_INPUT_CACHE_MISS_RATE)
              + (max_output_tokens * PEAK_OUTPUT_RATE)
        For example, with input_token_bound = 2,180 and max_output_tokens = 1,200:
            R = (2,180 * $0.00000132) + (1,200 * $0.00000396) = $0.00762960.
        When actual token usage is reported by the provider, complete_turn() permanently
        locks the actual cost and releases the remainder of the reservation hold.
    """

    DEFAULT_MAX_TURNS: int = 3
    DEFAULT_INCREMENTAL_BUDGET: float = 0.050000
    HISTORICAL_SPEND_BASELINE: float = 0.000211
    MAX_ALLOWED_INPUT_TOKENS: int = 8000

    SYNTHESIS_TOOLS_SCHEMA = [
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
        }
    ]

    def __init__(
        self,
        workspace_root: str,
        llm_client: Optional[LLMClientProtocol] = None,
        budget_controller: Optional[BudgetController] = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        cancel_event: Optional[threading.Event] = None,
        commit_lock: Optional[threading.Lock] = None,
        block_run_command: bool = True,
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        authorized_target: Optional[str] = None,
        bounded_mode: bool = False,
    ) -> None:
        if not workspace_root:
            raise ValueError("workspace_root must not be empty.")

        self.workspace_root = os.path.realpath(os.path.abspath(workspace_root))
        self.llm_client = llm_client
        self.max_turns = min(max_turns, self.DEFAULT_MAX_TURNS)
        self.cancel_event = cancel_event
        self.commit_lock = commit_lock if commit_lock is not None else threading.Lock()
        self.commit_state = CommitState.IDLE
        self._pre_commit_hook: Optional[Callable[[], None]] = None
        self._in_commit_hook: Optional[Callable[[], None]] = None
        self.block_run_command = block_run_command
        self.tools_schema = tools_schema
        self.authorized_target = authorized_target
        self.bounded_mode = bounded_mode

        # Initialize controller with historical baseline spend and incremental ceiling
        if budget_controller is not None:
            self.budget_controller = budget_controller
        else:
            total_ceiling = self.HISTORICAL_SPEND_BASELINE + self.DEFAULT_INCREMENTAL_BUDGET
            self.budget_controller = BudgetController(ceiling=total_ceiling)
            self.budget_controller.cumulative_spend = self.HISTORICAL_SPEND_BASELINE

    @property
    def is_committing(self) -> bool:
        with self.commit_lock:
            return self.commit_state == CommitState.COMMITTING

    @property
    def is_committed(self) -> bool:
        with self.commit_lock:
            return self.commit_state == CommitState.COMMITTED

    def request_cancellation(self) -> bool:
        """Requests cancellation under the shared commit lock.

        Returns True if cancellation was accepted before the commit boundary.
        Returns False if cancellation was rejected because the run has already
        entered COMMITTING or reached a terminal state (COMMITTED, FAILED, CANCELLED).
        """
        with self.commit_lock:
            if self.commit_state in (
                CommitState.COMMITTING,
                CommitState.COMMITTED,
                CommitState.FAILED,
                CommitState.CANCELLED,
            ):
                return False
            self.commit_state = CommitState.CANCELLED
            if self.cancel_event:
                self.cancel_event.set()
            return True

    @staticmethod
    def validate_fraction_math_interface(file_path: str) -> Tuple[bool, str]:
        """Validates that a generated Python file strictly satisfies the Space Fractions math contract."""
        import ast
        if not os.path.isfile(file_path):
            return False, f"File does not exist: {file_path}"
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
        except Exception as err:
            return False, f"Failed to read file: {err}"

        if not source.strip():
            return False, "File is empty."

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError as err:
            return False, f"Syntax error in generated code: {err}"

        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        if "Fraction" not in classes:
            return False, "Missing required class 'Fraction'."
        if "MathQuestionGenerator" not in classes:
            return False, "Missing required class 'MathQuestionGenerator'."

        fraction_methods = {
            n.name for n in classes["Fraction"].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        required_fraction_methods = {"__init__", "simplify", "add", "subtract", "compare", "equals"}
        missing_frac = required_fraction_methods - fraction_methods
        if missing_frac:
            return False, f"Class 'Fraction' is missing required methods: {sorted(list(missing_frac))}."

        generator_methods = {
            n.name for n in classes["MathQuestionGenerator"].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if "generate" not in generator_methods:
            return False, "Class 'MathQuestionGenerator' is missing required method 'generate'."

        return True, "Interface successfully verified."

    def preprocess(self, doc_path: str, view_path: str) -> SpecificationPreprocessResult:
        """Loads and parses canonical architecture documentation and views."""
        return SpecificationPreprocessor.preprocess(doc_path, view_path)

    def build_synthesis_prompt(
        self,
        spec_result: SpecificationPreprocessResult,
        target_artifact: str = "space_fractions/fraction_math.py",
    ) -> Tuple[str, List[str]]:
        """Constructs a compact, task-specific prompt from selected requirements.

        Guarantees:
        - Includes explicit requirement identifiers and traceability references.
        - Highlights the documented architectural conflict (desktop .exe vs cloud microservices).
        - Stays well under the 8,000 input-token bound by selecting relevant items rather than
          dumping the entire 14.7 KB raw documentation.
        - Instructs the model to use the write_file tool to write to target_artifact.
        """
        # Select requirements relevant to the target
        target_base = os.path.basename(target_artifact).lower()
        if target_base in ("fraction_math.py", "math.py"):
            relevant_reqs = [
                r for r in spec_result.explicit_requirements
                if r.req_id.startswith("FR-") or "fraction" in r.short_text.lower() or "math" in r.short_text.lower()
            ]
            if not relevant_reqs:
                relevant_reqs = spec_result.explicit_requirements[:4]
            artifact_desc = "Core Fraction Arithmetic Module for the Grade 6 Space Fractions Educational Client"
            module_spec = (
                "MODULE INTERFACE SPECIFICATION:\n"
                "- Pure standard library only (math, random, dataclasses, typing). Zero external packages.\n"
                "- Class Fraction(numerator: int, denominator: int = 1):\n"
                "  * Validate numerator & denominator are ints (raise TypeError), denominator != 0 (raise ValueError).\n"
                "  * Normalize negative denominator to numerator and reduce to lowest terms via GCD.\n"
                "  * Methods: add(other), subtract(other), simplify(), compare(other) -> int (-1, 0, 1), equals(other) -> bool.\n"
                "  * Static/class methods: gcd(a, b), lcm(a, b).\n"
                "  * Implement __str__, __repr__, __eq__.\n"
                "- Class/dataclass MathQuestion(prompt: str, options: list[str], correct_idx: int, question_type: str).\n"
                "- Class MathQuestionGenerator:\n"
                "  * generate_question(seed=None, question_type='addition') -> MathQuestion with 4 options.\n\n"
            )
        else:
            relevant_reqs = spec_result.explicit_requirements[:5]
            artifact_desc = f"Specification Deliverable: {target_artifact}"
            module_spec = ""

        req_ids = [r.req_id for r in relevant_reqs]

        # Format requirement bullets
        req_bullets = "\n".join(
            f"- [{r.req_id}] ({r.category}): {r.short_text} (Source: {r.source_section})"
            for r in relevant_reqs
        )

        # Highlight documented conflict
        conflict_notice = ""
        if spec_result.recorded_conflicts:
            c = spec_result.recorded_conflicts[0]
            conflict_notice = (
                f"\nIMPORTANT ARCHITECTURAL CONFLICT NOTICE [{c.conflict_id}]:\n"
                f"- Section B mandates a Windows desktop client deliverable.\n"
                f"- Section L specifies supplementary cloud microservices (FastAPI/Redis/PostgreSQL).\n"
                f"- DIRECTIVE: Implement code ONLY for the local standalone desktop client. "
                f"Do NOT generate or require external cloud microservice dependencies.\n"
            )

        prompt = (
            f"You are the Space Fractions Code Agent. Your task is to generate a single, reviewable "
            f"implementation module for the project.\n\n"
            f"TARGET ARTIFACT: {target_artifact}\n"
            f"DESCRIPTION: {artifact_desc}\n\n"
            f"RELEVANT REQUIREMENTS TRACEABILITY:\n"
            f"{req_bullets}\n"
            f"{conflict_notice}\n"
            f"{module_spec}"
            f"IMPLEMENTATION INSTRUCTIONS:\n"
            f"1. All necessary architecture and interface requirements are provided in this prompt.\n"
            f"2. Generate pure Python 3 code with zero external runtime dependencies.\n"
            f"3. Immediately invoke the 'write_file' tool to write the complete implementation to '{target_artifact}'.\n"
            f"4. Do not attempt to run shell commands or execute code.\n"
            f"5. Once written, output a brief completion message summarizing the implemented functions.\n"
        )

        return prompt, req_ids

    def run_synthesis(
        self,
        doc_path: str,
        view_path: str,
        target_artifact: str = "space_fractions/fraction_math.py",
        custom_prompt: Optional[str] = None,
        bounded_mode: Optional[bool] = None,
        destination_dir: Optional[str] = None,
    ) -> SynthesisResult:
        """Executes bounded, offline-testable synthesis using AgentLoop with staging and validation."""
        with self.commit_lock:
            self.commit_state = CommitState.RUNNING

        # 1. Preprocess canonical architecture documents
        try:
            spec_result = self.preprocess(doc_path, view_path)
        except Exception as err:
            with self.commit_lock:
                self.commit_state = CommitState.FAILED
            return SynthesisResult(
                artifact_name=os.path.basename(target_artifact),
                target_path=target_artifact,
                requirement_ids=[],
                total_turns=0,
                status=SynthesisStatus.FAILED,
                cumulative_spend=self.budget_controller.cumulative_spend,
                permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                active_holds=self.budget_controller.get_active_holds(),
                error_message=f"Specification preprocessing failed: {err}",
                is_complete=False,
                details={"commit_state": self.commit_state.value},
            )

        # 2. Path security check on target_artifact relative to workspace_root
        try:
            target_artifact_norm = os.path.normpath(target_artifact)
            if (
                os.path.isabs(target_artifact_norm)
                or target_artifact.startswith(("/", "\\"))
                or target_artifact_norm.startswith("..")
            ):
                raise PathSecurityError(f"Target artifact path '{target_artifact}' escapes workspace root.")
        except Exception as err:
            with self.commit_lock:
                self.commit_state = CommitState.FAILED
            return SynthesisResult(
                artifact_name=os.path.basename(target_artifact),
                target_path=target_artifact,
                requirement_ids=[],
                total_turns=0,
                status=SynthesisStatus.FAILED,
                cumulative_spend=self.budget_controller.cumulative_spend,
                permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                active_holds=self.budget_controller.get_active_holds(),
                error_message=f"Path security violation in target_artifact: {err}",
                is_complete=False,
                details={"commit_state": self.commit_state.value},
            )

        # 3. Build or use task-specific prompt
        if custom_prompt:
            prompt = custom_prompt
            req_ids = [r.req_id for r in spec_result.explicit_requirements[:3]]
        else:
            prompt, req_ids = self.build_synthesis_prompt(spec_result, target_artifact)

        # 4. Preflight Token-Bound and Payload Sizing Check
        token_bound = self.budget_controller.calculate_token_bound(prompt, include_framing=True)
        if token_bound > self.MAX_ALLOWED_INPUT_TOKENS:
            with self.commit_lock:
                self.commit_state = CommitState.FAILED
            return SynthesisResult(
                artifact_name=os.path.basename(target_artifact),
                target_path=target_artifact,
                requirement_ids=req_ids,
                total_turns=0,
                status=SynthesisStatus.PREFLIGHT_REJECTED,
                cumulative_spend=self.budget_controller.cumulative_spend,
                permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                active_holds=self.budget_controller.get_active_holds(),
                error_message=(
                    f"Prompt token bound ({token_bound}) exceeds maximum input bound of "
                    f"{self.MAX_ALLOWED_INPUT_TOKENS} tokens. Aborted before model dispatch."
                ),
                is_complete=False,
                details={"token_bound": token_bound, "commit_state": self.commit_state.value},
            )

        # 5. Check that an LLM client is configured
        if self.llm_client is None:
            with self.commit_lock:
                self.commit_state = CommitState.FAILED
            return SynthesisResult(
                artifact_name=os.path.basename(target_artifact),
                target_path=target_artifact,
                requirement_ids=req_ids,
                total_turns=0,
                status=SynthesisStatus.FAILED,
                cumulative_spend=self.budget_controller.cumulative_spend,
                permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                active_holds=self.budget_controller.get_active_holds(),
                error_message="No LLM client configured on SynthesisDriver.",
                is_complete=False,
                details={"commit_state": self.commit_state.value},
            )

        # 6. Initialize isolated, run-specific staging workspace and destination directory
        session_id = uuid.uuid4().hex
        staging_dir = os.path.join(self.workspace_root, f".staging_{session_id}")
        if os.path.exists(staging_dir):
            with self.commit_lock:
                self.commit_state = CommitState.FAILED
            raise SynthesisPreflightError(f"Staging collision: '{staging_dir}' already exists.")
        os.makedirs(staging_dir, exist_ok=False)

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        if destination_dir:
            target_run_dir = os.path.abspath(destination_dir)
        else:
            target_run_dir = os.path.join(
                self.workspace_root, f"synthesis_run_{timestamp_str}_{session_id[:8]}"
            )

        if os.path.exists(target_run_dir):
            with self.commit_lock:
                self.commit_state = CommitState.FAILED
            return SynthesisResult(
                artifact_name=os.path.basename(target_artifact),
                target_path=target_artifact,
                requirement_ids=req_ids,
                total_turns=0,
                status=SynthesisStatus.FAILED,
                cumulative_spend=self.budget_controller.cumulative_spend,
                permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                active_holds=self.budget_controller.get_active_holds(),
                error_message=f"Destination directory collision: '{target_run_dir}' already exists.",
                is_complete=False,
                details={"staging_dir": staging_dir, "commit_state": self.commit_state.value},
            )

        # 7. Execute AgentLoop with workspace constrained exclusively to staging_dir
        is_bounded = self.bounded_mode if bounded_mode is None else bounded_mode
        if is_bounded:
            effective_schema = self.tools_schema if self.tools_schema is not None else self.SYNTHESIS_TOOLS_SCHEMA
            effective_target = target_artifact
        else:
            effective_schema = self.tools_schema
            effective_target = self.authorized_target

        agent = AgentLoop(
            workspace_root=staging_dir,
            llm_client=self.llm_client,
            budget_controller=self.budget_controller,
            max_turns=self.max_turns,
            cancel_event=self.cancel_event,
            block_run_command=self.block_run_command,
            session_id=session_id,
            tools_schema=effective_schema,
            authorized_target=effective_target,
        )

        loop_result: AgentLoopResult = agent.run(prompt)

        # 8. Analyze Transcript and Extract Output Metrics
        files_written: List[str] = []
        files_failed: List[str] = []
        blocked_tools: List[str] = []

        for entry in loop_result.transcript:
            if entry.role == "tool_result" and entry.tool_name:
                res_text = entry.tool_result or ""
                if entry.tool_name == "write_file":
                    if "Successfully wrote" in res_text or "Successfully overwrote" in res_text:
                        if "'" in res_text:
                            path_extracted = res_text.split("'")[1]
                            files_written.append(path_extracted)
                        else:
                            files_written.append(target_artifact)
                    else:
                        files_failed.append(res_text)
                        if "SecurityError" in res_text:
                            blocked_tools.append(f"write_file: {res_text}")
                elif entry.tool_name == "run_command":
                    blocked_tools.append(f"run_command: {res_text}")
                elif "SecurityError" in res_text or "Error:" in res_text:
                    blocked_tools.append(f"{entry.tool_name}: {res_text}")

        # Map LoopTerminationStatus to SynthesisStatus
        status_map = {
            LoopTerminationStatus.COMPLETED: SynthesisStatus.COMPLETED,
            LoopTerminationStatus.MAX_TURNS_REACHED: SynthesisStatus.MAX_TURNS_REACHED,
            LoopTerminationStatus.BUDGET_EXCEEDED: SynthesisStatus.BUDGET_EXCEEDED,
            LoopTerminationStatus.CANCELLED: SynthesisStatus.CANCELLED,
            LoopTerminationStatus.MALFORMED_RESPONSE_ABORTED: SynthesisStatus.FAILED,
            LoopTerminationStatus.STUCK_LOOP_ABORTED: SynthesisStatus.FAILED,
            LoopTerminationStatus.REFUSAL_ABORTED: SynthesisStatus.FAILED,
        }
        synth_status = status_map.get(loop_result.status, SynthesisStatus.FAILED)

        # Non-completed runs: preserve staging, do not promote
        if synth_status != SynthesisStatus.COMPLETED:
            with self.commit_lock:
                if synth_status == SynthesisStatus.CANCELLED:
                    self.commit_state = CommitState.CANCELLED
                else:
                    self.commit_state = CommitState.FAILED
            return SynthesisResult(
                artifact_name=os.path.basename(target_artifact),
                target_path=target_artifact,
                requirement_ids=req_ids,
                total_turns=loop_result.total_turns,
                status=synth_status,
                files_written=files_written,
                files_failed=files_failed,
                blocked_tool_calls=blocked_tools,
                cumulative_spend=self.budget_controller.cumulative_spend,
                permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                active_holds=self.budget_controller.get_active_holds(),
                error_message=loop_result.error_message,
                final_response=loop_result.final_response,
                is_complete=False,
                details={
                    "loop_status": loop_result.status.value,
                    "token_bound": token_bound,
                    "workspace_root": self.workspace_root,
                    "staging_dir": staging_dir,
                    "session_id": session_id,
                    "commit_state": self.commit_state.value,
                },
            )

        # 9. Verify that expected target file was generated in staging
        staged_target_file = os.path.join(staging_dir, os.path.normpath(target_artifact))
        target_was_written = False
        try:
            target_rel = os.path.relpath(staged_target_file, staging_dir)
            for recorded_path in files_written:
                try:
                    val_rec = validate_workspace_path(staging_dir, recorded_path)
                    rec_rel = os.path.relpath(val_rec, staging_dir)
                    if os.path.normcase(rec_rel) == os.path.normcase(target_rel):
                        target_was_written = True
                        break
                except (PathSecurityError, ValueError):
                    continue
        except (PathSecurityError, ValueError):
            target_was_written = False

        if not os.path.isfile(staged_target_file) or not target_was_written:
            with self.commit_lock:
                self.commit_state = CommitState.FAILED
            return SynthesisResult(
                artifact_name=os.path.basename(target_artifact),
                target_path=target_artifact,
                requirement_ids=req_ids,
                total_turns=loop_result.total_turns,
                status=synth_status,
                files_written=files_written,
                files_failed=files_failed,
                blocked_tool_calls=blocked_tools,
                cumulative_spend=self.budget_controller.cumulative_spend,
                permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                active_holds=self.budget_controller.get_active_holds(),
                error_message=f"Target artifact '{target_artifact}' was not generated in staging workspace.",
                is_complete=False,
                details={
                    "staging_dir": staging_dir,
                    "session_id": session_id,
                    "commit_state": self.commit_state.value,
                },
            )

        # 10. Perform Interface Validation Gate
        target_base = os.path.basename(target_artifact).lower()
        if target_base in ("fraction_math.py", "math.py"):
            is_valid, val_msg = self.validate_fraction_math_interface(staged_target_file)
            if not is_valid:
                with self.commit_lock:
                    self.commit_state = CommitState.FAILED
                # Validation failure: preserve staging, do not promote, return failure
                return SynthesisResult(
                    artifact_name=os.path.basename(target_artifact),
                    target_path=target_artifact,
                    requirement_ids=req_ids,
                    total_turns=loop_result.total_turns,
                    status=SynthesisStatus.FAILED,
                    files_written=files_written,
                    files_failed=files_failed,
                    blocked_tool_calls=blocked_tools,
                    cumulative_spend=self.budget_controller.cumulative_spend,
                    permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                    active_holds=self.budget_controller.get_active_holds(),
                    error_message=f"Interface validation failed for '{target_artifact}': {val_msg}",
                    final_response=loop_result.final_response,
                    is_complete=False,
                    details={
                        "staging_dir": staging_dir,
                        "validation_error": val_msg,
                        "session_id": session_id,
                        "commit_state": self.commit_state.value,
                    },
                )

        # 11. Create Synthesis Manifest inside Staging (Prepared before commitment)
        manifest_data = {
            "session_id": session_id,
            "target_artifact": target_artifact,
            "timestamp": datetime.now().isoformat(),
            "status": "COMPLETED",
            "validation": "PASSED",
            "files_written": files_written,
            "total_turns": loop_result.total_turns,
        }
        manifest_path = os.path.join(staging_dir, "synthesis_manifest.json")
        try:
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest_data, mf, indent=2)
        except Exception as mf_err:
            with self.commit_lock:
                self.commit_state = CommitState.FAILED
            # Manifest creation failure: preserve staging, do not promote
            return SynthesisResult(
                artifact_name=os.path.basename(target_artifact),
                target_path=target_artifact,
                requirement_ids=req_ids,
                total_turns=loop_result.total_turns,
                status=SynthesisStatus.FAILED,
                files_written=files_written,
                files_failed=files_failed,
                blocked_tool_calls=blocked_tools,
                cumulative_spend=self.budget_controller.cumulative_spend,
                permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                active_holds=self.budget_controller.get_active_holds(),
                error_message=f"Failed to write synthesis manifest: {mf_err}",
                is_complete=False,
                details={
                    "staging_dir": staging_dir,
                    "manifest_error": str(mf_err),
                    "session_id": session_id,
                    "commit_state": self.commit_state.value,
                },
            )

        # Optional pre-commit hook for deterministic concurrency regression tests
        if self._pre_commit_hook:
            self._pre_commit_hook()

        # 12. Synchronized Atomic Commit Boundary and Directory Promotion
        with self.commit_lock:
            # Atomic check of cancellation under shared lock
            if self.commit_state == CommitState.CANCELLED or (self.cancel_event and self.cancel_event.is_set()):
                self.commit_state = CommitState.CANCELLED
                return SynthesisResult(
                    artifact_name=os.path.basename(target_artifact),
                    target_path=target_artifact,
                    requirement_ids=req_ids,
                    total_turns=loop_result.total_turns,
                    status=SynthesisStatus.CANCELLED,
                    files_written=files_written,
                    files_failed=files_failed,
                    blocked_tool_calls=blocked_tools,
                    cumulative_spend=self.budget_controller.cumulative_spend,
                    permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                    active_holds=self.budget_controller.get_active_holds(),
                    error_message="Synthesis cancelled before directory promotion.",
                    is_complete=False,
                    details={
                        "staging_dir": staging_dir,
                        "session_id": session_id,
                        "commit_state": self.commit_state.value,
                    },
                )

            # Atomically claim COMMITTING state
            self.commit_state = CommitState.COMMITTING

            if self._in_commit_hook:
                self._in_commit_hook()

            # Execute atomic directory promotion
            try:
                if os.path.exists(target_run_dir):
                    raise FileExistsError(f"Destination directory already exists: '{target_run_dir}'")
                os.rename(staging_dir, target_run_dir)
                self.commit_state = CommitState.COMMITTED
            except OSError as prom_err:
                self.commit_state = CommitState.FAILED
                return SynthesisResult(
                    artifact_name=os.path.basename(target_artifact),
                    target_path=target_artifact,
                    requirement_ids=req_ids,
                    total_turns=loop_result.total_turns,
                    status=SynthesisStatus.FAILED,
                    files_written=files_written,
                    files_failed=files_failed,
                    blocked_tool_calls=blocked_tools,
                    cumulative_spend=self.budget_controller.cumulative_spend,
                    permanently_locked_spend=self.budget_controller.permanently_locked_spend,
                    active_holds=self.budget_controller.get_active_holds(),
                    error_message=f"Directory promotion failed: {prom_err}",
                    is_complete=False,
                    details={
                        "staging_dir": staging_dir,
                        "target_run_dir": target_run_dir,
                        "promotion_error": str(prom_err),
                        "session_id": session_id,
                        "commit_state": self.commit_state.value,
                    },
                )

        # 13. Report Successful Completion with Promoted Artifact Path
        promoted_target_path = os.path.join(target_run_dir, os.path.normpath(target_artifact))
        return SynthesisResult(
            artifact_name=os.path.basename(target_artifact),
            target_path=promoted_target_path,
            requirement_ids=req_ids,
            total_turns=loop_result.total_turns,
            status=SynthesisStatus.COMPLETED,
            files_written=files_written,
            files_failed=files_failed,
            blocked_tool_calls=blocked_tools,
            cumulative_spend=self.budget_controller.cumulative_spend,
            permanently_locked_spend=self.budget_controller.permanently_locked_spend,
            active_holds=self.budget_controller.get_active_holds(),
            error_message=None,
            final_response=loop_result.final_response,
            is_complete=True,
            details={
                "loop_status": loop_result.status.value,
                "token_bound": token_bound,
                "workspace_root": self.workspace_root,
                "run_dir": target_run_dir,
                "manifest_path": os.path.join(target_run_dir, "synthesis_manifest.json"),
                "session_id": session_id,
                "commit_state": self.commit_state.value,
            },
        )
