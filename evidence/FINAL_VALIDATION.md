# Final Validation Record

## Release identity

- Application: DeepSeek Code Agent
- Platform: Windows 10/11 x64
- Executable: `dist/CodeAgent.exe`
- Size: `13,314,848` bytes
- SHA-256: `63d9bc59571de4a1566e8f435506c0caa20e059d6945a943ed27669ea265e024`
- PyInstaller: `6.22.3`

The executable size and hash were recomputed after the final build and match `dist/BUILD_MANIFEST.json`.

## Final live generation

- Run ID: `28c3e6b6b76f45d6815f88c10f7399b1`
- Mode: `LIVE_DEEPSEEK_AGENT`
- Model: `deepseek-v4-pro`
- Endpoint class: official DeepSeek HTTPS API
- Verified run cost: `$0.213439`
- Architecture view items extracted: `11`
- PlantUML diagrams extracted: `13`
- Recorded platform conflict: `CONFLICT-DELIVERABLE-PLATFORM-001`
- Required generated files: `8/8`
- Static acceptance: passed
- Output promotion: passed

The cost above is the verified cost of this final demonstrated run. It is not presented as the combined cost of all historical development runs.

## Generated files

1. `README.md`
2. `requirements.txt`
3. `space_fractions/fraction_math.py`
4. `space_fractions/game_engine.py`
5. `space_fractions/main.py`
6. `space_fractions/question_bank.py`
7. `space_fractions/ui_screens.py`
8. `tests/test_fraction_game.py`

## Independent post-run verification

The live agent performs static validation before promotion and does not automatically execute untrusted generated code. After promotion, the final output was independently checked:

| Check | Result |
| --- | --- |
| Generated unit tests | `37/37 passed` |
| Python compilation | Passed |
| Required module imports | Passed |
| Randomized question generation | `1,000/1,000 passed` |
| Option type and correct-index contract | Passed |
| Generated Tkinter desktop launch | Passed |
| Placeholder and unsafe sibling-import scan | Passed |

The independent checks supplement, rather than alter, the original live-run manifest. The original manifest correctly records that generated code was not executed inside the live synthesis process.

## Repository and package verification

| Check | Result |
| --- | --- |
| Repository suite | `227 passed, 3 deselected` |
| PyInstaller build | Passed |
| Packaged five-stage smoke test | Exit code `0` |
| Executable count under `dist/` | `1` |
| Credentials bundled | No |
| `.env` tracked | No |

## Reproduction commands

From a source checkout with the development dependencies installed:

```powershell
python -m pytest -q
```

Generated-project tests:

```powershell
cd <generated-run-directory>
python -m unittest discover -s tests -p "test_*.py" -v
```

Packaged smoke test:

```powershell
Start-Process -FilePath ".\dist\CodeAgent.exe" -ArgumentList "--smoke-test" -Wait -PassThru
```

The smoke test is offline and does not require or transmit an API key.

