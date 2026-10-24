"""Browser runtime verification tests using headless Microsoft Edge.

Captures and verifies visual artifacts for:
1. Start / Briefing screen.
2. Active Question & SVG visual fraction model.
3. Multiple-choice options & feedback banner.
4. Pause overlay screen.
5. Game Over / Mission debriefing screen.
"""

import os
import subprocess
from pathlib import Path
import pytest

pytestmark = pytest.mark.host_integration

EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
WORKSPACE_DIR = str(Path(__file__).resolve().parent.parent)
HTML_PATH = os.path.join(WORKSPACE_DIR, "space_fractions", "index.html")


def capture_screen(mode: str, output_name: str) -> str:
    out_path = os.path.join(WORKSPACE_DIR, output_name)
    if os.path.exists(out_path):
        os.remove(out_path)

    query = f"?mode={mode}" if mode else ""
    url = f"file:///{HTML_PATH.replace(os.sep, '/')}{query}"

    cmd = [
        EDGE_EXE,
        "--headless=new",
        "--disable-gpu",
        f"--screenshot={out_path}",
        "--window-size=1280,800",
        url,
    ]

    res = subprocess.run(cmd, capture_output=True, timeout=15)
    assert res.returncode == 0
    assert os.path.exists(out_path), f"Failed to generate {output_name}"
    assert os.path.getsize(out_path) > 10000, f"Screenshot {output_name} is too small"
    return out_path


def test_browser_start_screen():
    path = capture_screen("", "space_fractions_screen_start.png")
    assert os.path.exists(path)


def test_browser_playing_screen():
    path = capture_screen("play", "space_fractions_screen_play.png")
    assert os.path.exists(path)


def test_browser_feedback_screen():
    path = capture_screen("answer", "space_fractions_screen_feedback.png")
    assert os.path.exists(path)


def test_browser_pause_screen():
    path = capture_screen("pause", "space_fractions_screen_pause.png")
    assert os.path.exists(path)


def test_browser_gameover_screen():
    path = capture_screen("gameover", "space_fractions_screen_gameover.png")
    assert os.path.exists(path)
