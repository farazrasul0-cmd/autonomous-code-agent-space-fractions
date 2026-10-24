"""Document Extractor Module.

Extracts Markdown structural sections and raw PlantUML blocks from architecture documents,
preserving line numbers, raw diagram content, view classifications, and documenting
known syntactic/semantic anomalies without modifying source files.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PlantUMLBlock:
    index: int
    source_file: str
    diagram_name: str
    view_category: str
    diagram_type: str
    start_line: int
    end_line: int
    raw_content: str
    semantic_notes: List[str] = field(default_factory=list)


@dataclass
class MarkdownSection:
    heading_level: int
    title: str
    start_line: int
    end_line: int
    content: str


@dataclass
class ExtractionResult:
    source_file: str
    total_lines: int
    plantuml_blocks: List[PlantUMLBlock]
    markdown_sections: List[MarkdownSection]


class DocumentExtractor:
    """Extracts raw documentation and PlantUML blocks with source references."""

    VIEW_CATEGORY_PATTERN = re.compile(r"^##\s+([A-Za-z0-9]+View)", re.MULTILINE)
    ITEM_HEADER_PATTERN = re.compile(r"^\d+\.\s+([A-Za-z0-9]+)\s+—\s+([A-Za-z0-9 ]+View):\s+(.+)", re.MULTILINE)
    START_UML_PATTERN = re.compile(r"^@startuml(?:\s+([A-Za-z0-9_]+))?", re.MULTILINE)

    @classmethod
    def extract_from_file(cls, file_path: str) -> ExtractionResult:
        """Parses a Markdown architecture document into structured sections and PlantUML blocks."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Document not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total_lines = len(lines)
        plantuml_blocks: List[PlantUMLBlock] = []
        markdown_sections: List[MarkdownSection] = []

        current_view_category = "General"
        in_plantuml = False
        uml_start_line = -1
        uml_lines: List[str] = []
        diagram_name = ""

        # Extract PlantUML blocks
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Track view category headers
            if stripped.startswith("## ") and "View" in stripped:
                current_view_category = stripped.replace("##", "").strip()

            if stripped.startswith("@startuml"):
                in_plantuml = True
                uml_start_line = idx
                uml_lines = [line]
                match = cls.START_UML_PATTERN.search(stripped)
                diagram_name = match.group(1) if match and match.group(1) else f"Diagram_{len(plantuml_blocks) + 1}"
                continue

            if in_plantuml:
                uml_lines.append(line)
                if stripped.startswith("@enduml"):
                    in_plantuml = False
                    uml_end_line = idx
                    raw_content = "".join(uml_lines)

                    # Classify diagram type
                    diag_type = cls._classify_diagram(diagram_name, raw_content)
                    semantic_notes = cls._detect_semantic_issues(diagram_name, raw_content)

                    block = PlantUMLBlock(
                        index=len(plantuml_blocks) + 1,
                        source_file=os.path.basename(file_path),
                        diagram_name=diagram_name,
                        view_category=current_view_category,
                        diagram_type=diag_type,
                        start_line=uml_start_line,
                        end_line=uml_end_line,
                        raw_content=raw_content,
                        semantic_notes=semantic_notes,
                    )
                    plantuml_blocks.append(block)
                    uml_lines = []

        # Extract Markdown structural sections (headings)
        current_heading_level = 0
        current_title = ""
        current_section_start = 1
        section_lines: List[str] = []

        for idx, line in enumerate(lines, start=1):
            heading_match = re.match(r"^(#{1,6})\s+(.+)", line)
            if heading_match:
                if current_title:
                    markdown_sections.append(
                        MarkdownSection(
                            heading_level=current_heading_level,
                            title=current_title,
                            start_line=current_section_start,
                            end_line=idx - 1,
                            content="".join(section_lines),
                        )
                    )
                current_heading_level = len(heading_match.group(1))
                current_title = heading_match.group(2).strip()
                current_section_start = idx
                section_lines = [line]
            else:
                section_lines.append(line)

        if current_title:
            markdown_sections.append(
                MarkdownSection(
                    heading_level=current_heading_level,
                    title=current_title,
                    start_line=current_section_start,
                    end_line=total_lines,
                    content="".join(section_lines),
                )
            )

        return ExtractionResult(
            source_file=file_path,
            total_lines=total_lines,
            plantuml_blocks=plantuml_blocks,
            markdown_sections=markdown_sections,
        )

    @staticmethod
    def _classify_diagram(diagram_name: str, raw_content: str) -> str:
        """Classifies PlantUML diagram by syntax indicators and name."""
        name_lower = diagram_name.lower()
        if "usecase" in name_lower or "usecase" in raw_content:
            return "UseCase"
        if "class" in name_lower or "class " in raw_content:
            return "Class"
        if "object" in name_lower:
            return "Object"
        if "state" in name_lower or "[*] -->" in raw_content:
            return "State"
        if "activity" in name_lower or ("start" in raw_content and "stop" in raw_content and ":" in raw_content):
            return "Activity"
        if "sequence" in name_lower or "->>" in raw_content:
            return "Sequence"
        if "collaboration" in name_lower:
            return "Collaboration"
        if "package" in name_lower:
            return "Package"
        if "component" in name_lower or "artifact " in raw_content:
            return "Component"
        if "deployment" in name_lower or "node " in raw_content:
            return "Deployment"
        if "container" in name_lower:
            return "Container"
        return "Unknown"

    @staticmethod
    def _detect_semantic_issues(diagram_name: str, raw_content: str) -> List[str]:
        """Detects documented semantic or syntax anomalies in the raw PlantUML."""
        notes = []
        if "ObjectDiagram" in diagram_name:
            if "participant " in raw_content:
                notes.append("Syntax mismatch: Uses Sequence diagram keyword 'participant' inside an Object Diagram.")
        if "ActivityDiagram" in diagram_name:
            if ":continue playing;" in raw_content and "stop" in raw_content:
                if not re.search(r"repeat|while|backward", raw_content):
                    notes.append("Logic flaw: Activity flow has no loop-back edge for 'continue playing'; terminates unconditionally.")
        if "ClassDiagram" in diagram_name:
            if "Admin --* Question" in raw_content:
                notes.append("Semantic note: Composition implies private ownership of Question by Admin rather than shared repository.")
            if "Question" in raw_content and "checkAnswer" not in raw_content:
                notes.append("Semantic gap: Question class definition omits answer verification method called in SequenceDiagram1.")
        return notes
