@echo off
title Wuhan University Task 1 - Demo Walkthrough Automation
color 0A
cls
echo ===============================================================================
echo       WUHAN UNIVERSITY - TASK 1 DEMO WALKTHROUGH AUTOMATION
echo ===============================================================================
echo.
echo  This script orchestrates your live 5-minute video demonstration.
echo  Before pressing any key:
echo    1. Start your screen recorder (Press Win + Shift + R or open OBS/Game Bar).
echo    2. Keep this window visible or side-by-side with your desktop.
echo    3. Read the teleprompter lines displayed at each stage.
echo.
echo ===============================================================================
pause
cls

echo ===============================================================================
echo  SCENE 1: CODE AGENT DESKTOP GUI & CONFIGURATION (0:00 - 1:00)
echo ===============================================================================
echo.
echo  TELEPROMPTER VOICE-OVER:
echo  "Here is CodeAgent.exe, our standalone Windows desktop application developed
echo   for Task 1. It requires no pre-installed Python runtime on the target machine.
echo   The GUI provides masked API-key entry, file selectors for the architecture
echo   inputs, and destination controls. When Live DeepSeek Agent mode is selected,
echo   it requires explicit user authorization and enforces an internal $0.80 ceiling
echo   with zero automatic network retries."
echo.
echo  Launching dist\CodeAgent.exe now...
echo.
start "" "%~dp0dist\CodeAgent.exe"
echo  [CodeAgent.exe is running]
echo  - Point your mouse to the masked API Key field
echo  - Point to Architecture_Documentation.md and Architecture_View.md
echo  - Switch mode to "Live DeepSeek Agent" to show the Authorization checkbox
echo.
pause
cls

echo ===============================================================================
echo  SCENE 2: VERIFIED LIVE RUN ARTIFACTS & MANIFEST (1:00 - 2:00)
echo ===============================================================================
echo.
echo  TELEPROMPTER VOICE-OVER:
echo  "The agent preprocessed Kruchten 4+1 architecture views and PlantUML diagrams,
echo   recording platform conflict CONFLICT-DELIVERABLE-PLATFORM-001. In our verified
echo   live generation run 28c3e6b6, the agent executed 24 bounded turns with context
echo   compaction, successfully synthesizing all 8 required files with an official
echo   API cost of $0.213439, well below the one-dollar budget."
echo.
echo  Opening generated run directory in Explorer and showing manifest...
start "" explorer.exe "%~dp0generated_space_fractions\space_fractions_run_20261028_235118_28c3e6b6"
echo.
echo -------------------------------------------------------------------------------
type "%~dp0generated_space_fractions\space_fractions_run_20261028_235118_28c3e6b6\offline_validation_manifest.json"
echo.
echo -------------------------------------------------------------------------------
echo.
pause
cls

echo ===============================================================================
echo  SCENE 3: PLAYING THE GENERATED SPACE FRACTIONS DESKTOP GAME (2:00 - 3:30)
echo ===============================================================================
echo.
echo  TELEPROMPTER VOICE-OVER:
echo  "Now we launch the generated Space Fractions Grade 6 educational game.
echo   Built natively with Python Tkinter, it avoids web fallbacks and microservice
echo   dependencies. Let's launch an expedition, solve fraction arithmetic, view
echo   the immediate step-by-step feedback, pause and resume the mission, and view
echo   the final mission debrief score."
echo.
echo  Launching Space Fractions Desktop Game now...
echo.
start "" python -m space_fractions.main
echo  [Space Fractions Game is running]
echo  - Click "Launch Expedition"
echo  - Answer 1-2 fraction questions to show feedback HUD and score
echo  - Press Escape or Pause to demonstrate state transition
echo  - Let shields deplete or finish to show the Game Over summary card
echo.
pause
cls

echo ===============================================================================
echo  SCENE 4: AUTOMATED TEST SUITE & VERIFICATION (3:30 - 4:15)
echo ===============================================================================
echo.
echo  TELEPROMPTER VOICE-OVER:
echo  "All components are verified by comprehensive offline test suites covering
echo   token budget accounting, multi-turn compaction, path containment, and the
echo   generated fraction math engine."
echo.
echo  Executing python -m pytest -q ...
echo.
python -m pytest -q
echo.
echo  Executing standalone executable smoke test:
python -c "import subprocess; res = subprocess.run(['dist/CodeAgent.exe', '--smoke-test'], capture_output=True, text=True); print(res.stdout)"
echo.
pause
cls

echo ===============================================================================
echo  SCENE 5: CONCLUDING RECONCILIATION & REPOSITORY LINK (4:15 - 4:45)
echo ===============================================================================
echo.
echo  TELEPROMPTER VOICE-OVER:
echo  "In conclusion, our deliverable satisfies all requirements from Professor
echo   Liang Peng: a self-contained Windows executable, official deepseek-v4-pro
echo   integration, complete desktop game, and full test suite.
echo   The provider billing dashboard confirms total 30-day account spend across
echo   all 245 requests is 5.79 Chinese Yuan (approximately 81 cents US), strictly
echo   below the one-dollar requirement.
echo   The verified release is available on GitHub at:
echo   https://github.com/farazrasul0-cmd/autonomous-code-agent-space-fractions"
echo.
echo ===============================================================================
echo  DEMO RECORDING COMPLETE!
echo  Press red STOP on your screen recorder (Win + Shift + R).
echo  Save your video and upload to YouTube as Unlisted.
echo ===============================================================================
echo.
pause
