# Architecture realization

## System context

The submission contains two local Windows desktop components:

1. `dist/CodeAgent.exe`: the DeepSeek-powered code-agent workbench.
2. `space_fractions/`: the generated Grade 6 fraction game implemented with Python and Tkinter.

The desktop delivery requirement is treated as authoritative where the source documentation conflicts with its web and cloud examples.

## Code Agent

- `agent_gui.py` provides API-key, input-file, output-directory, and task controls.
- `spec_preprocessor.py` turns the architecture inputs into structured data.
- `agent_loop.py` coordinates bounded multi-turn tool calls and validates deliverables.
- `deepseek_client.py` calls the official `deepseek-v4-pro` HTTPS API.
- `budget_controller.py` reserves worst-case request cost under the local $0.80 ceiling.
- `path_security.py` confines reads and writes to the selected workspace.

## Generated game

- `fraction_math.py`: arithmetic and question generation.
- `game_engine.py`: Playing, Paused, and Game Over state transitions.
- `question_bank.py`: Grade 6 content.
- `ui_screens.py`: desktop presentation, HUD, and visual fractions.
- `main.py`: integrated Tkinter entry point.

## Design artifacts

- `openapi.yaml`: external interface specification.
- `internal.proto`: internal service contract.
- `sql/game_ddl.sql`: persistence schema.
- `k8s/spacefractions-deployment.yaml`: optional cloud deployment design.
- `traceability_matrix.csv`: requirement-to-artifact mapping.

These service artifacts document the supplied architecture but are not runtime dependencies of the standalone desktop game.

## Quality and safety

- Automated tests cover domain behavior, preprocessing, API response parsing, reservations, workspace containment, synthesis, GUI behavior, and packaging.
- Host command execution is disabled in the live agent.
- The complete transcript is retained for audit while request history is compacted to stay within the input guard.
- API credentials are never packaged and `.env` is Git-ignored.
