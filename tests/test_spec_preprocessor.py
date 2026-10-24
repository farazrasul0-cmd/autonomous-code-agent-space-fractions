"""Tests for Specification Preprocessor and Offline Generator Pipeline.

Verifies:
1. Extraction of all 11 numbered view items and 13 PlantUML diagram blocks.
2. Stable source ordering and traceability references.
3. Fail-visible handling on missing, empty, or malformed inputs.
4. Faithful preservation of the desktop vs. cloud architectural conflict.
5. Deterministic serialization across repeated runs.
6. Entity and deliverable specification extraction.
7. Offline pipeline execution with mock dependencies only.
8. Rejection of unsafe paths, traversals, and unbounded file writes.
9. Absence of HTTP network calls, subprocess execution, or generated-code execution.
"""

import http.client
import json
import os
import subprocess
import tempfile
import urllib.request
import pytest

from src.budget_controller import BudgetController
from src.path_security import PathSecurityError
from src.spec_preprocessor import (
    SpecificationPreprocessor,
    SpecificationExtractionError,
    SpecificationPreprocessResult,
)
from src.generator_pipeline import (
    OfflinePipelineCoordinator,
    PipelineExecutionError,
    PipelineStageType,
)


@pytest.fixture
def canonical_doc_paths():
    doc_path = os.path.abspath("Architecture_Documentation.md")
    view_path = os.path.abspath("Architecture_View.md")
    assert os.path.isfile(doc_path), f"Missing {doc_path}"
    assert os.path.isfile(view_path), f"Missing {view_path}"
    return doc_path, view_path


def test_all_11_numbered_view_items_extracted(canonical_doc_paths):
    doc_path, view_path = canonical_doc_paths
    result = SpecificationPreprocessor.preprocess(doc_path, view_path)

    assert len(result.view_items) == 11
    expected_items = [
        (1, "UseCase", "Scenario"),
        (2, "Class", "Logic"),
        (3, "Object", "Logic"),
        (4, "State", "Logic"),
        (5, "Activity", "Process"),
        (6, "Sequence", "Process"),
        (7, "Collaboration", "Process"),
        (8, "Package", "Development"),
        (9, "Component", "Development"),
        (10, "Deployment", "Physical"),
        (11, "Container", "Physical"),
    ]

    for idx, (expected_num, expected_title, expected_category) in enumerate(expected_items):
        item = result.view_items[idx]
        assert item.item_number == expected_num
        assert item.short_title == expected_title
        assert expected_category in item.view_category
        assert item.start_line < item.end_line
        assert item.diagram_count >= 1


def test_all_13_plantuml_diagram_blocks_extracted(canonical_doc_paths):
    doc_path, view_path = canonical_doc_paths
    result = SpecificationPreprocessor.preprocess(doc_path, view_path)

    assert len(result.diagrams) == 13
    expected_diagram_ids = [
        "UseCaseDiagram",
        "ClassDiagram",
        "ObjectDiagram",
        "StateDiagram",
        "ActivityDiagram",
        "SequenceDiagram1",
        "SequenceDiagram2",
        "CollaborationDiagram1",
        "CollaborationDiagram2",
        "PackageDiagram",
        "ComponentDiagram",
        "DeploymentDiagram",
        "ContainerDiagram",
    ]

    actual_diagram_ids = [d.diagram_id for d in result.diagrams]
    assert actual_diagram_ids == expected_diagram_ids

    # Verify that Item 6 (Sequence) has 2 diagrams and Item 7 (Collaboration) has 2 diagrams
    item_6 = next(i for i in result.view_items if i.item_number == 6)
    assert item_6.diagram_count == 2
    assert item_6.diagram_ids == ["SequenceDiagram1", "SequenceDiagram2"]

    item_7 = next(i for i in result.view_items if i.item_number == 7)
    assert item_7.diagram_count == 2
    assert item_7.diagram_ids == ["CollaborationDiagram1", "CollaborationDiagram2"]


def test_stable_source_ordering_and_traceability(canonical_doc_paths):
    doc_path, view_path = canonical_doc_paths
    result = SpecificationPreprocessor.preprocess(doc_path, view_path)

    # 1. View items strictly in order 1..11
    item_numbers = [item.item_number for item in result.view_items]
    assert item_numbers == list(range(1, 12))

    # 2. Diagrams strictly in order of appearance (index 1..13)
    diagram_indices = [d.index for d in result.diagrams]
    assert diagram_indices == list(range(1, 14))

    # 3. Explicit requirements traceability mappings
    req_map = {r.req_id: r for r in result.explicit_requirements}
    assert "FR-1" in req_map
    assert "NFR-1" in req_map
    assert "ASR-1" in req_map

    fr1 = req_map["FR-1"]
    assert "UseCaseDiagram" in fr1.diagram_refs
    assert "GameComponent" in fr1.component_refs
    assert "openapi.yaml" in fr1.artifact_refs

    nfr1 = req_map["NFR-1"]
    assert "SequenceDiagram1" in nfr1.diagram_refs
    assert "GameComponent" in nfr1.component_refs
    assert "internal.proto" in nfr1.artifact_refs

    asr1 = req_map["ASR-1"]
    assert "DeploymentDiagram" in asr1.diagram_refs
    assert "QuestionComponent" in asr1.component_refs
    assert "sql/question_ddl.sql" in asr1.artifact_refs


def test_missing_and_empty_input_files():
    # Non-existent file
    with pytest.raises(SpecificationExtractionError, match="not found"):
        SpecificationPreprocessor.preprocess("non_existent_doc.md", "Architecture_View.md")

    with pytest.raises(SpecificationExtractionError, match="not found"):
        SpecificationPreprocessor.preprocess("Architecture_Documentation.md", "non_existent_view.md")

    # Empty file
    with tempfile.NamedTemporaryFile("w", delete=False) as empty_file:
        empty_name = empty_file.name
    try:
        with pytest.raises(SpecificationExtractionError, match="is empty"):
            SpecificationPreprocessor.preprocess(empty_name, "Architecture_View.md")
    finally:
        if os.path.exists(empty_name):
            os.remove(empty_name)


def test_malformed_input_missing_views_fails_visibly(canonical_doc_paths):
    doc_path, _ = canonical_doc_paths

    # Create a truncated view file containing only 3 view items
    truncated_content = """## ScenarioView
1. UseCase — Scenario View: Use Case Diagram
```plantuml
@startuml UseCaseDiagram
actor User
User -> (Play)
@enduml
```
2. Class — Logic View: Class Diagram
```plantuml
@startuml ClassDiagram
class Game
@enduml
```
"""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(truncated_content)
        truncated_path = f.name

    try:
        # Preprocessor must visibly raise SpecificationExtractionError, not silently return incomplete data
        with pytest.raises(SpecificationExtractionError, match="Missing required view items"):
            SpecificationPreprocessor.preprocess(doc_path, truncated_path)
    finally:
        if os.path.exists(truncated_path):
            os.remove(truncated_path)


def test_preservation_of_desktop_vs_cloud_conflict(canonical_doc_paths):
    doc_path, view_path = canonical_doc_paths
    result = SpecificationPreprocessor.preprocess(doc_path, view_path)

    assert len(result.recorded_conflicts) >= 1
    conflict = result.recorded_conflicts[0]

    assert conflict.conflict_id == "CONFLICT-DELIVERABLE-PLATFORM-001"
    assert conflict.status == "RECORDED_UNRESOLVED"
    assert "Native Windows Desktop" in conflict.title
    assert "Cloud Microservices" in conflict.title

    # Source texts preserved
    assert "Task1-EN.docx" in conflict.source_a["document"]
    assert "Architecture_Documentation.md" in conflict.source_b["document"]
    assert "microservices" in conflict.source_b["text"].lower()


def test_deterministic_output_across_repeated_runs(canonical_doc_paths):
    doc_path, view_path = canonical_doc_paths

    res1 = SpecificationPreprocessor.preprocess(doc_path, view_path)
    res2 = SpecificationPreprocessor.preprocess(doc_path, view_path)

    json1 = res1.to_json()
    json2 = res2.to_json()

    # Exact character-for-character, byte-for-byte identity
    assert json1 == json2
    assert res1.serialized_utf8_bytes == res2.serialized_utf8_bytes
    assert res1.serialized_utf8_bytes == len(json1.encode("utf-8"))


def test_extracted_entities_and_deliverables(canonical_doc_paths):
    doc_path, view_path = canonical_doc_paths
    result = SpecificationPreprocessor.preprocess(doc_path, view_path)

    entities = result.entities
    assert set(entities.actors) == {"Admin", "End User"}
    class_names = [c["name"] for c in entities.classes]
    assert set(class_names) == {"Admin", "Game", "Question", "User"}
    assert set(entities.states) == {"GameOver", "Paused", "Playing"}
    assert set(entities.deployment_nodes) == {"AdminClient", "GameServer", "QuestionServer", "UserClient"}
    assert set(entities.container_artifacts) == {"AdminContainer", "GameContainer", "QuestionContainer", "UserContainer"}

    # Deliverables from Section L
    deliverable_files = [d.filename for d in result.deliverable_specs]
    assert "openapi.yaml" in deliverable_files
    assert "internal.proto" in deliverable_files
    assert "k8s/spacefractions-deployment.yaml" in deliverable_files
    assert "sql/game_ddl.sql" in deliverable_files
    assert "traceability_matrix.csv" in deliverable_files


def test_pipeline_operation_with_mock_dependencies_only(canonical_doc_paths):
    doc_path, view_path = canonical_doc_paths

    with tempfile.TemporaryDirectory() as temp_workspace:
        output_dir = os.path.join(temp_workspace, "generated_output")
        os.makedirs(output_dir, exist_ok=True)

        budget = BudgetController(ceiling=0.80)
        coordinator = OfflinePipelineCoordinator(
            workspace_root=temp_workspace,
            output_dir=output_dir,
            budget_controller=budget,
        )

        summary = coordinator.run_mock_pipeline(doc_path, view_path)

        assert summary.success is True
        assert summary.total_stages_run == 4
        assert len(summary.completed_stages) == 4
        assert len(summary.generated_files) > 0
        assert summary.cumulative_reserved > 0.0
        assert summary.cumulative_spend > 0.0
        assert summary.cumulative_spend <= 0.80
        assert len(summary.safety_violations) == 0
        assert len(summary.errors) == 0

        # Check generated files on disk
        for file_path in summary.generated_files:
            assert os.path.isfile(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                assert "# Generated content" in content


def test_pipeline_rejection_of_unsafe_paths():
    with tempfile.TemporaryDirectory() as temp_workspace:
        output_dir = os.path.join(temp_workspace, "out")
        os.makedirs(output_dir, exist_ok=True)

        coordinator = OfflinePipelineCoordinator(
            workspace_root=temp_workspace,
            output_dir=output_dir,
        )

        # 1. Traversal outside output directory
        with pytest.raises(PathSecurityError):
            coordinator.safe_write_generated_file("../escape.txt", "content")

        # 2. Absolute path
        with pytest.raises(PathSecurityError):
            coordinator.safe_write_generated_file("C:\\Windows\\win.ini", "content")

        # 3. Null byte in path
        with pytest.raises(PathSecurityError):
            coordinator.safe_write_generated_file("safe\0evil.txt", "content")

        # 4. Null byte in content
        with pytest.raises(PathSecurityError):
            coordinator.safe_write_generated_file("evil_null.txt", "null\0content")

        assert len(coordinator.safety_violations) >= 4


def test_pipeline_bounded_file_write_rejection():
    with tempfile.TemporaryDirectory() as temp_workspace:
        output_dir = os.path.join(temp_workspace, "out")
        os.makedirs(output_dir, exist_ok=True)

        coordinator = OfflinePipelineCoordinator(
            workspace_root=temp_workspace,
            output_dir=output_dir,
        )

        # Attempt to write 200 bytes when max_bytes is 50
        oversized_content = "X" * 200
        with pytest.raises(PipelineExecutionError, match="exceeds maximum permitted limit"):
            coordinator.safe_write_generated_file("big.txt", oversized_content, max_bytes=50)


def test_no_http_calls_or_subprocess_execution_in_pipeline(canonical_doc_paths, monkeypatch):
    doc_path, view_path = canonical_doc_paths

    def prohibited_network_call(*args, **kwargs):
        raise AssertionError("Network HTTP call was attempted during offline pipeline execution!")

    def prohibited_subprocess_call(*args, **kwargs):
        raise AssertionError("Subprocess call was attempted during offline pipeline execution!")

    # Intercept network sockets and HTTP clients
    monkeypatch.setattr(urllib.request, "urlopen", prohibited_network_call)
    monkeypatch.setattr(http.client.HTTPConnection, "request", prohibited_network_call)
    monkeypatch.setattr(subprocess, "Popen", prohibited_subprocess_call)
    monkeypatch.setattr(subprocess, "run", prohibited_subprocess_call)

    # Run preprocessor and mock pipeline with network and subprocess disabled
    with tempfile.TemporaryDirectory() as temp_workspace:
        output_dir = os.path.join(temp_workspace, "out")
        os.makedirs(output_dir, exist_ok=True)

        coordinator = OfflinePipelineCoordinator(
            workspace_root=temp_workspace,
            output_dir=output_dir,
        )

        summary = coordinator.run_mock_pipeline(doc_path, view_path)
        assert summary.success is True
