# DeepSeek Code Agent

A simplified Claude Code-style Windows desktop agent that reads architecture documents, calls the official `deepseek-v4-pro` API, and writes a complete project into a user-selected output directory. The repository includes the generated Space Fractions Grade 6 desktop game used for acceptance testing.

## Deliverable

- Standalone Windows application: `dist/CodeAgent.exe`
- No Python installation is required to run the executable.
- The GUI provides API-key configuration, architecture-file selection, task input, output-directory selection, and task execution.
- Live runs use tool calling, workspace containment, an internal `$0.80` reservation ceiling, and no blind network retries.

## Repository layout

```text
Architecture_Documentation.md  Architecture requirements input
Architecture_View.md           Kruchten 4+1 / PlantUML views input
src/                           Code Agent implementation
space_fractions/               Generated desktop-game source
specs/                         Architecture/API/data deliverables
tests/                         Core automated tests
packaging/code_agent.spec      Reproducible PyInstaller recipe
dist/CodeAgent.exe             Double-click Windows deliverable
requirements.txt               Test and packaging dependencies
IMPLEMENTATION_REPORT.md        Design rationale and reference-project study
REQUIREMENTS_COMPLIANCE.md      Requirement-to-evidence acceptance matrix
evidence/                       Sanitized final-run and release evidence
```

## Run

Double-click `dist/CodeAgent.exe`, enter a DeepSeek API key, select the two architecture files, choose an output directory, and start the task.

From source on Windows with Python 3.12+ and Tk support:

```powershell
python -m src.agent_gui
```

The API key is read from the GUI or local `.env`; `.env` is excluded from Git and from the executable.

## Test

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
```

The suite covers API parsing, reservations and the sub-one-dollar guard, multi-turn context compaction, path containment, preprocessing, synthesis, GUI dispatch, the fraction game, and executable smoke checks.

## Final verification

The submission build and its final generated project were independently verified before release:

- Final live run ID: `28c3e6b6b76f45d6815f88c10f7399b1`
- Model and transport: `deepseek-v4-pro` through the official DeepSeek HTTPS API
- Cost of the demonstrated end-to-end run: `$0.213439`
- Repository test suite: `227 passed, 3 deselected`
- Generated-project test suite: `37/37 passed`
- Randomized question stress check: `1,000/1,000 passed`
- Generated Tkinter game launch: passed
- Packaged executable five-stage smoke test: exit code `0`

The stated cost is the verified cost of the final demonstrated generation run. It is not a claim about the combined cost of every historical development or debugging run.

## Demonstration Video

A complete walkthrough demonstrating the Windows desktop agent, architecture input parsing, live authorization controls, generated code artifacts, and interactive gameplay of the Space Fractions desktop client is available on YouTube:

- **YouTube Demonstration:** [https://youtu.be/U1p9AeE_6p8](https://youtu.be/U1p9AeE_6p8)

## Rebuild

The recipe expects a verified Tcl/Tk 8.6 runtime. Set `CODE_AGENT_TK_RUNTIME_ROOT` if it is not installed at `C:\msys64\mingw64`.

```powershell
pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/code_agent.spec
```

Current executable SHA-256:

```text
F07BC5EA25ABE010246B5AEA6212ACB5372DA1BFA56CEA08373BEE61F0512F87
```

## Safety boundaries

- Writes are restricted to the selected workspace.
- Host command execution is disabled during live synthesis.
- Requests are reserved before transmission against the local `$0.80` ceiling.
- Canonical eight-file generation allows 24 bounded turns and up to three retries per deliverable so truncated writes can be recovered without weakening the financial guard.
- Ambiguous network failures lock their reservation rather than treating usage as zero.
- Historical context is compacted before the 8,000-token input guard; the complete audit transcript remains local.

The local ceiling is an application safeguard, not a provider-enforced billing cap.
