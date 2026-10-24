"""Unit tests for DocumentExtractor.

Validates extraction of raw PlantUML blocks from both architecture documents,
preservation of source line references, diagram type classification, and
explains the exact block count discrepancy between the 11 numbered view items
and the 13 raw @startuml blocks.
"""

import os
import pytest
from src.doc_extractor import DocumentExtractor


WORKSPACE_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
DOC_PATH = os.path.join(WORKSPACE_DIR, "Architecture_Documentation.md")
VIEW_PATH = os.path.join(WORKSPACE_DIR, "Architecture_View.md")


def test_extract_architecture_view():
    """Validates extraction of PlantUML blocks from Architecture_View.md."""
    res = DocumentExtractor.extract_from_file(VIEW_PATH)

    assert res.total_lines == 201
    blocks = res.plantuml_blocks

    # ARCHITECTURAL AUDIT FINDING:
    # Architecture_View.md groups diagrams into 11 numbered sections:
    # 1. UseCase, 2. Class, 3. Object, 4. State, 5. Activity,
    # 6. Sequence (contains 2 blocks: SequenceDiagram1 & SequenceDiagram2),
    # 7. Collaboration (contains 2 blocks: CollaborationDiagram1 & CollaborationDiagram2),
    # 8. Package, 9. Component, 10. Deployment, 11. Container.
    # Therefore, exactly 13 @startuml...@enduml blocks exist across the 11 numbered items.
    assert len(blocks) == 13, f"Expected 13 raw PlantUML blocks, found {len(blocks)}"

    diagram_names = [b.diagram_name for b in blocks]
    expected_names = [
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
    assert diagram_names == expected_names

    # Check that line references are strictly positive, ascending, and bounded by 201
    for block in blocks:
        assert 1 <= block.start_line < block.end_line <= 201
        assert "@startuml" in block.raw_content
        assert "@enduml" in block.raw_content

    # Validate specific line references for key diagrams
    use_case = blocks[0]
    assert use_case.diagram_name == "UseCaseDiagram"
    assert use_case.start_line == 4
    assert use_case.end_line == 18

    container = blocks[-1]
    assert container.diagram_name == "ContainerDiagram"
    assert container.start_line == 192
    assert container.end_line == 200


def test_extract_architecture_documentation():
    """Validates that Architecture_Documentation.md contains markdown sections and 0 PlantUML blocks."""
    res = DocumentExtractor.extract_from_file(DOC_PATH)

    assert res.total_lines == 315
    # Architecture_Documentation.md contains YAML, SQL, proto, and CSV snippets, but NO @startuml blocks.
    assert len(res.plantuml_blocks) == 0

    # It must contain structured markdown sections (Executive Summary, Detailed Design, etc.)
    section_titles = [s.title for s in res.markdown_sections]
    assert any("Executive Summary" in t for t in section_titles)
    assert any("Detailed Technical Design" in t for t in section_titles)
    assert any("Testing Strategy" in t for t in section_titles)
    assert any("Deliverables" in t for t in section_titles)


def test_detected_semantic_issues_annotated():
    """Validates that known syntactic/semantic anomalies are annotated without mutating source."""
    res = DocumentExtractor.extract_from_file(VIEW_PATH)
    block_map = {b.diagram_name: b for b in res.plantuml_blocks}

    # 1. Object diagram using sequence keyword 'participant'
    assert any("participant" in note for note in block_map["ObjectDiagram"].semantic_notes)

    # 2. Activity diagram missing loop-back
    assert any("loop-back" in note for note in block_map["ActivityDiagram"].semantic_notes)

    # 3. Class diagram composition and missing checkAnswer
    assert any("Composition" in note for note in block_map["ClassDiagram"].semantic_notes)
    assert any("Question" in note for note in block_map["ClassDiagram"].semantic_notes)
