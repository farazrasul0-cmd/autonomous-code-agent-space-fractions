# Submission Evidence

This directory contains a minimal, sanitized record of the final verified release. It intentionally excludes API keys, `.env` files, authorization headers, raw request payloads, and full development logs.

## Evidence files

- [`FINAL_VALIDATION.md`](FINAL_VALIDATION.md): human-readable verification record and reproduction commands.
- [`final_run_manifest.sanitized.json`](final_run_manifest.sanitized.json): machine-readable facts from the final live run plus clearly separated independent post-run checks.
- [`code_agent_gui.png`](code_agent_gui.png): packaged CodeAgent startup state with an empty API-key field and no live request in progress.
- [`generated_game_play.png`](generated_game_play.png): representative Space Fractions desktop gameplay screen.
- [`screenshots/`](screenshots/): screenshots illustrating key Space Fractions gameplay states (start briefing, active play, pedagogical feedback, pause overlay, and debrief game over).

The original local run directory is excluded from Git because it contains verbose development artifacts. This directory retains only the evidence necessary to evaluate the submission.

