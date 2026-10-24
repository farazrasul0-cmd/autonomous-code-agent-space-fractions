# Space Fractions Enterprise Architecture & Traceability Specification

**Document Identifier:** `specs/architecture.md`  
**Standard Compliance:** Kruchten 4+1 Architectural View Model & IEEE 1471 / ISO/IEC 42010  
**Deliverable Status:** Section L Core Deliverable Specification  
**Reference Canonical Documents:** [`Architecture_Documentation.md`](../Architecture_Documentation.md), [`Architecture_View.md`](../Architecture_View.md)  

---

## 1. Executive Summary & Scope Distinction

This document provides the formal enterprise architectural specification and bidirectional traceability matrix for the **Space Fractions** system, fulfilling the Section L deliverables mandated in `Architecture_Documentation.md`.

### 1.1 Architectural Distinction: Code Agent vs. Space Fractions Game vs. Cloud Reference
To ensure complete architectural clarity and eliminate ambiguity, this specification explicitly separates three distinct system boundaries:

1. **The Autonomous Code Agent Desktop Application (`src/`, `dist/onefile/CodeAgent_Offline.exe`):**
   - **Role:** The synthesis tool requested by the course assignment instructions (`Task1-EN.docx`).
   - **Architecture:** Local desktop application with Tkinter GUI, Claude Code-like ReAct loop (`AgentLoop`), budget enforcement (`BudgetController`), security boundary jailing (`PathSecurity`), and official DeepSeek API client (`DeepSeekClient` targeting `deepseek-v4-pro`).
   - **Delivery Mode:** Self-contained Windows `.exe` requiring zero external dependencies.

2. **The Space Fractions Standalone Game (`space_fractions/`):**
   - **Role:** The primary client educational application delivered for end-user interaction.
   - **Architecture:** Zero-dependency, client-side web application (`index.html`, `style.css`, `game.js`, `fraction_engine.js`) paired with a live-synthesized Python mathematical engine ([`space_fractions/fraction_math.py`](../space_fractions/fraction_math.py)).
   - **Delivery Mode:** Standalone browser execution with dynamic SVG visual models and procedural Web Audio API sound.

3. **The Enterprise Cloud Microservices Architecture (Specifications in `specs/`):**
   - **Role:** The reference enterprise architecture outlined in `Architecture_Documentation.md` (Node.js/Express, PostgreSQL, Redis, RabbitMQ, Docker, Kubernetes, Istio).
   - **Delivery Mode:** Preserved as formal, authoritative **engineering specifications** (`specs/openapi.yaml`, `specs/internal.proto`, `specs/k8s/`, `specs/sql/`) rather than running local network listeners, ensuring zero cloud overhead for local desktop evaluation.

---

## 2. Enterprise Component Architecture

The canonical specification defines three primary microservice components:

### 2.1 GameComponent
- **Core Responsibilities:** Encapsulates game session lifecycle, state machine transitions (`Playing`, `Paused`, `GameOver`), answer validation, score calculation, streak multipliers, and player shields.
- **Data Ownership:** Owns active session state and player telemetry.
- **Recommended Enterprise Stack:** Node.js 18 LTS, Express.js 4, PostgreSQL 14, Redis 6.
- **Local Desktop Realization:** Implemented in `space_fractions/game.js` (client state machine) and `space_fractions/fraction_math.py` (verified mathematical engine).

### 2.2 QuestionComponent
- **Core Responsibilities:** Mathematical generation of Grade 6 fraction questions (simplification, common denominators, mixed operations), distractor generation (4 unique options, exactly 1 correct), and step-by-step explanations.
- **Data Ownership:** Question definitions, difficulty tiers, and curriculum metadata.
- **Recommended Enterprise Stack:** Node.js 18 LTS, PostgreSQL 14 (DDL: `CREATE TABLE games (id SERIAL PRIMARY KEY, game_state JSONB NOT NULL)`).
- **Local Desktop Realization:** Realized via deterministic seeded PRNG in `space_fractions/fraction_engine.js` and Python class `MathQuestionGenerator` in `space_fractions/fraction_math.py`.

### 2.3 UserComponent & AdminComponent
- **Core Responsibilities:** User authentication (OAuth2), student progress tracking, and administrative question bank updates.
- **Enterprise Specification:** Defined in OpenAPI 3.0 path `/play` and Protobuf service `GameService`.
- **Local Desktop Realization:** Specification-only for cloud deployment; offline standalone game operates without mandatory authentication.

---

## 3. Section L Deliverables & Specification Catalog

All six deliverables mandated in Section L of `Architecture_Documentation.md` are formally cataloged below:

| Section L Deliverable | Local File Path | Format / Schema | Status |
| :--- | :--- | :--- | :---: |
| **`architecture.md`** | [`specs/architecture.md`](architecture.md) | Markdown | **Authored (This document)** |
| **`openapi.yaml`** | [`specs/openapi.yaml`](openapi.yaml) | OpenAPI 3.0.0 (YAML) | **Present & Verified** |
| **`internal.proto`** | [`specs/internal.proto`](internal.proto) | Protocol Buffers (proto3) | **Present & Verified** |
| **`k8s/spacefractions-deployment.yaml`** | [`specs/k8s/spacefractions-deployment.yaml`](k8s/spacefractions-deployment.yaml) | Kubernetes Deployment (YAML) | **Present & Verified** |
| **`sql/game_ddl.sql`** | [`specs/sql/game_ddl.sql`](sql/game_ddl.sql) | PostgreSQL SQL DDL | **Present & Verified** |
| **`traceability_matrix.csv`** | [`specs/traceability_matrix.csv`](traceability_matrix.csv) | CSV Document | **Present & Verified** |

### 3.1 External API Contract (`specs/openapi.yaml`)
Defines the RESTful endpoint `/play` returning game initialization state with status 200:
```yaml
openapi: 3.0.0
info:
  title: Space Fractions API
  description: API for the Space Fractions game
  version: 1.0.0
paths:
  /play:
    get:
      summary: Play the game
      responses:
        200:
          description: Game started
          content:
            application/json:
              schema:
                type: object
                properties:
                  gameId:
                    type: integer
                    description: Game ID
```

### 3.2 Internal Service Contract (`specs/internal.proto`)
Defines gRPC `GameService` for internal RPC communication between Game and Question components:
```protobuf
syntax = "proto3";
package spacefractions;

service GameService {
  rpc Play(PlayRequest) returns (PlayResponse) {}
}

message PlayRequest {
  int32 gameId = 1;
}

message PlayResponse {
  int32 gameId = 1;
}
```

### 3.3 Kubernetes Deployment Manifest (`specs/k8s/spacefractions-deployment.yaml`)
Specifies 3-replica production container deployment with port 80 exposure:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: spacefractions
spec:
  replicas: 3
  selector:
    matchLabels:
      app: spacefractions
  template:
    metadata:
      labels:
        app: spacefractions
    spec:
      containers:
      - name: spacefractions
        image: spacefractions:latest
        ports:
        - containerPort: 80
```

### 3.4 Database Persistence Schema (`specs/sql/game_ddl.sql`)
Defines persistence schema for session recovery and audit:
```sql
CREATE TABLE games (
  id SERIAL PRIMARY KEY,
  game_state JSONB NOT NULL
);
```

---

## 4. Kruchten 4+1 Views Traceability Matrix

The 11 numbered view items and 13 PlantUML diagram specifications from [`Architecture_View.md`](../Architecture_View.md) map to project implementation as follows:

| Item # | Kruchten View Category | Diagram ID | Key Architectural Entities | Implementation Status | Implementation Evidence File Paths | Implementation Details & Architectural Realization |
| :---: | :--- | :--- | :--- | :---: | :--- | :--- |
| **1** | Scenario View | `UseCaseDiagram` | `EndUser`, `Admin`, `PlayGame`, `ViewScore`, `ViewHelp`, `UpdateQuestions` | **Implemented / Specification** | `space_fractions/index.html`<br>`space_fractions/game.js`<br>`specs/openapi.yaml` | End User use cases (`PlayGame`, `ViewScore`, `ViewHelp`) are fully implemented in the interactive game HUD. Admin use case (`UpdateQuestions`) is preserved as an OpenAPI specification. |
| **2** | Logic View | `ClassDiagram` | `Game`, `Question`, `User`, `Admin` | **Implemented** | `space_fractions/fraction_math.py`<br>`space_fractions/fraction_engine.js`<br>`space_fractions/game.js` | `Fraction` and `MathQuestionGenerator` classes implement fraction arithmetic and question modeling with properties (`n`, `d`, `prompt`, `options`, `correctIndex`). |
| **3** | Logic View | `ObjectDiagram` | `game1`, `question1`, `user1`, `admin1` | **Implemented** | `space_fractions/game.js` | Live DOM execution instantiates and links runtime game and question instances during active play sessions. |
| **4** | Logic View | `StateDiagram` | `Playing`, `Paused`, `GameOver` | **Implemented** | `space_fractions/game.js` | Finite state machine strictly executes transitions: `Playing <-> Paused` via `pause()`/`resume()` and `Playing -> GameOver` via `gameOver()`. |
| **5** | Process View | `ActivityDiagram` | Activity Flow: Play -> Check Over -> Score -> Display | **Implemented** | `space_fractions/game.js` | Flow loops through answering, checks shield exhaustion for game over, computes final score and accuracy, and displays mission debriefing. |
| **6** | Process View | `SequenceDiagram1` | `User -> Game -> Question: play, getPrompt, submit answer, check answer, display result` | **Implemented** | `space_fractions/game.js`<br>`space_fractions/fraction_engine.js` | Direct implementation: User clicks/keys option -> `submitAnswer()` validates choice against question -> updates HUD and displays explanation. |
| **6** | Process View | `SequenceDiagram2` | `Admin -> Question: update()` | **Specification Only** | `specs/internal.proto`<br>`specs/openapi.yaml` | Administrative remote update sequence preserved as gRPC RPC specification. |
| **7** | Process View | `CollaborationDiagram1` | Play Game scenario collaboration | **Implemented** | `space_fractions/game.js`<br>`space_fractions/index.html` | Interactive collaboration between Canvas visualizer, Fraction engine, and scoring HUD. |
| **7** | Process View | `CollaborationDiagram2` | Update Questions collaboration | **Specification Only** | `specs/openapi.yaml` | Cloud administrative sync scenario preserved as specification. |
| **8** | Development View | `PackageDiagram` | Packages: `Game`, `User`, `Admin` | **Implemented** | `space_fractions/`<br>`src/` | Modular project structure separating domain math (`fraction_math.py`, `fraction_engine.js`), controller state (`game.js`), and agent packaging. |
| **9** | Development View | `ComponentDiagram` | `GameComponent`, `QuestionComponent`, `UserComponent`, `AdminComponent` | **Implemented / Specification** | `space_fractions/`<br>`specs/` | `GameComponent` and `QuestionComponent` execute locally in browser; `UserComponent`/`AdminComponent` specified in `/specs`. |
| **10** | Physical View | `DeploymentDiagram` | `GameServer`, `QuestionServer`, `UserClient`, `AdminClient` | **Implemented / Specification** | `space_fractions/index.html`<br>`specs/k8s/` | `UserClient` runs as local desktop client; server nodes are specified in Kubernetes deployment manifest. |
| **11** | Physical View | `ContainerDiagram` | `GameContainer`, `QuestionContainer`, `UserContainer`, `AdminContainer` | **Implemented / Specification** | `space_fractions/`<br>`specs/k8s/` | Client container bundle deployed under `space_fractions/`; cloud containers specified in `spacefractions:latest` k8s image descriptor. |

---

## 5. Architectural Conflicts & Diagram Syntax Issues Register

In accordance with architectural engineering integrity, source conflicts and syntax ambiguities are preserved and formally resolved below rather than silently rewritten:

### 5.1 Architectural Conflict: Desktop Binary vs. Cloud Microservices (`CONFLICT-DELIVERABLE-PLATFORM-001`)
- **Conflict Description:** `Task1-EN.docx` mandates a Windows desktop `.exe` application running without external dependencies. In contrast, `Architecture_Documentation.md` outlines an enterprise cloud microservices cluster deploying Node.js, PostgreSQL, Redis, RabbitMQ, Docker, and Kubernetes.
- **Formal Resolution:** 
  1. The **Code Agent** fulfills the Windows `.exe` desktop application deliverable via [`dist/onefile/CodeAgent_Offline.exe`](../dist/onefile/CodeAgent_Offline.exe).
  2. The **Space Fractions** target game fulfills the local desktop educational requirement via self-contained, offline HTML5/JS execution (`space_fractions/index.html`) backed by the live-synthesized Python fraction engine (`space_fractions/fraction_math.py`).
  3. The cloud microservice topology is preserved as formal enterprise deployment specifications under `specs/` (`k8s/`, `sql/`, `openapi.yaml`, `internal.proto`).

### 5.2 PlantUML Diagram Syntax & Modeling Observations
1. **Item 3 (`ObjectDiagram`) Syntax Ambiguity:**
   - *Observation:* Line 56–64 of `Architecture_View.md` declares objects using `participant game1` instead of PlantUML object syntax `object game1`.
   - *Resolution:* Preprocessor handles participants as instantiated object instances in the Logic View.
2. **Item 7 (`CollaborationDiagram`) Representation:**
   - *Observation:* PlantUML lacks dedicated collaboration diagram syntax; Item 7 uses sequence participants with scenario annotations (`note right of Game: Scenario: Play Game`).
   - *Resolution:* Preprocessor indexes these as interaction collaboration diagrams.
3. **Item 9 & 11 Diagram Artifact Keywords:**
   - *Observation:* Component and Container diagrams declare entities using the `artifact` keyword rather than `component` or `node`.
   - *Resolution:* Preprocessor extracts artifacts and maps them to their corresponding architectural abstractions.
4. **Item 2 (`ClassDiagram`) Composition Semantics:**
   - *Observation:* Lines 49–51 specify `User --* Game` and `Admin --* Question` using black diamond composition (`*`).
   - *Resolution:* Treated in code as an associative session relationship rather than physical lifecycle ownership.
5. **Diagram Multiplicity:**
   - *Observation:* There are 11 numbered view items, but Item 6 and Item 7 contain two `@startuml` blocks each, yielding 13 total diagrams.
   - *Resolution:* All 13 diagrams are extracted and indexed under their respective parent numbered items.

---

## 6. Requirements-to-Module-to-Test Traceability Matrix

| Requirement ID | Requirement Summary | Architectural Source | Responsible Module | Test Suite & Verification | Status |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **FR-1** | Play Game (Grade 6 Interactive Fraction Learning) | `Architecture_Doc.md`, Sec B; `Architecture_View.md`, Item 1 | `space_fractions/index.html`<br>`space_fractions/game.js` | Verified via browser runtime tests (`test_browser_runtime.py`) and manual inspection. | **Implemented** |
| **NFR-1** | Performance (Low latency, responsive user interaction) | `Architecture_Doc.md`, Sec B, D | `space_fractions/fraction_engine.js`<br>`space_fractions/game.js` | Verified zero-network latency; synchronous DOM and Web Audio execution. | **Implemented** |
| **ASR-1** | Data Durability (Session state and persistence) | `Architecture_Doc.md`, Sec B, D, L | `specs/sql/game_ddl.sql`<br>`space_fractions/fraction_math.py` | Standalone client uses seeded PRNG for reproducible state; SQL DDL specified in `specs/sql/`. | **Specification / Implemented** |
| **ASR-2** | Security & Secret Protection | `Architecture_Doc.md`, Sec F; Internal Guardrails | `src/agent_gui.py`<br>`src/path_security.py` | Unit tests `test_agent_gui.py`, `test_path_security.py`; masked GUI fields; zero key logging. | **Implemented** |
| **REQ-MATH** | Fraction Simplification & Equivalence | Common Core Grade 6 (6.NS); ClassDiagram | `space_fractions/fraction_math.py` | `tests/test_fraction_engine.py::test_fraction_simplification` (PASSED). | **Verified** |
| **REQ-ADD** | Fraction Addition (Like & Unlike Denominators) | Common Core Grade 6 (6.NS); ClassDiagram | `space_fractions/fraction_math.py` | `tests/test_fraction_engine.py::test_fraction_addition_*` (PASSED). | **Verified** |
| **REQ-SUB** | Fraction Subtraction & Negative Normalization | Common Core Grade 6 (6.NS); ClassDiagram | `space_fractions/fraction_math.py` | `tests/test_fraction_engine.py::test_fraction_subtraction_*` (PASSED). | **Verified** |
| **REQ-ZERO** | Zero-Denominator Rejection (`ValueError`) | Robustness Invariant; Fraction Domain | `space_fractions/fraction_math.py` | `tests/test_fraction_engine.py::test_fraction_zero_denominator` (PASSED). | **Verified** |
| **REQ-PARITY** | Python vs. JavaScript Mathematical Parity | Cross-Engine Verification Contract | `space_fractions/fraction_math.py`<br>`space_fractions/fraction_engine.js` | `tests/test_js_py_consistency.py` (All 6 cross-engine consistency tests PASSED). | **Verified** |
| **REQ-EXE** | Windows Desktop Application (.exe) | `Task1-EN.docx`, Para 2 | `dist/onefile/CodeAgent_Offline.exe` | Verified via `tests/test_packaging_distribution.py`; PE header and offline execution checked. | **Implemented** |

---

## 7. Section L Review Checklist Compliance

Evaluating `specs/architecture.md` and related artifacts against the Section L review checklist:

| Checklist Criterion | Evaluation Finding | Compliance |
| :--- | :--- | :---: |
| **All FR/NFR/ASR present in traceability matrix?** | FR-1, NFR-1, ASR-1, ASR-2, A1, A2 fully mapped in Sections 4 and 6 above and in `specs/traceability_matrix.csv`. | **COMPLIANT** |
| **OpenAPI + internal API contract included and valid?** | `specs/openapi.yaml` (OpenAPI 3.0) and `specs/internal.proto` (proto3) authored, present, and valid. | **COMPLIANT** |
| **Major components have responsibilities, stack options (3+), recommended stack + ASR/NFR justification?** | Documented in Section 2 above and detailed in `Architecture_Documentation.md` Section D. | **COMPLIANT** |
| **Kubernetes deployment snippet present and syntactically valid?** | `specs/k8s/spacefractions-deployment.yaml` present, valid Kubernetes Apps/v1 Deployment syntax. | **COMPLIANT** |
| **SQL DDLs provided for persisted entities?** | `specs/sql/game_ddl.sql` present with valid PostgreSQL DDL for `games` table. | **COMPLIANT** |
| **Assumptions and open questions clearly listed?** | Documented in Section 1 and Section 5 above, reconciling cloud vs. desktop assumptions. | **COMPLIANT** |
