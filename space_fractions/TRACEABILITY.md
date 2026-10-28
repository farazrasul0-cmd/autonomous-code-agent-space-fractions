# Space Fractions traceability

| Architecture concern | Implementation |
| --- | --- |
| Play Game / View Score / View Help | `main.py`, `ui_screens.py` |
| Game, Question, User concepts | `game_engine.py`, `question_bank.py` |
| Playing, Paused, Game Over states | `game_engine.py` |
| Fraction arithmetic and explanations | `fraction_math.py` |
| Desktop presentation and input | `ui_screens.py`, `main.py` |
| Automated acceptance coverage | `tests/test_fraction_game.py` |
| API, database, and deployment design | `../specs/` |

The desktop requirement takes precedence over the conflicting web/microservice wording in the architecture narrative. The generated game is therefore a local Tkinter application with in-memory state and no mandatory external services.
