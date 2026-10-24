# DeepSeek Autonomous Code Agent — Offline Development Build

**Desktop Application Shell & Architecture Preprocessing Engine**  
*Task 1 Submission Component | Prof. Liang Peng Master's Supervision Evaluation*

---

## 1. Quick Launch Instructions

This distribution provides two native Windows packaging formats built using Python 3.12.10 and PyInstaller 6.22.3:

### Option A: Standalone Executable (Recommended)
- **Path:** `dist/onefile/CodeAgent_Offline.exe`
- **File Size:** ~11.65 MB
- **Launch:** Double-click [`CodeAgent_Offline.exe`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/dist/onefile/CodeAgent_Offline.exe) in Windows Explorer.
- **Behavior:** Unpacks bundled Tkinter runtime and assets into a temporary directory (`sys._MEIPASS`) and immediately displays the desktop interface.

### Option B: Folder Distribution (Onedir Fallback)
- **Path:** `dist/onedir/CodeAgent_Offline_onedir/CodeAgent_Offline.exe`
- **Launch:** Double-click [`CodeAgent_Offline.exe`](file:///c:/Users/Syed%20Faraz%20Zain/Downloads/task-1/Task1/dist/onedir/CodeAgent_Offline_onedir/CodeAgent_Offline.exe) within the folder.
- **Behavior:** Launches directly without initial unpack latency, reading dependencies and assets from `_internal/`.

---

## 2. Distinction: Code Agent vs. Space Fractions Game

Please note the clear architectural distinction between the two deliverables:

1. **The Code Agent Desktop Application (`CodeAgent_Offline.exe`):**
   - A native Windows desktop GUI shell designed to ingest software architecture documentation (`Architecture_Documentation.md` and `Architecture_View.md`), parse all 11 numbered view items and 13 PlantUML diagram specifications, record architectural conflicts (e.g. desktop `.exe` vs. cloud microservices), and orchestrate generation pipelines under strict budget constraints.
   - **Current Build Mode:** Explicitly operates in **Offline Development Mode** using the verified specification preprocessor and mock staged pipeline. Zero live DeepSeek tokens are billed during execution.

2. **The Space Fractions Game (`space_fractions/`):**
   - A separate, standalone, client-side Grade 6 educational mathematics game.
   - Designed to run independently in any modern browser (`space_fractions/index.html`) with zero dependencies, procedural Web Audio sound, and dynamic SVG visual models.
   - Bundled inside the Code Agent distribution under `space_fractions/` strictly for **distribution integrity and offline inspection**. The Code Agent does not execute game scripts or browser sessions.

---

## 3. Bundled Assets & Resource Resolution

Both executables embed all project architecture specifications and game assets via PyInstaller data bundling:
- Canonical inputs: `Architecture_Documentation.md` (10,454 B), `Architecture_View.md` (4,242 B)
- Game files: `space_fractions/index.html`, `style.css`, `fraction_engine.js`, `game.js`, `fraction_math.py`, `README.md`, `TRACEABILITY.md`
- Specifications: `specs/openapi.yaml`, `specs/internal.proto`, `specs/k8s/`, `specs/sql/`, `specs/traceability_matrix.csv`

The application uses dynamic resource resolution (`get_resource_path()` supporting `sys._MEIPASS` and local source trees), allowing input files and bundled resources to be resolved automatically regardless of where the binary is launched.

---

## 4. Security & Safety Boundaries

- **No Secrets Embedded:** The binary excludes `.env`, API keys, local credentials, and development test caches.
- **API Key Field:** When launching the GUI, the API key field is masked (`*`). Auto-loading from a local `.env` is supported if present on the host, but keys are never printed, logged, or exported.
- **Budget Protection:** Real-time telemetry cards reflect the verified Gate E live spend (**$0.000186**) and enforce the **$0.80** cumulative project budget ceiling.
- **Code Execution Quarantine:** AST and compilation checks validate code syntax only; generated code or test suites are never executed on the host system.

---

## 5. Explicit Limitations of this Stage F4A Build

1. **Simulated Generation Only:** Live DeepSeek V4 Pro API calling is not activated in this development build. The "Start Offline Validation" button executes the local preprocessor and staged mock pipeline.
2. **Read-Only Game Verification:** Tab 4 ("Bundled Assets & Specs") verifies that game files exist and are readable; it does not launch the browser. To play the game, launch `space_fractions/index.html` directly in Edge or Chrome.
3. **AST Syntax Checking:** Python files generated into the destination directory are checked with `ast.parse` and `py_compile`, but are not executed.
