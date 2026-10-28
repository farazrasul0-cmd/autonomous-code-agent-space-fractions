# Implementation Report: DeepSeek Code Agent

## 1. Assignment interpretation

The assignment asks for a simplified coding agent informed by open-source Claude Code projects. The agent must use `deepseek-v4-pro` through the official API, accept the supplied architecture documentation and architectural views, and generate a complete project. The primary deliverable is a self-contained Windows desktop `.exe` with a minimal GUI for API-key entry, input-file selection, task triggering, and output-directory selection.

The supplied architecture narrative describes a web and cloud deployment, while the assignment and the professor's clarification require a desktop application and a generated game with desktop UI. This implementation therefore treats the explicit desktop-delivery requirement as authoritative. Cloud API, database, and Kubernetes files under `specs/` preserve the supplied design context, but they are not runtime dependencies of the generated Tkinter game.

## 2. Reference-project study

The implementation was guided by three references named in the assignment:

- [UltraWorkers Claw Code](https://github.com/ultraworkers/claw-code) presents an agent harness with provider authentication, sessions, task execution, verification, health checks, Windows guidance, and deterministic test or parity workflows. The main lesson adopted here is that an agent is more than one model request: it needs a controlled harness around planning, execution, verification, and failure recovery.
- [How Claude Code Works](https://github.com/Windy3f3f3f3f/how-claude-code-works) is an independent educational architecture analysis. Its overview highlights the agent loop, a common tool interface, reinjection of tool results, context engineering and compaction, permissions, path and command safety, and observability. These ideas directly informed the bounded multi-turn loop and validation layers in this project. Because this reference is an independent analysis rather than official Anthropic documentation, it is treated as an architectural study, not as an authoritative description of proprietary internals.
- [Anthropic Claude Code](https://github.com/anthropics/claude-code) is the official public product repository. Its README describes an agentic coding tool that understands a codebase, performs routine coding tasks, explains code, and supports Git workflows from natural-language instructions. This project implements only the subset necessary for the assignment: architecture ingestion, bounded project generation, safe file writes, validation, and a desktop interface.

The result is intentionally smaller than a production coding agent. It uses one model provider, a narrow tool surface, a single selected workspace, and assignment-specific validation instead of plugins, arbitrary shell access, multi-agent collaboration, or general Git automation.

## 3. System architecture

The application is divided into six main responsibilities:

1. `src/agent_gui.py` provides the Tkinter desktop interface and background-worker coordination.
2. `src/spec_preprocessor.py` parses the Markdown documents and PlantUML blocks into bounded structured data, including the 11 view items, 13 diagrams, deliverable specifications, and detected conflicts.
3. `src/deepseek_client.py` sends authenticated HTTPS requests to the official DeepSeek `/chat/completions` endpoint with model `deepseek-v4-pro`.
4. `src/agent_loop.py` maintains the multi-turn conversation, requests one deliverable at a time, parses tool calls, dispatches safe writes, returns observations to the model, compacts history, and stops at bounded terminal conditions.
5. `src/path_security.py`, `src/budget_controller.py`, and related validators enforce workspace and financial boundaries.
6. `src/project_validator.py` and `src/synthesis_driver.py` check syntax, interfaces, imports, required files, and integration contracts before promoting staged output.

The Windows release is produced with PyInstaller using `packaging/code_agent.spec`. Python, the application modules, architecture inputs, the example game, and the verified Tcl/Tk runtime are bundled into `dist/CodeAgent.exe`, so the target machine does not require a pre-installed Python runtime.

## 4. Agent loop and tool calling

The agent follows a simplified observe-and-act cycle:

1. Validate the task, selected files, output directory, live authorization, and available budget.
2. Preprocess the architecture documents into compact structured context.
3. Send the task and next required deliverable to DeepSeek.
4. Parse the assistant response and its `write_file` tool call.
5. Validate the target path, file size, syntax, and deliverable-specific contract.
6. Write accepted content into an isolated staging directory.
7. Append a compact tool result to the conversation and continue with the next missing deliverable.
8. Run final static integration validation and atomically promote the staging directory only when the required project is complete.

Canonical Space Fractions generation is bounded to 24 turns, with at most three retries per deliverable. The extra turns are recovery capacity for truncated or malformed model responses; they do not bypass the financial ceiling or validation gates. Long historical tool content is compacted before later requests, while the complete local audit log remains available outside the model context.

## 5. Security and containment

The live agent is deliberately less permissive than a general-purpose terminal coding agent:

- Files may be written only beneath the user-selected workspace.
- Traversal, absolute-path escapes, canonical-input overwrites, unsafe links, and oversized writes are rejected.
- Model-generated host commands are disabled during live synthesis.
- The API key is masked in the GUI, redacted from diagnostic text, excluded by `.gitignore`, and not bundled into the executable.
- Live API use requires explicit opt-in in the GUI.
- Generated work remains in a staging directory until acceptance checks pass.
- Failed or cancelled runs cannot overwrite an existing promoted output.

Static parsing and compilation checks are not described as security sandboxes. The agent does not automatically execute untrusted generated code during live synthesis. Runtime tests and the generated desktop launch were performed independently for the final release.

## 6. Financial control

The assignment requires DeepSeek token cost below one US dollar. The application uses a stricter local ceiling of `$0.80` per run. Before every request, `BudgetController` reserves a conservative upper bound based on the bounded input and maximum output tokens. A request is refused before network transmission if the reservation would exceed the ceiling. Verified provider usage reconciles the reservation after a successful response; ambiguous network failures retain a conservative locked amount rather than assuming zero cost.

The final demonstrated end-to-end run cost `$0.213439`, which is below the assignment limit. This number describes the final evidence run and is not presented as the combined cost of every historical development or debugging run. The local ceiling is an application safeguard, not a provider-enforced billing cap.

## 7. Generated deliverable

The final live run generated a standalone Space Fractions Grade 6 desktop project containing:

- fraction arithmetic and randomized question generation;
- game state, scoring, lives, pause/resume, and game-over behavior;
- a curated Grade 6 question bank;
- Tkinter fraction visualization and HUD widgets;
- a complete Tkinter application entry point;
- `requirements.txt`;
- a project README;
- unit tests.

The desktop interpretation avoids mandatory web servers, databases, containers, or cloud services. The supplied cloud artifacts are retained as traceability and design material only.

## 8. Verification results

The release was checked at several levels:

- Repository suite: `227 passed, 3 deselected`.
- Final generated-project suite: `37/37 passed`.
- Randomized generator stress check: `1,000/1,000 passed`.
- Required module import check: passed.
- Generated Tkinter game launch: passed.
- Packaged executable five-stage smoke test: exit code `0`.
- Release size and SHA-256: matched `dist/BUILD_MANIFEST.json`.

The final successful live run used ID `28c3e6b6b76f45d6815f88c10f7399b1`, called the official DeepSeek API, generated all eight required files, and passed static acceptance before promotion.

## 9. Limitations and trade-offs

- The agent is assignment-focused rather than a general repository assistant.
- Arbitrary shell execution is disabled, so live synthesis cannot perform an automatic execute-test-repair loop.
- Static acceptance does not guarantee semantic correctness; independent runtime verification remains necessary.
- Model output can be truncated or malformed. Bounded retries improve recovery but can increase latency.
- The budget ledger protects an individual application run; it is not a replacement for provider account-level billing controls.
- The executable is unsigned, so Windows may display the normal warning shown for an unknown publisher.

These restrictions reduce generality but make the submitted system easier to audit, safer to demonstrate, and aligned with the assignment's narrow generation task.

## 10. Requirement summary

The project provides the required official-model integration, Windows desktop GUI, double-click executable, no-Python target deployment, architecture-file inputs, task and destination controls, complete generated source, dependency manifest, README, and tests. The generated game is a desktop application rather than the permitted lower-scoring web fallback. The final demonstrated generation cost is below one dollar.

