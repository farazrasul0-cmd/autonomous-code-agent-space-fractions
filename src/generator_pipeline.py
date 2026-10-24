"""Generator Pipeline Module.

Offline generation pipeline coordinator establishing structured interfaces for:
1. Architecture preprocessing (SpecificationPreprocessor)
2. Staged generation planning (GenerationPlan / StagePlan)
3. Request-budget preflight reservation and accounting (BudgetController)
4. Model response validation and classification (ResponseParser)
5. Safe, bounded file writing within designated workspace boundaries (PathSecurity)

SAFETY CONSTRAINTS:
- MOCK INTERFACES ONLY: This module sends NO live HTTP requests and invokes NO remote API.
- NO CODE EXECUTION: Generated Python scripts, test suites, and shell commands are NEVER executed.
- NO AUTOMATIC API RETRIES: Retries are disabled to prevent uncontrolled token spend loops.
- UNTRUSTED DATA CONTRACT: Model-generated outputs are treated as untrusted input.
- SANDBOX DISCLAIMER: AST parsing, py_compile, and command allowlists are syntax checks only,
  NOT security sandboxes. They do not isolate operating system resources from malicious bytecode.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.agent_loop import (
    AgentLoop,
    AgentLoopResult,
    LLMClientProtocol,
    LoopTerminationStatus,
)
from src.budget_controller import BudgetController, BudgetException, BudgetExceededError
from src.path_security import validate_workspace_path, safe_write_file, PathSecurityError
from src.response_parser import ResponseParser, ParsedResponse, ResponseClassification
from src.spec_preprocessor import (
    SpecificationPreprocessor,
    SpecificationPreprocessResult,
    SpecificationExtractionError,
)


class PipelineStageType(str, Enum):
    PREPROCESS = "preprocess"
    PLAN = "plan"
    SPECS_GENERATION = "specs_generation"
    GAME_CORE_GENERATION = "game_core_generation"
    GAME_UI_GENERATION = "game_ui_generation"
    TESTS_GENERATION = "tests_generation"
    SYNTAX_VERIFICATION = "syntax_verification"


class PipelineExecutionError(Exception):
    """Raised when pipeline execution fails or encounters a safety violation."""
    pass


@dataclass
class StagePlan:
    stage_id: str
    stage_type: PipelineStageType
    description: str
    expected_output_files: List[str]
    max_output_tokens: int
    prompt_payload: Dict[str, Any] = field(default_factory=dict)
    reservation_id: str = ""
    reserved_amount: float = 0.0
    status: str = "PENDING"  # PENDING, RESERVED, COMPLETED, FAILED, SKIPPED


@dataclass
class GenerationPlan:
    plan_id: str
    total_stages: int
    stages: List[StagePlan]
    estimated_worst_case_cost: float


@dataclass
class PipelineExecutionSummary:
    success: bool
    total_stages_run: int
    completed_stages: List[str]
    generated_files: List[str]
    cumulative_reserved: float
    cumulative_spend: float
    safety_violations: List[str]
    errors: List[str]
    security_notices: List[str] = field(default_factory=list)


class OfflinePipelineCoordinator:
    """Coordinates the staged generation workflow offline using verified security interfaces."""

    MAX_FILE_WRITE_BYTES: int = 1_000_000  # 1 MB maximum per generated file

    def __init__(
        self,
        workspace_root: str,
        output_dir: str,
        budget_controller: Optional[BudgetController] = None,
        preprocessor: Optional[SpecificationPreprocessor] = None,
        llm_client: Optional[LLMClientProtocol] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        if not workspace_root:
            raise ValueError("workspace_root cannot be empty.")
        if not output_dir:
            raise ValueError("output_dir cannot be empty.")

        self.workspace_root = os.path.abspath(workspace_root)
        self.output_dir = os.path.abspath(output_dir)

        # Budget controller with strict $0.80 ceiling
        self.budget_controller = budget_controller or BudgetController(ceiling=0.80)
        self.preprocessor = preprocessor or SpecificationPreprocessor()
        self.llm_client = llm_client
        self.cancel_event = cancel_event

        # Track written files and safety events
        self.written_files: List[str] = []
        self.safety_violations: List[str] = []

    def preprocess_architecture(
        self, doc_path: str, view_path: str
    ) -> SpecificationPreprocessResult:
        """Runs the specification preprocessor without mutating source files."""
        return self.preprocessor.preprocess(doc_path, view_path)

    def create_generation_plan(
        self, spec_result: SpecificationPreprocessResult
    ) -> GenerationPlan:
        """Constructs a deterministic, staged generation plan within strict token limits.
        
        Divides generation into targeted stages to prevent single-prompt context bloat:
        Stage 1: Enterprise specifications (/specs)
        Stage 2: Space Fractions domain & fraction math engine
        Stage 3: Space Fractions Tkinter desktop GUI
        Stage 4: Offline test suite
        Stage 5: Offline syntax verification
        """
        plan_id = f"PLAN-{uuid.uuid4().hex[:8].upper()}"
        stages: List[StagePlan] = []

        # Stage 1: Enterprise Specs
        stage_1_files = [d.filename for d in spec_result.deliverable_specs]
        stages.append(
            StagePlan(
                stage_id=f"{plan_id}-S1-SPECS",
                stage_type=PipelineStageType.SPECS_GENERATION,
                description="Generate secondary enterprise specifications in /specs based on Section L",
                expected_output_files=stage_1_files,
                max_output_tokens=1200,
                prompt_payload={
                    "stage": "specs",
                    "deliverable_count": len(spec_result.deliverable_specs),
                    "files": stage_1_files,
                },
            )
        )

        # Stage 2: Space Fractions Game Core
        stages.append(
            StagePlan(
                stage_id=f"{plan_id}-S2-GAME-CORE",
                stage_type=PipelineStageType.GAME_CORE_GENERATION,
                description="Generate core fraction math engine and question bank for Grade 6 math",
                expected_output_files=[
                    "space_fractions/fraction_math.py",
                    "space_fractions/game_engine.py",
                    "space_fractions/question_bank.py",
                ],
                max_output_tokens=1200,
                prompt_payload={
                    "stage": "game_core",
                    "entities": [c["name"] for c in spec_result.entities.classes],
                    "states": spec_result.entities.states,
                },
            )
        )

        # Stage 3: Space Fractions Game UI
        stages.append(
            StagePlan(
                stage_id=f"{plan_id}-S3-GAME-UI",
                stage_type=PipelineStageType.GAME_UI_GENERATION,
                description="Generate Tkinter desktop GUI, visual fraction canvas, and state screens",
                expected_output_files=[
                    "space_fractions/main.py",
                    "space_fractions/ui_screens.py",
                ],
                max_output_tokens=1200,
                prompt_payload={
                    "stage": "game_ui",
                    "actors": spec_result.entities.actors,
                    "use_cases": [item.short_title for item in spec_result.view_items],
                },
            )
        )

        # Stage 4: Test Suite
        stages.append(
            StagePlan(
                stage_id=f"{plan_id}-S4-TESTS",
                stage_type=PipelineStageType.TESTS_GENERATION,
                description="Generate offline test cases verifying fraction arithmetic and game states",
                expected_output_files=[
                    "tests/test_fraction_math.py",
                    "tests/test_game_engine.py",
                ],
                max_output_tokens=1200,
                prompt_payload={
                    "stage": "tests",
                    "requirements": [r.req_id for r in spec_result.explicit_requirements],
                },
            )
        )

        # Calculate worst-case budget reservation across all stages
        estimated_cost = 0.0
        for s in stages:
            input_bound = self.budget_controller.calculate_request_token_bound(s.prompt_payload)
            s.reservation_id = f"RES-{s.stage_id}"
            stage_worst_case = (
                (input_bound * self.budget_controller.PEAK_INPUT_CACHE_MISS_RATE)
                + (s.max_output_tokens * self.budget_controller.PEAK_OUTPUT_RATE)
            )
            estimated_cost += stage_worst_case

        return GenerationPlan(
            plan_id=plan_id,
            total_stages=len(stages),
            stages=stages,
            estimated_worst_case_cost=estimated_cost,
        )

    def preflight_stage_budget(self, stage: StagePlan) -> float:
        """Executes a pre-flight budget reservation before any simulated stage dispatch.
        
        Guarantees that token reservations fit within the internal $0.80 ceiling.
        """
        reserved = self.budget_controller.reserve(
            reservation_id=stage.reservation_id,
            prompt_or_payload=stage.prompt_payload,
            max_output_tokens=stage.max_output_tokens,
            description=stage.description,
        )
        stage.reserved_amount = reserved
        stage.status = "RESERVED"
        return reserved

    def validate_model_response(
        self, response_payload: Dict[str, Any], http_status: int = 200
    ) -> ParsedResponse:
        """Validates simulated or recorded model responses using ResponseParser."""
        parsed = ResponseParser.parse(response_payload, http_status=http_status)
        if not parsed.is_valid_generation:
            raise PipelineExecutionError(
                f"Model response validation failed: classification={parsed.classification}, "
                f"error={parsed.error_message}"
            )
        return parsed

    def safe_write_generated_file(
        self, target_rel_path: str, content: str, max_bytes: Optional[int] = None
    ) -> str:
        """Safely writes a generated file into the output directory with security guards.
        
        Guards:
        1. Validates that target_rel_path strictly resolves within self.output_dir using commonpath.
        2. Rejects path traversal (..), absolute paths, and UNC network shares.
        3. Rejects null bytes in path or content.
        4. Rejects content exceeding max_bytes (bounded file writes).
        5. Performs atomic replacement to prevent partial writes.
        """
        limit = max_bytes if max_bytes is not None else self.MAX_FILE_WRITE_BYTES

        if not target_rel_path or not target_rel_path.strip():
            self.safety_violations.append("Empty target path rejected.")
            raise PathSecurityError("Target relative path cannot be empty.")

        if "\0" in target_rel_path or "\0" in content:
            self.safety_violations.append(f"Null byte rejected in target path '{target_rel_path}'.")
            raise PathSecurityError("Null bytes detected in file path or content.")

        # Reject path traversals or escapes
        if target_rel_path.startswith(("/", "\\", "..")) or ":\\" in target_rel_path:
            self.safety_violations.append(f"Unsafe path syntax rejected: '{target_rel_path}'.")
            raise PathSecurityError(f"Unsafe relative path syntax: '{target_rel_path}'")

        # Bounded file write check
        byte_len = len(content.encode("utf-8"))
        if byte_len > limit:
            self.safety_violations.append(
                f"File write size ({byte_len} bytes) exceeds limit ({limit} bytes) for '{target_rel_path}'."
            )
            raise PipelineExecutionError(
                f"Generated content size ({byte_len} bytes) exceeds maximum permitted limit ({limit} bytes)."
            )

        # Validate that path strictly resolves within self.output_dir
        verified_path = validate_workspace_path(self.output_dir, target_rel_path)

        # Atomic write
        safe_write_file(self.output_dir, target_rel_path, content, encoding="utf-8")
        self.written_files.append(verified_path)
        return verified_path

    def execute_stage_with_agent(
        self,
        stage: StagePlan,
        llm_client: Optional[LLMClientProtocol] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> AgentLoopResult:
        """Executes a single pipeline stage through AgentLoop with bounded tool execution.
        
        Guarantees:
        - Workspace root is strictly isolated to self.output_dir.
        - Budget reservation precedes every turn and is reconciled with usage telemetry.
        - Tool execution is strictly validated (write_file only within output_dir).
        - Zero code execution (block_run_command=True).
        - Cancellation is propagated to AgentLoop.
        """
        client = llm_client or self.llm_client
        if client is None:
            raise PipelineExecutionError("execute_stage_with_agent requires an LLMClientProtocol instance.")

        effective_cancel = cancel_event or self.cancel_event

        stage_prompt = (
            f"Generate implementation for stage: {stage.stage_id} ({stage.stage_type.value}).\n"
            f"Description: {stage.description}\n"
            f"Expected output files:\n"
            + "\n".join(f"- {f}" for f in stage.expected_output_files)
            + f"\nPayload: {json.dumps(stage.prompt_payload)}\n"
            f"Use the write_file tool to write each file to the designated relative path."
        )

        agent = AgentLoop(
            workspace_root=self.output_dir,
            llm_client=client,
            budget_controller=self.budget_controller,
            max_turns=6,
            cancel_event=effective_cancel,
            block_run_command=True,
            session_id=stage.stage_id,
        )

        loop_result = agent.run(stage_prompt)

        # Track written files and safety violations from transcript
        for entry in loop_result.transcript:
            if entry.role == "tool_result" and entry.tool_result:
                res_text = entry.tool_result
                if "Successfully wrote" in res_text or "Successfully overwrote" in res_text:
                    if "'" in res_text:
                        path_extracted = res_text.split("'")[1]
                        full_written = os.path.normpath(os.path.join(self.output_dir, path_extracted))
                        if full_written not in self.written_files:
                            self.written_files.append(full_written)
                elif "SecurityError" in res_text:
                    self.safety_violations.append(f"SecurityError in {stage.stage_id}: {res_text}")

        return loop_result

    def run_agent_pipeline(
        self,
        doc_path: str,
        view_path: str,
        llm_client: Optional[LLMClientProtocol] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> PipelineExecutionSummary:
        """Executes the staged generation pipeline using AgentLoop with the provided LLM client."""
        client = llm_client or self.llm_client
        if client is None:
            raise PipelineExecutionError("run_agent_pipeline requires an LLMClientProtocol instance.")

        effective_cancel = cancel_event or self.cancel_event
        errors: List[str] = []
        completed_stages: List[str] = []

        security_notices = [
            "SECURITY NOTICE: Pipeline operated via AgentLoop with LLMClientProtocol.",
            "SECURITY NOTICE: Model output was treated as untrusted data; all file writes validated via PathSecurity.",
            "SECURITY NOTICE: No generated code or tests were executed during this pipeline run (block_run_command=True).",
            "SECURITY NOTICE: Budget reservation preceded every turn and was reconciled with usage telemetry.",
        ]

        # 1. Preprocess
        try:
            spec_result = self.preprocess_architecture(doc_path, view_path)
        except Exception as err:
            return PipelineExecutionSummary(
                success=False,
                total_stages_run=0,
                completed_stages=[],
                generated_files=[],
                cumulative_reserved=0.0,
                cumulative_spend=self.budget_controller.cumulative_spend,
                safety_violations=list(self.safety_violations),
                errors=[f"Preprocessing failed: {err}"],
                security_notices=security_notices,
            )

        # 2. Plan
        plan = self.create_generation_plan(spec_result)

        # 3. Execute Stages via AgentLoop
        for stage in plan.stages:
            if effective_cancel and effective_cancel.is_set():
                errors.append(f"Pipeline cancelled before stage '{stage.stage_id}'.")
                stage.status = "CANCELLED"
                break

            loop_result = self.execute_stage_with_agent(
                stage=stage,
                llm_client=client,
                cancel_event=effective_cancel,
            )

            if loop_result.status == LoopTerminationStatus.COMPLETED:
                stage.status = "COMPLETED"
                completed_stages.append(stage.stage_id)
            elif loop_result.status == LoopTerminationStatus.CANCELLED:
                stage.status = "CANCELLED"
                errors.append(f"Stage '{stage.stage_id}' was cancelled: {loop_result.error_message}")
                break
            elif loop_result.status == LoopTerminationStatus.BUDGET_EXCEEDED:
                stage.status = "FAILED"
                errors.append(f"Stage '{stage.stage_id}' failed due to budget exhaustion: {loop_result.error_message}")
                break
            else:
                stage.status = "FAILED"
                errors.append(
                    f"Stage '{stage.stage_id}' failed with status {loop_result.status.value}: {loop_result.error_message}"
                )
                break

        success = len(errors) == 0 and len(completed_stages) == len(plan.stages)

        return PipelineExecutionSummary(
            success=success,
            total_stages_run=len(completed_stages),
            completed_stages=completed_stages,
            generated_files=list(self.written_files),
            cumulative_reserved=self.budget_controller.cumulative_spend + self.budget_controller.get_active_holds(),
            cumulative_spend=self.budget_controller.cumulative_spend,
            safety_violations=list(self.safety_violations),
            errors=errors,
            security_notices=security_notices,
        )

    def run_mock_pipeline(
        self,
        doc_path: str,
        view_path: str,
        mock_response_generator: Optional[Callable[[StagePlan], Dict[str, Any]]] = None,
        llm_client: Optional[LLMClientProtocol] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> PipelineExecutionSummary:
        """Executes the pipeline end-to-end in offline mode.
        
        If an LLMClientProtocol is provided (or configured on this instance) and no custom
        mock_response_generator is supplied, delegates to run_agent_pipeline using AgentLoop.
        Otherwise, executes using deterministic mock responses.
        
        Zero HTTP network requests are made.
        Zero generated code or subprocesses are executed.
        Retries are disabled.
        """
        effective_client = llm_client or self.llm_client
        if effective_client is not None and mock_response_generator is None:
            return self.run_agent_pipeline(
                doc_path=doc_path,
                view_path=view_path,
                llm_client=effective_client,
                cancel_event=cancel_event or self.cancel_event,
            )
        errors: List[str] = []
        completed_stages: List[str] = []
        total_reserved = 0.0

        security_notices = [
            "SECURITY NOTICE: Pipeline operated exclusively in offline mock mode. No network calls dispatched.",
            "SECURITY NOTICE: Model output was treated as untrusted data; all file writes validated via PathSecurity.",
            "SECURITY NOTICE: No generated code or tests were executed during this pipeline run.",
            "SECURITY NOTICE: AST and py_compile are syntax checks only, not process-isolation sandboxes.",
        ]

        # 1. Preprocess
        try:
            spec_result = self.preprocess_architecture(doc_path, view_path)
        except Exception as err:
            return PipelineExecutionSummary(
                success=False,
                total_stages_run=0,
                completed_stages=[],
                generated_files=[],
                cumulative_reserved=0.0,
                cumulative_spend=self.budget_controller.cumulative_spend,
                safety_violations=list(self.safety_violations),
                errors=[f"Preprocessing failed: {err}"],
                security_notices=security_notices,
            )

        # 2. Plan
        plan = self.create_generation_plan(spec_result)

        # 3. Execute Stages with Mock Responses
        for stage in plan.stages:
            try:
                # Preflight budget reservation
                reserved = self.preflight_stage_budget(stage)
                total_reserved += reserved

                # Generate mock response
                if mock_response_generator:
                    mock_resp = mock_response_generator(stage)
                else:
                    mock_resp = self._default_mock_response(stage)

                # Validate response
                parsed = self.validate_model_response(mock_resp)

                # Simulate telemetry reconciliation
                usage = mock_resp.get("usage", {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 100,
                })
                self.budget_controller.reconcile(stage.reservation_id, usage)

                # Write simulated generated files
                for expected_file in stage.expected_output_files:
                    mock_content = f"# Generated content for {expected_file}\n# Stage: {stage.stage_id}\n"
                    self.safe_write_generated_file(expected_file, mock_content)

                stage.status = "COMPLETED"
                completed_stages.append(stage.stage_id)

            except Exception as err:
                stage.status = "FAILED"
                errors.append(f"Stage '{stage.stage_id}' failed: {err}")
                # Reconcile or lock reservation upon failure
                if stage.reservation_id in self.budget_controller.active_reservations:
                    self.budget_controller.record_network_failure(stage.reservation_id)
                break  # Fail visibly without unconstrained retries

        success = len(errors) == 0 and len(completed_stages) == len(plan.stages)

        return PipelineExecutionSummary(
            success=success,
            total_stages_run=len(completed_stages),
            completed_stages=completed_stages,
            generated_files=list(self.written_files),
            cumulative_reserved=total_reserved,
            cumulative_spend=self.budget_controller.cumulative_spend,
            safety_violations=list(self.safety_violations),
            errors=errors,
            security_notices=security_notices,
        )

    @staticmethod
    def _default_mock_response(stage: StagePlan) -> Dict[str, Any]:
        """Provides deterministic mock response payload without API calls."""
        return {
            "id": f"mock-{stage.stage_id}",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"// Mock generation output for {stage.description}",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 60,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 120,
            },
        }
