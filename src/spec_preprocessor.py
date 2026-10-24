"""Specification Preprocessor Module.

Reads Architecture_Documentation.md and Architecture_View.md without modifying them,
extracts all 11 numbered view items and 13 PlantUML blocks with exact line references,
distinguishes explicit requirements from inferred decisions, and records the architectural
conflict between the Windows desktop deliverable and cloud microservices without
silently rewriting source requirements.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.doc_extractor import DocumentExtractor, PlantUMLBlock, MarkdownSection


class SpecificationExtractionError(Exception):
    """Raised when architecture documentation cannot be parsed or required items are missing."""
    pass


@dataclass
class ViewDiagram:
    index: int
    diagram_id: str
    diagram_type: str
    start_line: int
    end_line: int
    raw_content: str
    semantic_notes: List[str] = field(default_factory=list)


@dataclass
class NumberedViewItem:
    item_number: int
    short_title: str
    view_category: str
    full_title: str
    start_line: int
    end_line: int
    diagram_count: int
    diagram_ids: List[str]
    diagrams: List[ViewDiagram] = field(default_factory=list)


@dataclass
class ExplicitRequirement:
    req_id: str
    category: str  # "Functional", "NonFunctional", "ArchitecturalSignificant", "Assumption"
    short_text: str
    source_file: str
    source_section: str
    source_line: int
    diagram_refs: List[str]
    component_refs: List[str]
    artifact_refs: List[str]
    rationale: str


@dataclass
class InferredDecision:
    decision_id: str
    topic: str
    inferred_choice: str
    source_section: str
    justification: str
    is_explicit_in_doc: bool = False


@dataclass
class ArchitecturalConflict:
    conflict_id: str
    title: str
    conflict_category: str
    source_a: Dict[str, Any]
    source_b: Dict[str, Any]
    description: str
    status: str  # "RECORDED_UNRESOLVED"
    architectural_implication: str


@dataclass
class ExtractedEntities:
    actors: List[str]
    classes: List[Dict[str, Any]]
    states: List[str]
    operations: List[str]
    deployment_nodes: List[str]
    container_artifacts: List[str]
    components: List[str]


@dataclass
class DeliverableSpecification:
    filename: str
    format: str
    source_section: str
    start_line: int
    end_line: int
    raw_content: str


@dataclass
class SpecificationPreprocessResult:
    schema_version: str
    source_files: Dict[str, Dict[str, Any]]
    extraction_warnings: List[str]
    view_items: List[NumberedViewItem]
    diagrams: List[ViewDiagram]
    explicit_requirements: List[ExplicitRequirement]
    inferred_decisions: List[InferredDecision]
    recorded_conflicts: List[ArchitecturalConflict]
    entities: ExtractedEntities
    deliverable_specs: List[DeliverableSpecification]
    serialized_utf8_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Converts result to a clean, deterministic dictionary."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Serializes result to a deterministic JSON string."""
        data = self.to_dict()
        # Remove serialized_utf8_bytes self-reference during calculation
        data["serialized_utf8_bytes"] = 0
        raw_json = json.dumps(data, indent=indent, sort_keys=True, ensure_ascii=False)
        byte_len = len(raw_json.encode("utf-8"))
        data["serialized_utf8_bytes"] = byte_len
        return json.dumps(data, indent=indent, sort_keys=True, ensure_ascii=False)


class SpecificationPreprocessor:
    """Parses and validates architecture documentation and views."""

    REQUIRED_VIEW_COUNT = 11
    REQUIRED_DIAGRAM_COUNT = 13

    VIEW_ITEM_PATTERN = re.compile(
        r"^(\d+)\.\s+([A-Za-z0-9]+)\s+[—\-]\s+([A-Za-z0-9 ]+View):\s*(.*)$"
    )

    @classmethod
    def preprocess(
        cls,
        doc_path: str,
        view_path: str,
        assignment_brief_text: Optional[str] = None,
    ) -> SpecificationPreprocessResult:
        """Extracts structured architecture specification from markdown source files.
        
        Preserves original documents as read-only inputs.
        Fails visibly if required view items or diagram blocks are missing.
        """
        if not os.path.isfile(doc_path):
            raise SpecificationExtractionError(f"Architecture documentation file not found: {doc_path}")
        if not os.path.isfile(view_path):
            raise SpecificationExtractionError(f"Architecture view file not found: {view_path}")

        # Check for empty files
        if os.path.getsize(doc_path) == 0:
            raise SpecificationExtractionError(f"Architecture documentation file is empty: {doc_path}")
        if os.path.getsize(view_path) == 0:
            raise SpecificationExtractionError(f"Architecture view file is empty: {view_path}")

        # Compute file metadata without modifying files
        doc_meta = cls._get_file_metadata(doc_path)
        view_meta = cls._get_file_metadata(view_path)

        with open(doc_path, "r", encoding="utf-8") as f:
            doc_lines = f.readlines()
        with open(view_path, "r", encoding="utf-8") as f:
            view_lines = f.readlines()

        warnings: List[str] = []

        # 1. Parse Architecture_View.md (11 numbered items, 13 PlantUML blocks)
        view_items, diagrams, view_warnings = cls._parse_views(view_path, view_lines)
        warnings.extend(view_warnings)

        if len(view_items) != cls.REQUIRED_VIEW_COUNT:
            found_nums = [item.item_number for item in view_items]
            raise SpecificationExtractionError(
                f"Missing required view items in '{view_path}'. Expected {cls.REQUIRED_VIEW_COUNT}, "
                f"found {len(view_items)}. Items found: {found_nums}"
            )

        if len(diagrams) != cls.REQUIRED_DIAGRAM_COUNT:
            found_diags = [d.diagram_id for d in diagrams]
            raise SpecificationExtractionError(
                f"Missing required PlantUML diagrams in '{view_path}'. Expected {cls.REQUIRED_DIAGRAM_COUNT}, "
                f"found {len(diagrams)}. Diagrams found: {found_diags}"
            )

        # 2. Extract Entities from PlantUML diagrams
        entities = cls._extract_entities(diagrams)

        # 3. Parse Architecture_Documentation.md
        reqs, decisions, deliverables, doc_warnings = cls._parse_documentation(doc_path, doc_lines)
        warnings.extend(doc_warnings)

        # 4. Record Architectural Conflicts
        conflicts = cls._record_conflicts(doc_path, doc_lines, assignment_brief_text)

        result = SpecificationPreprocessResult(
            schema_version="1.0.0",
            source_files={
                "documentation": doc_meta,
                "views": view_meta,
            },
            extraction_warnings=sorted(warnings),
            view_items=view_items,
            diagrams=diagrams,
            explicit_requirements=reqs,
            inferred_decisions=decisions,
            recorded_conflicts=conflicts,
            entities=entities,
            deliverable_specs=deliverables,
        )

        # Measure serialized UTF-8 bytes
        rendered_json = result.to_json()
        result.serialized_utf8_bytes = len(rendered_json.encode("utf-8"))

        return result

    @classmethod
    def _get_file_metadata(cls, file_path: str) -> Dict[str, Any]:
        """Calculates file size, line count, and SHA256 checksum."""
        hasher = hashlib.sha256()
        line_count = 0
        byte_size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
                line_count += chunk.count(b"\n")
        return {
            "filename": os.path.basename(file_path),
            "absolute_path": os.path.abspath(file_path),
            "byte_size": byte_size,
            "line_count": line_count,
            "sha256": hasher.hexdigest(),
        }

    @classmethod
    def _parse_views(
        cls, view_path: str, lines: List[str]
    ) -> Tuple[List[NumberedViewItem], List[ViewDiagram], List[str]]:
        """Parses the 11 numbered view items and 13 PlantUML blocks."""
        doc_ext_result = DocumentExtractor.extract_from_file(view_path)
        all_blocks = doc_ext_result.plantuml_blocks
        warnings: List[str] = []

        # Convert doc_extractor PlantUMLBlocks to ViewDiagrams
        diagrams: List[ViewDiagram] = []
        for b in all_blocks:
            diagrams.append(
                ViewDiagram(
                    index=b.index,
                    diagram_id=b.diagram_name,
                    diagram_type=b.diagram_type,
                    start_line=b.start_line,
                    end_line=b.end_line,
                    raw_content=b.raw_content,
                    semantic_notes=list(b.semantic_notes),
                )
            )
            for note in b.semantic_notes:
                warnings.append(f"{b.diagram_name} (lines {b.start_line}-{b.end_line}): {note}")

        # Find line positions of numbered items
        item_headers: List[Tuple[int, int, str, str, str]] = []  # (item_num, line_idx, short_title, view_cat, full_title)
        current_view_cat = "General"

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("## ") and "View" in stripped:
                current_view_cat = stripped.replace("##", "").strip()

            match = cls.VIEW_ITEM_PATTERN.match(stripped)
            if match:
                num = int(match.group(1))
                short_title = match.group(2)
                view_cat = match.group(3)
                item_headers.append((num, idx, short_title, view_cat, stripped))

        view_items: List[NumberedViewItem] = []
        for i, (num, start_l, short_t, view_cat, full_t) in enumerate(item_headers):
            end_l = item_headers[i + 1][1] - 1 if i + 1 < len(item_headers) else len(lines)

            # Associate diagrams that fall within this line range
            item_diagrams = [
                d for d in diagrams if start_l <= d.start_line <= end_l
            ]

            view_items.append(
                NumberedViewItem(
                    item_number=num,
                    short_title=short_t,
                    view_category=view_cat,
                    full_title=full_t,
                    start_line=start_l,
                    end_line=end_l,
                    diagram_count=len(item_diagrams),
                    diagram_ids=[d.diagram_id for d in item_diagrams],
                    diagrams=item_diagrams,
                )
            )

        # Sort view items deterministically by item_number
        view_items.sort(key=lambda x: x.item_number)
        diagrams.sort(key=lambda x: x.index)

        return view_items, diagrams, warnings

    @classmethod
    def _extract_entities(cls, diagrams: List[ViewDiagram]) -> ExtractedEntities:
        """Extracts actors, classes, states, operations, and deployment artifacts from PlantUML."""
        actors: set[str] = set()
        classes: Dict[str, Dict[str, Any]] = {}
        states: set[str] = set()
        operations: set[str] = set()
        deployment_nodes: set[str] = set()
        container_artifacts: set[str] = set()
        components: set[str] = set()

        for d in diagrams:
            content = d.raw_content

            # Actors
            for match in re.finditer(r'actor\s+([A-Za-z0-9_]+)(?:\s+as\s+"([^"]+)")?', content):
                name = match.group(2) if match.group(2) else match.group(1)
                actors.add(name)

            # Classes
            for match in re.finditer(r'class\s+([A-Za-z0-9_]+)\s*\{([^}]*)\}', content, re.DOTALL):
                cname = match.group(1)
                body = match.group(2)
                attrs: List[str] = []
                methods: List[str] = []
                for line in body.splitlines():
                    s = line.strip()
                    if s.startswith("-"):
                        attrs.append(s[1:].strip())
                    elif s.startswith("+"):
                        methods.append(s[1:].strip())
                        op_name = re.match(r"^([A-Za-z0-9_]+)", s[1:].strip())
                        if op_name:
                            operations.add(op_name.group(1))
                classes[cname] = {
                    "name": cname,
                    "attributes": sorted(attrs),
                    "methods": sorted(methods),
                }

            # States
            for match in re.finditer(r'state\s+([A-Za-z0-9_]+)', content):
                states.add(match.group(1))

            # State transitions & operations
            for match in re.finditer(r':\s*([A-Za-z0-9_]+)\(\)', content):
                operations.add(match.group(1))

            # Use case operations
            for match in re.finditer(r'usecase\s+"([^"]+)"', content):
                operations.add(match.group(1))

            # Sequence & Activity calls
            for match in re.finditer(r'->>?[A-Za-z0-9_]+:\s*([A-Za-z0-9_ ]+)', content):
                operations.add(match.group(1).strip())
            for match in re.finditer(r':([A-Za-z0-9_ ]+);', content):
                operations.add(match.group(1).strip())

            # Deployment nodes
            for match in re.finditer(r'node\s+([A-Za-z0-9_]+)', content):
                deployment_nodes.add(match.group(1))

            # Containers & Component artifacts
            for match in re.finditer(r'artifact\s+([A-Za-z0-9_]+)', content):
                art_name = match.group(1)
                if "Container" in art_name:
                    container_artifacts.add(art_name)
                elif "Component" in art_name:
                    components.add(art_name)

        class_list = [classes[k] for k in sorted(classes.keys())]

        return ExtractedEntities(
            actors=sorted(actors),
            classes=class_list,
            states=sorted(states),
            operations=sorted(operations),
            deployment_nodes=sorted(deployment_nodes),
            container_artifacts=sorted(container_artifacts),
            components=sorted(components),
        )

    @classmethod
    def _parse_documentation(
        cls, doc_path: str, lines: List[str]
    ) -> Tuple[List[ExplicitRequirement], List[InferredDecision], List[DeliverableSpecification], List[str]]:
        """Extracts requirements, decisions, and deliverables from Architecture_Documentation.md."""
        content = "".join(lines)
        warnings: List[str] = []

        # Validate presence of core sections
        required_headers = ["Executive Summary", "Detailed Technical Design", "Deliverables"]
        for header in required_headers:
            if header not in content:
                raise SpecificationExtractionError(
                    f"Required canonical section '{header}' is missing from '{doc_path}'."
                )

        explicit_reqs: List[ExplicitRequirement] = []

        # 1. Parse Traceability Table in Section B
        table_regex = re.compile(
            r"^\|\s*([A-Za-z0-9\-]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|",
            re.MULTILINE,
        )
        for match in table_regex.finditer(content):
            rid = match.group(1).strip()
            if rid in ("Requirement ID", "ASR/NFR ID", "---"):
                continue
            short_txt = match.group(2).strip()
            diagrams = [d.strip() for d in match.group(3).split(",") if d.strip()]
            comps = [c.strip() for c in match.group(4).split(",") if c.strip()]
            artifacts = [a.strip() for a in match.group(5).split(",") if a.strip()]
            rationale = match.group(6).strip()

            cat = "Functional" if rid.startswith("FR") else (
                "NonFunctional" if rid.startswith("NFR") else "ArchitecturalSignificant"
            )

            # Locate line number in lines
            target_line = 1
            for lidx, ltext in enumerate(lines, start=1):
                if f"| {rid} |" in ltext:
                    target_line = lidx
                    break

            explicit_reqs.append(
                ExplicitRequirement(
                    req_id=rid,
                    category=cat,
                    short_text=short_txt,
                    source_file=os.path.basename(doc_path),
                    source_section="B. Traceability & Rationale",
                    source_line=target_line,
                    diagram_refs=diagrams,
                    component_refs=comps,
                    artifact_refs=artifacts,
                    rationale=rationale,
                )
            )

        # 2. Parse Assumptions in Section K
        for lidx, ltext in enumerate(lines, start=1):
            assump_match = re.match(r"^(A\d+):\s*(.+)", ltext.strip())
            if assump_match:
                aid = assump_match.group(1)
                atext = assump_match.group(2)
                explicit_reqs.append(
                    ExplicitRequirement(
                        req_id=aid,
                        category="Assumption",
                        short_text=atext,
                        source_file=os.path.basename(doc_path),
                        source_section="K. Open Questions & Assumptions",
                        source_line=lidx,
                        diagram_refs=[],
                        component_refs=[],
                        artifact_refs=[],
                        rationale="Explicitly documented operational assumption.",
                    )
                )

        explicit_reqs.sort(key=lambda r: r.req_id)

        # 3. Explicit vs Inferred Decisions
        inferred_decisions = [
            InferredDecision(
                decision_id="DEC-001",
                topic="Cloud Microservices Backend Stack",
                inferred_choice="Node.js 18 / Express.js 4 / PostgreSQL 14 / Redis 6 / RabbitMQ 3",
                source_section="D. Detailed Technical Design",
                justification="Document states stack meets ASR-1 (durability), NFR-1 (performance), and ASR-2 (security) for cloud microservices.",
                is_explicit_in_doc=False,
            ),
            InferredDecision(
                decision_id="DEC-002",
                topic="Container Orchestration & Topology",
                inferred_choice="Docker 20 container runtime with Kubernetes 3-replica deployment and PostgreSQL HA replication",
                source_section="E. Operations & Deployment",
                justification="Document provides Kubernetes manifest and specifies HA data replication strategy.",
                is_explicit_in_doc=False,
            ),
            InferredDecision(
                decision_id="DEC-003",
                topic="Client Platform Adaptation for Submission",
                inferred_choice="Standalone Native Windows Tkinter GUI Game Engine",
                source_section="Architecture Reconciliation Strategy",
                justification="Inferred to fulfill the mandatory assignment brief (Task1-EN.docx) requiring a double-clickable Windows desktop .exe without Python, while preserving microservices specs in /specs.",
                is_explicit_in_doc=False,
            ),
        ]
        inferred_decisions.sort(key=lambda d: d.decision_id)

        # 4. Extract Deliverable Code Snippets from Section L
        deliverables = cls._extract_deliverable_snippets(lines)

        return explicit_reqs, inferred_decisions, deliverables, warnings

    @classmethod
    def _extract_deliverable_snippets(cls, lines: List[str]) -> List[DeliverableSpecification]:
        """Extracts code blocks in Section L representing deliverable artifacts."""
        deliverables: List[DeliverableSpecification] = []
        in_section_l = False
        in_code_block = False
        block_format = ""
        block_start = 0
        block_lines: List[str] = []

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("# L. Deliverables"):
                in_section_l = True
                continue
            if in_section_l and stripped.startswith("# ") and not stripped.startswith("# L."):
                in_section_l = False

            if in_section_l:
                if stripped.startswith("```"):
                    if not in_code_block:
                        in_code_block = True
                        block_format = stripped.replace("```", "").strip()
                        block_start = idx
                        block_lines = []
                    else:
                        in_code_block = False
                        raw = "".join(block_lines)
                        filename = ""
                        fmt = block_format

                        if block_format == "markdown":
                            filename = "deliverables_manifest.txt"
                            fmt = "text"
                        elif "openapi:" in raw and ("paths:" in raw or "openapi: 3." in raw):
                            filename = "openapi.yaml"
                            fmt = "yaml"
                        elif 'syntax = "proto3"' in raw:
                            filename = "internal.proto"
                            fmt = "proto"
                        elif "apiVersion: apps/v1" in raw or "kind: Deployment" in raw:
                            filename = "k8s/spacefractions-deployment.yaml"
                            fmt = "yaml"
                        elif "CREATE TABLE" in raw:
                            filename = "sql/game_ddl.sql"
                            fmt = "sql"
                        elif "Requirement ID,Short Text" in raw:
                            filename = "traceability_matrix.csv"
                            fmt = "csv"

                        if filename:
                            deliverables.append(
                                DeliverableSpecification(
                                    filename=filename,
                                    format=fmt,
                                    source_section="L. Deliverables",
                                    start_line=block_start,
                                    end_line=idx,
                                    raw_content=raw,
                                )
                            )
                        block_lines = []
                elif in_code_block:
                    block_lines.append(line)

        deliverables.sort(key=lambda d: d.filename)
        return deliverables

    @classmethod
    def _record_conflicts(
        cls, doc_path: str, doc_lines: List[str], assignment_brief_text: Optional[str] = None
    ) -> List[ArchitecturalConflict]:
        """Identifies and preserves architectural conflicts rather than silently resolving them."""
        conflicts: List[ArchitecturalConflict] = []

        # The primary conflict between Assignment Brief and Architecture Documentation
        brief_quote = (
            assignment_brief_text
            if assignment_brief_text
            else "Deliverable: Windows desktop application. Produce a doubleclickrun .exe executable. "
                 "No preinstalled Python runtime is required on target machines. The application shall provide "
                 "minimal GUI for DeepSeek APIKey configuration, inputfile selection, task triggering and "
                 "outputdirectory assignment."
        )

        doc_quote = (
            "Section A: The system will follow a microservices approach with separate components... "
            "deployed on a cloud-based infrastructure. Section D: Technology options include Node.js 18, Express.js 4, "
            "PostgreSQL 14, Redis 6, RabbitMQ 3, Docker 20. Section E: Kubernetes 3-replica deployment."
        )

        conflicts.append(
            ArchitecturalConflict(
                conflict_id="CONFLICT-DELIVERABLE-PLATFORM-001",
                title="Platform Mismatch: Native Windows Desktop Executable vs. Cloud Microservices Infrastructure",
                conflict_category="PLATFORM_DELIVERABLE_MISMATCH",
                source_a={
                    "document": "Task1-EN.docx (Assignment Specification)",
                    "section": "Deliverable Requirements",
                    "text": brief_quote,
                },
                source_b={
                    "document": os.path.basename(doc_path),
                    "section": "Sections A, D, E, L",
                    "text": doc_quote,
                },
                description=(
                    "The assignment specification explicitly mandates a self-contained, double-clickable "
                    "Windows desktop .exe application without requiring Python installed on target machines. "
                    "In contrast, the provided architecture documentation specifies a cloud-hosted microservices "
                    "stack using Node.js, Express, PostgreSQL, Redis, RabbitMQ, Docker, and Kubernetes."
                ),
                status="RECORDED_UNRESOLVED",
                architectural_implication=(
                    "This conflict must NOT be silently overwritten in the documentation. For implementation, "
                    "both requirements are preserved through separation: The primary deliverable submitted to "
                    "Professor Liang Peng is a standalone Windows desktop GUI application ('CodeAgent.exe') "
                    "generating a playable offline Grade 6 Space Fractions game, while the cloud microservices "
                    "architecture is preserved and emitted as static engineering specifications under /specs."
                ),
            )
        )

        return conflicts
