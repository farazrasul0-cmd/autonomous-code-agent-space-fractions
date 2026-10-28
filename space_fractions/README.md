# Space Fractions

A self-contained Grade 6 fraction-learning desktop game built with Python and Tkinter.

## Run

```powershell
python -m space_fractions.main
```

No third-party runtime packages are required.

## Modules

- `fraction_math.py`: fraction arithmetic and question generation
- `game_engine.py`: score, progress, pause/resume, and game-over state
- `question_bank.py`: Grade 6 fraction questions
- `ui_screens.py`: Tkinter screens, HUD, and fraction visualizations
- `main.py`: desktop application entry point

The game implements the local desktop interpretation of the supplied Kruchten 4+1 views. Cloud APIs, Kubernetes, and SQL remain design artifacts under `specs/`; the playable game does not require network services.
