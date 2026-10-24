# DeepSeek Autonomous Code Agent & Space Fractions

**Course Assignment:** Simplified Code Agent Implementation & Space Fractions Educational Game  
**Target LLM:** DeepSeek V4 Pro (`deepseek-v4-pro`) via Official API (`https://api.deepseek.com/chat/completions`)  
**Primary Deliverable:** Native Windows Desktop Application (`.exe`, double-click run)  
**Secondary Deliverable:** Grade 6 Interactive Mathematics Game (*Space Fractions*)  

---

## 1. Project Purpose & Assignment Scope

This project implements a simplified, autonomous **Code Agent** informed by research into open-source Claude Code architecture patterns (specifically [ClawCode](https://github.com/ultraworkers/clawcode), [How Claude Code Works](https://github.com/Windy3f3f3f3f/howclaudecodeworks), and [Anthropic Claude Code](https://github.com/anthropics/claudecode)). 

The Code Agent is delivered as a native Windows desktop GUI application that:
1. Ingests software architectural documentation ([`Architecture_Documentation.md`](Architecture_Documentation.md)) and structured UML views ([`Architecture_View.md`](Architecture_View.md)).
2. Preprocesses documentation and 11 Kruchten 4+1 architectural view items (13 PlantUML diagrams) into structured JSON representations without mutating source files.
3. Formulates a deterministic, staged generation plan within strict financial boundaries.
4. Orchestrates code synthesis via the official DeepSeek API (`deepseek-v4-pro`) using tool calling, preflight token-budget reservations, and strict workspace containment.
5. Synthesizes the target project: **Space Fractions**, an interactive educational mathematics game targeting Grade 6 Common Core fraction standards (CCSS.MATH.CONTENT.6.NS).

---

## 2. Repository Structure

```text
Task1/
├── src/                               # Code Agent Core Engine & Desktop GUI
│   ├── agent_gui.py                   # Tkinter desktop GUI application (972 lines)
│   ├── agent_loop.py                  # Multi-turn ReAct orchestration & tool dispatch
│   ├── budget_controller.py           # Financial ceiling ($0.80) & token reservation
│   ├── command_validator.py           # Command execution allowlist & safety rules
│   ├── deepseek_client.py             # Official DeepSeek API client (standard library urllib)
│   ├── doc_extractor.py               # Markdown and PlantUML diagram parser
│   ├── generator_pipeline.py          # Staged generation planning & offline coordinator
│   ├── path_security.py               # Workspace jailing & canonical file protection
│   ├── response_parser.py             # LLM response validation & tool-call parser
│   ├── spec_preprocessor.py           # Architecture preprocessor & conflict detection
│   └── synthesis_driver.py            # Bounded multi-turn synthesis orchestration
├── space_fractions/                   # Space Fractions Target Educational Game
│   ├── index.html                     # Responsive HTML5 game canvas & HUD
│   ├── style.css                      # Pure CSS3 space theme (zero external fonts)
│   ├── fraction_engine.js             # JavaScript math engine & SVG visual renderers
│   ├── game.js                        # State machine (Playing/Paused/GameOver) & audio
│   ├── fraction_math.py               # Python mathematical engine (live-synthesized)
│   ├── fraction_math.py.bak           # Rollback safety backup of pristine reference
│   ├── README.md                      # Game-specific guide & curriculum mapping
│   └── TRACEABILITY.md                # Architectural traceability to 11 view items
├── specs/                             # Enterprise Specifications (Section L Deliverables)
│   ├── architecture.md                # Enterprise architecture & conflict traceability
│   ├── openapi.yaml                   # OpenAPI 3.0 external REST contract (/play)
│   ├── internal.proto                 # Protobuf internal RPC definition (GameService)
│   ├── k8s/spacefractions-deployment.yaml # Kubernetes 3-replica cloud deployment
│   ├── sql/game_ddl.sql               # PostgreSQL schema DDL (games table)
│   └── traceability_matrix.csv        # Section L requirement traceability CSV
├── packaging/                         # Standalone Packaging Configurations
│   ├── code_agent_onefile.spec        # PyInstaller specification for single-file .exe
│   └── code_agent_onedir.spec         # PyInstaller specification for folder distribution
├── dist/                              # Packaged Executable Distribution
│   ├── onefile/CodeAgent_Offline.exe  # Standalone Windows executable (~11.65 MB)
│   ├── onedir/CodeAgent_Offline_onedir/ # Folder distribution fallback
│   ├── BUILD_MANIFEST.json            # Build metadata, compiler version, and hashes
│   └── README.md                      # Standalone distribution notes
├── tests/                             # Automated Test Suites (19 test files)
│   ├── test_fraction_engine.py        # Verified unit tests for Python fraction engine
│   ├── test_js_py_consistency.py      # Cross-engine consistency tests (Node.js vs Python)
│   ├── test_agent_gui.py              # Desktop GUI initialization & control tests
│   ├── test_agent_loop.py             # ReAct loop & adapter integration tests
│   ├── test_agent_pipeline_integration.py # Pipeline coordinator & tool-dispatch tests
│   ├── test_budget_controller.py      # Token reservations & cost accounting tests
│   ├── test_deepseek_client.py        # DeepSeek API client mock tests
│   ├── test_packaging_distribution.py # PyInstaller executable validation tests
│   └── ...                            # Additional unit and preflight test suites
├── Architecture_Documentation.md      # Canonical architectural specification
├── Architecture_View.md               # Canonical Kruchten 4+1 PlantUML views
├── pytest.ini                         # Pytest configuration (filters host_integration)
├── requirements.txt                   # Dependency manifest
└── README.md                          # This document
```

---

## 3. Code Agent Architecture & DeepSeek API Integration

The Code Agent adheres strictly to Claude Code multi-turn ReAct principles:
1. **ReAct Cycle:** `AgentLoop` maintains conversation history, sends structured system instructions, inspects model responses, extracts tool calls (`write_file`, etc.), dispatches execution within workspace boundaries, and appends observation results back to context.
2. **Official Model Target:** Strictly uses model `deepseek-v4-pro` through the official endpoint `https://api.deepseek.com/chat/completions`.
3. **Non-Thinking Mode:** Explicitly sets `thinking: {"type": "disabled"}` in API payloads to ensure deterministic code generation without runaway reasoning token overhead.
4. **Standard Library Transport:** `DeepSeekClient` uses Python's standard library `urllib.request` and `http.client`, eliminating external networking dependencies.
5. **Zero Automatic Retries:** Network errors or rate limits immediately halt execution and lock reservations, preventing runaway billing loops.

---

## 4. Windows Desktop Executable Launch Instructions

The primary assignment deliverable is a self-contained Windows desktop executable that runs without requiring Python or pip packages to be installed on target machines.

### Option A: Standalone Executable (Recommended)
- **Path:** `dist/onefile/CodeAgent_Offline.exe`
- **File Size:** ~11.65 MB
- **Launch:** Double-click `CodeAgent_Offline.exe` in Windows Explorer.
- **Behavior:** Unpacks bundled runtime into `sys._MEIPASS` and displays the GUI.

### Option B: Folder Distribution (Onedir Fallback)
- **Path:** `dist/onedir/CodeAgent_Offline_onedir/CodeAgent_Offline.exe`
- **Launch:** Double-click `CodeAgent_Offline.exe` within the folder.
- **Behavior:** Launches directly without unpack latency, loading assets from `_internal/`.

> [!WARNING]
> **Prebuilt Artifact Freshness Notice:**
> The executables currently present in `dist/` were built on 2026-10-23 (`BUILD_MANIFEST.json`). While fully functional for GUI and preprocessor execution, they represent a pre-Phase-2.11 build and have not yet been rebuilt to bundle the recently integrated `fraction_math.py`. Executable rebuilding is scheduled for Phase 2.15.

---

## 5. Running from Source

For developers running directly from the source repository:

### Prerequisites
- Windows 10/11 (AMD64)
- Python 3.12+ (tested with Python 3.12.10) with Tk/Tcl support enabled during installation.

### Launch Command
```powershell
python src/agent_gui.py
```

### Supported CLI Flags (Automated Verification)
- `--auto-run`: Automatically starts offline validation on GUI launch.
- `--auto-close-ms <milliseconds>`: Automatically closes the window after the specified duration (used in automated testing).
- `--doc-file <path>`: Specifies custom architecture documentation path.
- `--view-file <path>`: Specifies custom architecture view path.
- `--output-dir <path>`: Specifies custom target output directory.

---

## 6. API Key & Security Configuration

The Code Agent requires a DeepSeek API key for live generation turns:

1. **GUI Configuration:** Enter the key in the "DeepSeek API Key" entry field. The input is masked with `*` and is never printed to logs or exported.
2. **Environment Variable:** Alternatively, set `DEEPSEEK_API_KEY` in your environment:
   ```powershell
   $env:DEEPSEEK_API_KEY="sk-your-deepseek-api-key-placeholder"
   ```
3. **Local `.env` Auto-Load:** The GUI will detect and auto-load `DEEPSEEK_API_KEY` from a local `.env` file in the repository root if present.
4. **Secret Quarantine:** API keys are never embedded into compiled binaries, never committed to git (`.gitignore`), and never written to output manifests.

---

## 7. Input Files & Output Directory Behavior

### Input Documents
- **`Architecture_Documentation.md`:** Outlines system scope, component responsibilities (`GameComponent`, `QuestionComponent`, `UserComponent`), enterprise stacks, and Section L deliverables.
- **`Architecture_View.md`:** Outlines 11 Kruchten 4+1 view items and 13 PlantUML diagrams.

### Preprocessing & Output Behavior
- The agent preprocessor extracts view items, entities (actors, classes, states, components, deployment nodes), and explicit requirements into structured in-memory data structures without altering the input files.
- All generated files are confined strictly to the designated output directory (default: `generated_space_fractions/`).
- `PathSecurity` rejects any attempt to write outside the workspace jail, access parent directories (`..`), or overwrite canonical input files. Individual file writes are capped at 1 MB.

---

## 8. Space Fractions Game Launch Instructions

Space Fractions is a client-side, zero-dependency educational mathematics game designed to run directly in modern web browsers:

### How to Play
1. Navigate to `space_fractions/` in Windows Explorer.
2. Double-click `index.html`.
3. The game launches immediately in your default browser (Microsoft Edge, Google Chrome, Mozilla Firefox).

### Features
- **100% Offline:** Zero external CDN scripts, zero npm packages, zero external web fonts.
- **Procedural Sound:** Built-in Web Audio API synthesizer for mission sounds.
- **Visual Fraction Models:** Dynamic inline SVG pie charts and fraction bars.
- **State Machine:** Full adherence to Kruchten Logic View `StateDiagram` (`Playing <-> Paused`, `Playing -> GameOver`).

### Relationship to Python Mathematical Engine
The game logic in `space_fractions/game.js` is paired with [`space_fractions/fraction_math.py`](space_fractions/fraction_math.py), a Python reference mathematical engine that was **synthesized live by DeepSeek V4 Pro** via the official API in Phase 2.5C and integrated in Phase 2.11. The Python engine passes 18/18 tests, confirming identical mathematical behavior, GCD reduction, and deterministic question generation between Python and JavaScript.

---

## 9. Dependency Installation

### Installing Development Dependencies
```powershell
pip install -r requirements.txt
```

> [!NOTE]
> **Clean-Environment Status:** Clean-environment installation via `requirements.txt` has not yet been executed in this audit phase to maintain strict offline isolation. The manifest is derived directly from static AST inspection of repository imports.

---

## 10. Automated Testing Instructions

The repository provides 19 automated test suites with a strict distinction between offline unit tests and host-integration tests:

### Running Default Offline Tests
By default, `pytest.ini` excludes tests marked `host_integration` so that tests can be executed anywhere without host subprocess dependencies:
```powershell
python -m pytest -v
```

### Running Host-Integration Tests
Host-integration tests spawn host subprocesses (such as `msedge.exe` for browser screenshots, `node.exe` for JS-Python parity, or `CodeAgent_Offline.exe` for packaging validation):
```powershell
# Run fraction math engine unit and cross-engine consistency tests:
python -m pytest -m host_integration tests/test_fraction_engine.py tests/test_js_py_consistency.py -v

# Run packaged executable verification tests:
python -m pytest -m host_integration tests/test_packaging_distribution.py -v
```

---

## 11. Budget & Financial Safety Controls

To comply with course financial constraints, the Code Agent enforces a multi-layer defense-in-depth safety mechanism:
- **Internal Hard Ceiling:** **$0.800000 USD** cumulative project spend.
- **Current Verified Spend:** **$0.027616 USD** across 4 authorized API calls (Canary: $0.000186, Attempt 1: $0.005779, Attempt 2: $0.010874, Attempt 3: $0.010777). Headroom remaining: **$0.772384 USD** (96.55% remaining).
- **Preflight Token Reservation:** Before each API dispatch, funds are reserved using worst-case formulas:
  $$\text{Hold} = (\text{Input Token Bound} \times \$0.00000132) + (\text{Max Output Tokens} \times \$0.00000396)$$
  If the reservation would exceed the ceiling, the turn is blocked before sending HTTP packets.
- **Code Execution Quarantine:** Model-generated code is never executed during automated generation pipelines. Syntax validity is checked via `ast.parse` and `py_compile` only.

---

## 12. Known Gaps & Incomplete Functionality

Based on static inspection, the following items remain to be completed in subsequent phases:
1. **Python Game Engine & Question Bank:** `space_fractions/game_engine.py` and `space_fractions/question_bank.py` (planned in generator pipeline Stage 2) are currently unwritten. The educational game currently runs via `space_fractions/game.js` and `fraction_engine.js`, with math validated by `fraction_math.py`.
2. **Packaged Binary Refresh:** The executable in `dist/` is prebuilt from 10/23/2026 and must be recompiled via PyInstaller in Phase 2.15 to bundle the live-synthesized `fraction_math.py` and updated manifests.
3. **Optional Dockerfile:** A `Dockerfile` has not been authored (explicitly optional in assignment specifications).

---

## 13. Packaging & Demonstration Readiness

- **Packaging Specs:** Authoritative PyInstaller specs reside in `packaging/code_agent_onefile.spec` and `code_agent_onedir.spec`.
- **Visual Artifacts:** 5 verified screenshots captured from headless Microsoft Edge are stored in the root directory:
  - `space_fractions_screen_start.png`: Start / Mission Briefing Screen
  - `space_fractions_screen_play.png`: Active Question & SVG Fraction Pie Screen
  - `space_fractions_screen_feedback.png`: Step-by-Step Educational Explanation Screen
  - `space_fractions_screen_pause.png`: Mission Paused Overlay
  - `space_fractions_screen_gameover.png`: Mission Debriefing & Final Score Screen
- **Video Demonstration:** A video demonstration script covering desktop launch, document selection, generation pipeline execution, and game play will be recorded following the binary refresh in Phase 2.15.
