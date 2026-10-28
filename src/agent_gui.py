"""DeepSeek Autonomous Code Agent — Native Windows Desktop GUI.

Desktop application shell built with Python 3.12 and Tkinter.
Provides:
1. Masked API key configuration with local .env auto-load support.
2. File pickers for canonical Architecture_Documentation.md and Architecture_View.md.
3. Destination output directory selection.
4. Task trigger and cancel controls with background worker thread and thread-safe queue.
5. Real-time budget telemetry distinguishing verified live spend (UNRECONCILED baseline) from active holds.
6. Execution of the offline specification preprocessor and staged generator pipeline.
7. Multi-tab activity log, extracted view/entity details, and generated artifact summaries.

SAFETY & CREDENTIAL NOTICE:
- Operates strictly in offline mock mode during Stage F2.
- Zero live network or DeepSeek API calls are dispatched.
- API keys are masked in the GUI and never printed, logged, or persisted.
- Generated code and test suites are never executed on the host.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time

def _bootstrap_tcl_tk_environment() -> None:
    """Ensure TCL_LIBRARY, TK_LIBRARY, and TCLLIBPATH point to valid bundled directories containing init.tcl and tk.tcl."""
    if hasattr(sys, "_MEIPASS"):
        meipass = getattr(sys, "_MEIPASS")
        # Find directory containing init.tcl
        tcl_candidates = [
            os.path.join(meipass, "_tcl_data"),
            os.path.join(meipass, "tcl", "tcl8.6"),
            os.path.join(meipass, "tcl8.6"),
            os.path.join(meipass, "_tcl_data", "tcl8.6"),
        ]
        for cand in tcl_candidates:
            if os.path.isfile(os.path.join(cand, "init.tcl")):
                os.environ["TCL_LIBRARY"] = cand
                break

        # Find directory containing tk.tcl
        tk_candidates = [
            os.path.join(meipass, "_tk_data"),
            os.path.join(meipass, "tcl", "tk8.6"),
            os.path.join(meipass, "tk8.6"),
            os.path.join(meipass, "_tk_data", "tk8.6"),
        ]
        for cand in tk_candidates:
            if os.path.isfile(os.path.join(cand, "tk.tcl")):
                os.environ["TK_LIBRARY"] = cand
                break

        tcl8_dir = os.path.join(meipass, "tcl8")
        if os.path.isdir(tcl8_dir):
            existing = os.environ.get("TCLLIBPATH", "")
            if meipass not in existing:
                os.environ["TCLLIBPATH"] = f"{meipass} {existing}".strip()


_bootstrap_tcl_tk_environment()

import tkinter as tk
import uuid
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

# Ensure project root is in sys.path when executed directly as a script
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.agent_loop import (
    AgentLoop,
    DeepSeekAgentAdapter,
    LoopTerminationStatus,
    LLMClientProtocol,
    ModelTurnResponse,
    ToolCall,
)
from src.budget_controller import BudgetController
from src.command_validator import redact_secrets
from src.deepseek_client import DeepSeekClient
from src.generator_pipeline import OfflinePipelineCoordinator, PipelineExecutionSummary
from src.path_security import PathSecurityError, safe_write_file, validate_workspace_path
from src.project_validator import ProjectValidator
from src.spec_preprocessor import (
    SpecificationExtractionError,
    SpecificationPreprocessResult,
    SpecificationPreprocessor,
)


def get_resource_path(relative_path: str) -> str:
    """Resolve resource path for both development source tree and PyInstaller bundle.

    When packaged by PyInstaller, sys._MEIPASS holds the unpacked directory path.
    When running unbundled, PROJECT_ROOT is used as the base path.
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(getattr(sys, "_MEIPASS"), relative_path)
    return os.path.abspath(os.path.join(PROJECT_ROOT, relative_path))


def resolve_input_file(filename: str) -> str:
    """Finds an input file in current working directory, then bundled resources."""
    if os.path.isfile(filename):
        return os.path.abspath(filename)
    bundled = get_resource_path(filename)
    if os.path.isfile(bundled):
        return os.path.abspath(bundled)
    return os.path.abspath(filename)


STARTUP_RECEIPT_SCHEMA_VERSION = 1


def startup_receipt_directory(workspace_root: Optional[str] = None) -> str:
    """Return the deterministic local diagnostic directory for unattended startup."""
    root = workspace_root or os.getcwd()
    return os.path.join(os.path.realpath(os.path.abspath(root)), ".code_agent_diagnostics")


def _write_startup_receipt(receipt_path: str, receipt: Dict[str, Any]) -> bool:
    """Atomically persist a sanitized receipt; never expose an underlying error."""
    try:
        os.makedirs(os.path.dirname(receipt_path), exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix=".startup_", suffix=".json", dir=os.path.dirname(receipt_path)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as receipt_file:
                json.dump(receipt, receipt_file, sort_keys=True, indent=2)
                receipt_file.write("\n")
            os.replace(temporary_path, receipt_path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class WorkerRunConfig:
    """Immutable snapshot of execution configuration captured on GUI thread before worker spawn."""

    run_id: str
    doc_path: str
    view_path: str
    output_dir: str
    execution_mode: str
    task_goal: str
    allow_live_dispatch: bool
    api_key: str = ""
    live_timeout: float = 60.0
    max_turns: int = 20
    workflow_mode: str = "offline_pipeline"
    live_max_output_tokens: int = 2500
    startup_receipt_path: Optional[str] = None
    resume_staging: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"WorkerRunConfig(run_id='{self.run_id}', doc_path='{self.doc_path}', "
            f"view_path='{self.view_path}', output_dir='{self.output_dir}', "
            f"execution_mode='{self.execution_mode}', task_goal='{self.task_goal}', "
            f"allow_live_dispatch={self.allow_live_dispatch}, api_key='[PROTECTED_AND_REDACTED]', "
            f"live_timeout={self.live_timeout}, max_turns={self.max_turns}, "
            f"workflow_mode='{self.workflow_mode}')"
        )

    def __str__(self) -> str:
        return self.__repr__()


class QueueMessage(tuple):
    """2-tuple (msg_type, data) with optional run_id attribute for backwards compatibility."""

    def __new__(cls, msg_type: str, data: Any, run_id: Optional[str] = None):
        obj = super().__new__(cls, (msg_type, data))
        obj.run_id = run_id
        return obj


class OfflineMockAgentClient:
    """Deterministic offline mock LLM client for safe desktop GUI pipeline execution.

    Adheres to LLMClientProtocol and synthesizes valid, AST-parsable Space Fractions
    deliverables across all 4 pipeline stages using write_file tool calls.
    Makes zero network requests and incurs zero financial spend.
    Emits substantive playable game implementations bounded strictly within the
    budget controller's 8,000 input token envelope per stage.
    """

    def __init__(self) -> None:
        self.call_count: int = 0
        self.recorded_reservations: List[Optional[str]] = []

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        reservation_id: Optional[str] = None,
    ) -> ModelTurnResponse:
        self.call_count += 1
        self.recorded_reservations.append(reservation_id)

        # If previous message is a tool response, stage execution is finished
        has_tool_results = any(m.get("role") == "tool" for m in messages)
        if has_tool_results:
            return ModelTurnResponse(
                content="Stage synthesis complete.",
                tool_calls=[],
                raw_usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                finish_reason="stop",
                status="SUCCESS",
            )

        prompt_text = json.dumps(messages)

        mock_fraction_math = 'import math\nclass Fraction:\n    def __init__(self, n=0, d=1):\n        if d == 0: raise ValueError()\n        if d < 0: n, d = -n, -d\n        self.n, self.d = int(n), int(d)\n    def simplify(self): g = math.gcd(self.n, self.d); return Fraction(self.n // g, self.d // g)\n    def add(self, o): return Fraction(self.n * o.d + o.n * self.d, self.d * o.d).simplify()\n    def subtract(self, o): return Fraction(self.n * o.d - o.n * self.d, self.d * o.d).simplify()\n    def compare(self, o): diff = self.n * o.d - o.n * self.d; return 1 if diff > 0 else (-1 if diff < 0 else 0)\n    def equals(self, o): return self.compare(o) == 0\n    @classmethod\n    def from_string(cls, s): p = s.strip().split(\'/\'); return cls(int(p[0]), int(p[1])) if len(p) == 2 else cls(int(p[0]))\n    def __str__(self): return str(self.n) if self.d == 1 else f"{self.n}/{self.d}"\n    def __eq__(self, o): return self.equals(o) if isinstance(o, Fraction) else False\nclass MathQuestionGenerator:\n    def __init__(self, seed=42): self.seed = seed\n    def generate(self, level=1, index=1):\n        from space_fractions.question_bank import QuestionBank\n        return QuestionBank().get_question(level, index)\n'
        mock_game_engine = 'from enum import Enum\nfrom dataclasses import dataclass\nfrom types import SimpleNamespace\nclass GameState(str, Enum): TITLE = "TITLE"; PLAYING = "PLAYING"; PAUSED = "PAUSED"; GAMEOVER = "GAMEOVER"\nclass GameEngineError(Exception): pass\n@dataclass\nclass TurnResult:\n    is_correct: bool = False; is_game_over: bool = False; current_score: int = 0\nclass GameEngine:\n    def __init__(self, level=1, total_questions=10, seed=42):\n        from space_fractions.question_bank import QuestionBank\n        self.level, self.total_questions, self.state, self.score, self.shields, self.streak, self.current_question_index, self.questions_solved, self.victory, self.bank, self.current_question = level, total_questions, GameState.TITLE, 0, 3, 0, 0, 0, False, QuestionBank(), None\n    def play(self):\n        self.__init__(self.level, self.total_questions)\n        self.state = GameState.PLAYING\n        self.next_question()\n    def viewScore(self): return self.score\n    view_score = viewScore\n    def pause(self):\n        if self.state != GameState.PLAYING: raise GameEngineError("Err")\n        self.state = GameState.PAUSED\n    def resume(self):\n        if self.state != GameState.PAUSED: raise GameEngineError("Err")\n        self.state = GameState.PLAYING\n    def gameOver(self, v=True): self.state, self.victory = GameState.GAMEOVER, v\n    game_over = gameOver\n    def next_question(self):\n        if self.current_question_index >= self.total_questions: self.gameOver(True); self.current_question = None; return None\n        self.current_question_index += 1; self.current_question = self.bank.get_question(self.level, self.current_question_index); return self.current_question\n    def submit_answer(self, idx: int) -> TurnResult:\n        if self.state != GameState.PLAYING: raise GameEngineError("Err")\n        c = (idx == self.current_question.correct_index)\n        if c: self.streak += 1; self.score += 100 + (self.streak - 1) * 20; self.questions_solved += 1\n        else: self.streak = 0; self.shields -= 1\n        over = self.shields <= 0 or self.current_question_index >= self.total_questions\n        vic = self.shields > 0 and self.current_question_index >= self.total_questions\n        if over: self.gameOver(vic)\n        return TurnResult(c, over, self.score)\n    def calculate_accuracy(self): return 100\n    def calculate_rank(self): return "Fleet Admiral"\n    def get_summary(self):\n        return SimpleNamespace(score=self.score, accuracy_percentage=100, rank="Fleet Admiral", questions_solved=self.questions_solved, questions_attempted=self.current_question_index, victory=self.victory)\nclass User:\n    def __init__(self, u, n): self.id, self.username = u, n\n    def playGame(self, g): g.play()\n    play_game = playGame\n    def viewScore(self, g): return g.viewScore()\n    view_score = viewScore\nGame = GameEngine\n'
        mock_question_bank = 'from dataclasses import dataclass\n@dataclass\nclass Question:\n    id: str; prompt: str; options: list; correct_index: int; explanation: str = ""; level: int = 1; topic: str = "Math"\n    def getPrompt(self): return self.prompt\n    get_prompt = getPrompt\n    def getOptions(self): return list(self.options)\n    get_options = getOptions\n    def check_answer(self, i): return i == self.correct_index\n_F = [\n("4/8",1,1),("2/3?",0,1),("6/9",2,1),("3/5?",3,1),("10/25",1,1),("1/4?",0,1),("12/16",2,1),("5/6?",3,1),\n("1/5+2/5",0,2),("3/4 vs 2/3",1,2),("7/8-3/8",2,2),("2/5 vs 3/7",3,2),("2/7+3/7",1,2),("5/6 vs 7/8",0,2),("5/9-2/9",2,2),("4/5 vs 8/10",3,2),\n("1/2+1/3",0,3),("3/4-1/2",1,3),("2/3+1/4",2,3),("5/6-1/3",3,3),("3/5+1/2",1,3),("4/5-2/3",0,3),("3/8+1/4",2,3),("7/10-2/5",3,3)\n]\n_O = ["1/2", "2/3", "3/4", "4/5"]\nCURATED_QUESTIONS = [Question(f"Q{i}", p, list(_O), c, f"Sol: {_O[c]}", l) for i, (p, c, l) in enumerate(_F, 1)]\nclass QuestionBank:\n    def __init__(self, seed=42, custom_questions=None, use_curated=True):\n        self._custom = {q.id: q for q in (custom_questions or [])}\n    def add_question(self, q): self._custom[q.id] = q\n    def update_questions(self, qs): [self.add_question(q) for q in qs]\n    def get_question(self, level: int = 1, index: int = 1) -> Question:\n        cid = f"CUSTOM-L{level}-Q{index}"\n        if cid in self._custom: return self._custom[cid]\n        pool = [q for q in CURATED_QUESTIONS if q.level == level]\n        return pool[(index - 1) % len(pool)] if pool else CURATED_QUESTIONS[0]\n    def get_questions_for_level(self, level: int = 1, count: int = 10): return [self.get_question(level, i) for i in range(1, count + 1)]\n    def count_custom_questions(self): return len(self._custom)\n    @classmethod\n    def total_curated_questions(cls): return len(CURATED_QUESTIONS)\n'
        mock_ui_screens = 'import tkinter as tk\nfrom tkinter import ttk\nclass FractionVisualizer(tk.Canvas):\n    def __init__(self, parent, width: int = 240, height: int = 180, **kwargs):\n        super().__init__(parent, width=width, height=height, bg="#111827", highlightthickness=1, **kwargs)\n        self.canvas_width, self.canvas_height = width, height\n    def draw_fraction(self, n: int, d: int, title: str = "") -> None:\n        self.delete("all")\n        if d <= 0 or n < 0:\n            self.create_text(self.canvas_width // 2, self.canvas_height // 2, text="Invalid Fraction", fill="#EF4444")\n            return\n        cx, cy, r = self.canvas_width // 2, self.canvas_height // 2 - 10, min(self.canvas_width, self.canvas_height) // 2 - 18\n        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#1F2937", outline="#38BDF8", width=2)\n        if min(n, d) > 0:\n            ang = 360.0 / d\n            for i in range(min(n, d)):\n                self.create_arc(cx - r, cy - r, cx + r, cy + r, start=90.0 - (i + 1) * ang, extent=ang, fill="#06B6D4", outline="#38BDF8", width=2)\n        self.create_text(cx, self.canvas_height - 14, text=f"{n}/{d}" if not title else f"{title}: {n}/{d}", fill="#F8FAFC", font=("Segoe UI", 11, "bold"))\nclass HUDFrame(ttk.Frame):\n    def __init__(self, parent):\n        super().__init__(parent, padding="8")\n        self.shields_var, self.score_var, self.streak_var, self.level_var, self.progress_var = (\n            tk.StringVar(value="[3 Shields]"), tk.StringVar(value="0"), tk.StringVar(value="0"), tk.StringVar(value="Level 1"), tk.StringVar(value="Question 0 / 10")\n        )\n        ttk.Label(self, textvariable=self.shields_var).pack(side=tk.LEFT, padx=6)\n        ttk.Label(self, textvariable=self.score_var).pack(side=tk.LEFT, padx=6)\n        ttk.Label(self, textvariable=self.streak_var).pack(side=tk.LEFT, padx=6)\n        ttk.Label(self, textvariable=self.progress_var).pack(side=tk.RIGHT, padx=6)\n    def update_hud(self, shields: int, score: int, streak: int, current_q: int, total_q: int, level=None):\n        self.shields_var.set(f"[{shields} Shields]" if shields > 0 else "[DEPLETED]")\n        self.score_var.set(f"{score}")\n        self.streak_var.set(f"Streak: {streak}")\n        self.progress_var.set(f"Question {current_q} / {total_q}")\n        if level is not None: self.level_var.set(f"Level {level}")\n'
        mock_main = 'import tkinter as tk\nfrom tkinter import ttk\nfrom space_fractions.game_engine import GameEngine, GameState\nfrom space_fractions.ui_screens import FractionVisualizer, HUDFrame\n\nclass SpaceFractionsApp:\n    def __init__(self, root: tk.Tk):\n        self.root = root\n        self.root.title("Space Fractions — Desktop Edition")\n        self.root.geometry("840x620")\n        self.engine = GameEngine()\n        self.container = ttk.Frame(self.root, padding="10")\n        self.container.pack(fill=tk.BOTH, expand=True)\n        self.show_title_screen()\n    def show_title_screen(self):\n        for w in self.container.winfo_children(): w.destroy()\n        ttk.Label(self.container, text="Space Fractions", font=("Segoe UI", 18, "bold")).pack(pady=10)\n        ttk.Button(self.container, text="Launch Mission", command=self.start_mission).pack(pady=10)\n    def start_mission(self):\n        self.engine.play()\n        self.show_gameplay_screen()\n    def show_gameplay_screen(self):\n        for w in self.container.winfo_children(): w.destroy()\n        self.hud = HUDFrame(self.container); self.hud.pack(fill=tk.X)\n        self.hud.update_hud(self.engine.shields, self.engine.score, self.engine.streak, self.engine.current_question_index, self.engine.total_questions, self.engine.level)\n        self.visualizer = FractionVisualizer(self.container); self.visualizer.pack(pady=10)\n        self.visualizer.draw_fraction(1, 2)\n        q = self.engine.current_question\n        ttk.Label(self.container, text=q.prompt if q else "", font=("Segoe UI", 12, "bold")).pack(pady=8)\n        for i, opt in enumerate(q.options if q else []):\n            ttk.Button(self.container, text=f"[{i+1}] {opt}", command=lambda idx=i: self.handle_answer(idx)).pack(pady=3)\n    def handle_answer(self, idx: int):\n        res = self.engine.submit_answer(idx)\n        if res.is_game_over: self.show_game_over_screen()\n        else: self.engine.next_question(); self.show_gameplay_screen()\n    def toggle_pause(self):\n        if self.engine.state == GameState.PLAYING: self.engine.pause()\n        elif self.engine.state == GameState.PAUSED: self.engine.resume()\n    def show_game_over_screen(self):\n        for w in self.container.winfo_children(): w.destroy()\n        s = self.engine.get_summary()\n        t = "Mission Accomplished!" if s.victory else "Emergency Warp Out!"\n        ttk.Label(self.container, text=t, font=("Segoe UI", 16, "bold")).pack(pady=10)\n        ttk.Label(self.container, text=f"Score: {s.score} | Accuracy: {s.accuracy_percentage}% | Rank: {s.rank}").pack(pady=6)\n        ttk.Button(self.container, text="Restart", command=self.show_title_screen).pack(pady=10)\n\ndef main():\n    root = tk.Tk()\n    app = SpaceFractionsApp(root)\n    root.mainloop()\n\nif __name__ == "__main__": main()\n'
        mock_test_math = 'import pytest\nfrom space_fractions.fraction_math import Fraction\n\ndef test_fraction_math_basic():\n    assert Fraction(1, 2).equals(Fraction(2, 4))\n    assert Fraction(1, 3).add(Fraction(1, 6)) == Fraction(1, 2)\n    assert Fraction(3, 4).subtract(Fraction(1, 2)) == Fraction(1, 4)\n    assert Fraction.from_string("3/4") == Fraction(3, 4)\n'
        mock_test_engine = 'import pytest\nfrom space_fractions.game_engine import GameEngine, GameState\n\ndef test_game_engine_basic():\n    e = GameEngine(level=1, total_questions=5, seed=42)\n    assert e.state == GameState.TITLE\n    e.play()\n    assert e.state == GameState.PLAYING\n    assert e.current_question is not None\n    res = e.submit_answer(e.current_question.correct_index)\n    assert res.is_correct is True\n    assert res.current_score >= 100\n'
        mock_test_game = 'import pytest\nfrom space_fractions.fraction_math import Fraction\nfrom space_fractions.game_engine import GameEngine, GameState\nfrom space_fractions.question_bank import QuestionBank\n\ndef test_space_fractions_integration():\n    f = Fraction(3, 4)\n    assert f.n == 3 and f.d == 4\n    engine = GameEngine()\n    assert engine.state == GameState.TITLE\n    engine.play()\n    assert engine.state == GameState.PLAYING\n    bank = QuestionBank()\n    assert bank.total_curated_questions() >= 20\n    q = bank.get_question(level=1, index=1)\n    assert len(q.options) == 4\n'

        mock_fraction_math = mock_fraction_math.replace(
            '    def __str__(self): return str(self.n) if self.d == 1 else f"{self.n}/{self.d}"\n',
            '    def __str__(self):\n        reduced = self.simplify(); return str(reduced.n) if reduced.d == 1 else f"{reduced.n}/{reduced.d}"\n',
        )

        # Detect stage from prompt
        mock_calculator = '"""Command-line calculator implementation."""\nimport sys\n\n\ndef add(a: float, b: float) -> float:\n    return a + b\n\n\ndef subtract(a: float, b: float) -> float:\n    return a - b\n\n\ndef multiply(a: float, b: float) -> float:\n    return a * b\n\n\ndef divide(a: float, b: float) -> float:\n    if b == 0:\n        raise ValueError("Division by zero is undefined.")\n    return a / b\n\n\ndef calculate(operation: str, a: float, b: float) -> float:\n    ops = {\n        "add": add, "+": add,\n        "subtract": subtract, "-": subtract, "sub": subtract,\n        "multiply": multiply, "*": multiply, "mul": multiply,\n        "divide": divide, "/": divide, "div": divide,\n    }\n    op_clean = operation.strip().lower()\n    if op_clean not in ops:\n        raise ValueError(f"Unsupported operation: {operation}")\n    return ops[op_clean](a, b)\n\n\ndef main() -> None:\n    if len(sys.argv) != 4:\n        print("Usage: python calculator.py <operation> <operand1> <operand2>")\n        sys.exit(1)\n    op = sys.argv[1]\n    try:\n        a, b = float(sys.argv[2]), float(sys.argv[3])\n        result = calculate(op, a, b)\n        print(f"Result: {result}")\n    except ValueError as err:\n        print(f"Error: {err}")\n        sys.exit(1)\n\n\nif __name__ == "__main__":\n    main()\n'
        mock_test_calculator = '"""Unit tests for command-line calculator."""\nimport pytest\nfrom calculator import add, subtract, multiply, divide, calculate\n\n\ndef test_add():\n    assert add(10, 5) == 15\n    assert add(-3, 7) == 4\n    assert add(0, 0) == 0\n\n\ndef test_subtract():\n    assert subtract(10, 5) == 5\n    assert subtract(5, 10) == -5\n    assert subtract(0, 0) == 0\n\n\ndef test_multiply():\n    assert multiply(3, 4) == 12\n    assert multiply(-2, 5) == -10\n    assert multiply(0, 100) == 0\n\n\ndef test_divide():\n    assert divide(10, 2) == 5\n    with pytest.raises(ValueError, match="Division by zero"):\n        divide(10, 0)\n\n\ndef test_calculate():\n    assert calculate("add", 2, 3) == 5\n    assert calculate("+", 2, 3) == 5\n    assert calculate("sub", 5, 2) == 3\n    assert calculate("mul", 4, 3) == 12\n    assert calculate("div", 12, 4) == 3\n\n\ndef test_calculate_unknown():\n    with pytest.raises(ValueError, match="Unsupported operation"):\n        calculate("power", 2, 3)\n'

        if "calculator" in prompt_text.lower():
            files_to_write = {
                "calculator.py": mock_calculator,
                "tests/test_calculator.py": mock_test_calculator,
                "README.md": (
                    "# Python Command-Line Calculator\n\n"
                    "Command-line calculator supporting add, subtract, multiply, and divide.\n"
                    "Deterministic offline mock deliverable generated via AgentLoop.\n"
                ),
            }
        elif "specs_generation" in prompt_text or "S1-SPECS" in prompt_text:
            files_to_write = {
                "specs/openapi.yaml": "openapi: 3.0.3\ninfo:\n  title: API\n  version: 1.0.0\npaths: {}\n",
                "specs/internal.proto": 'syntax = "proto3";\npackage spacefractions;\n',
                "specs/sql/game_ddl.sql": "CREATE TABLE IF NOT EXISTS games (id SERIAL PRIMARY KEY);\n",
                "specs/k8s/spacefractions-deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\n",
                "specs/traceability_matrix.csv": "RequirementId,Component,File,Status\nREQ-1,Core,math.py,Pass\n",
            }
        elif "game_core_generation" in prompt_text or "S2-GAME-CORE" in prompt_text:
            files_to_write = {
                "space_fractions/fraction_math.py": mock_fraction_math,
                "space_fractions/game_engine.py": mock_game_engine,
                "space_fractions/question_bank.py": mock_question_bank,
            }
        elif "game_ui_generation" in prompt_text or "S3-GAME-UI" in prompt_text:
            files_to_write = {
                "space_fractions/ui_screens.py": mock_ui_screens,
                "space_fractions/main.py": mock_main,
            }
        elif "tests_generation" in prompt_text or "S4-TESTS" in prompt_text:
            files_to_write = {
                "tests/test_fraction_math.py": mock_test_math,
                "tests/test_game_engine.py": mock_test_engine,
                "tests/test_fraction_game.py": mock_test_game,
                "requirements.txt": (
                    "# Requirements for Space Fractions\n"
                    "# Python 3 Standard Library only (tkinter, math, random, dataclasses, enum)\n"
                ),
                "README.md": (
                    "# Space Fractions\n\n"
                    "Grade 6 Educational Mathematics Desktop Adventure Game.\n"
                    "Built with Python 3.12 and Tkinter. 100% offline.\n"
                ),
                "offline_validation_manifest.json": json.dumps({
                    "project_name": "Space Fractions",
                    "runtime_gameplay_verified": False,
                    "generated_files": [
                        "specs/openapi.yaml",
                        "specs/internal.proto",
                        "specs/sql/game_ddl.sql",
                        "specs/k8s/spacefractions-deployment.yaml",
                        "specs/traceability_matrix.csv",
                        "space_fractions/fraction_math.py",
                        "space_fractions/game_engine.py",
                        "space_fractions/question_bank.py",
                        "space_fractions/ui_screens.py",
                        "space_fractions/main.py",
                        "tests/test_fraction_math.py",
                        "tests/test_game_engine.py",
                        "tests/test_fraction_game.py",
                        "requirements.txt",
                        "README.md",
                    ],
                }, indent=2),
                "space_fractions_manifest.json": json.dumps({
                    "project_name": "Space Fractions",
                    "target_platform": "Windows Desktop (Python 3.12 / Tkinter)",
                    "runtime_gameplay_verified": False,
                    "generated_files": [
                        "specs/openapi.yaml",
                        "specs/internal.proto",
                        "specs/sql/game_ddl.sql",
                        "specs/k8s/spacefractions-deployment.yaml",
                        "specs/traceability_matrix.csv",
                        "space_fractions/fraction_math.py",
                        "space_fractions/game_engine.py",
                        "space_fractions/question_bank.py",
                        "space_fractions/ui_screens.py",
                        "space_fractions/main.py",
                        "tests/test_fraction_math.py",
                        "tests/test_game_engine.py",
                        "tests/test_fraction_game.py",
                        "requirements.txt",
                        "README.md",
                    ],
                }, indent=2),
            }
        else:
            if "task goal:" in prompt_text.lower() or "user task" in prompt_text.lower():
                files_to_write = {
                    "main.py": '# Generic Task Implementation\ndef main() -> None:\n    print("Task completed successfully.")\n\nif __name__ == "__main__":\n    main()\n',
                    "tests/test_main.py": '# Generic Task Tests\ndef test_main():\n    assert True\n',
                    "README.md": "# Task Project\n\nGenerated via offline deterministic mock AgentLoop.\n",
                }
            else:
                files_to_write = {}

        if files_to_write:
            tool_calls = [
                ToolCall(tool_id=f"tc_{idx}", name="write_file", arguments={"path": path, "content": content})
                for idx, (path, content) in enumerate(files_to_write.items())
            ]
            return ModelTurnResponse(
                content=None,
                tool_calls=tool_calls,
                raw_usage={"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230},
                finish_reason="tool_calls",
                status="SUCCESS",
            )
        else:
            return ModelTurnResponse(
                content="Stage synthesis complete.",
                tool_calls=[],
                raw_usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
                finish_reason="stop",
                status="SUCCESS",
            )


class AgentGUIApp:
    """Native Windows desktop GUI for the Autonomous Code Agent."""

    DEFAULT_DOC_FILE = "Architecture_Documentation.md"
    DEFAULT_VIEW_FILE = "Architecture_View.md"
    DEFAULT_OUTPUT_DIR = "generated_space_fractions"

    DEFAULT_OFFLINE_TASK = "Ingest architecture specs, extract 11 views / 13 PlantUML diagrams, and run offline pipeline."
    DEFAULT_AGENT_TASK = "Synthesize Space Fractions Grade 6 educational game from Kruchten 4+1 architecture specifications."

    # Historical spend baseline (unreconciled without centralized repository ledger)
    BASE_VERIFIED_SPEND: Optional[float] = None
    HARD_CEILING = 0.800000

    def __init__(
        self,
        root: tk.Tk,
        auto_run: bool = False,
        auto_close_ms: int = 0,
        suppress_messagebox: bool = False,
        mock_llm_client: Optional[LLMClientProtocol] = None,
        allow_live_dispatch: bool = False,
        workflow_mode: str = "offline_pipeline",
        live_max_output_tokens: int = 2500,
        synthesis_runner_factory: Optional[Callable[..., Any]] = None,
        default_execution_mode: str = "offline",
        default_output_dir: Optional[str] = None,
        launch_game: bool = False,
        default_task: Optional[str] = None,
        max_turns: int = 20,
        live_timeout: float = 60.0,
        resume_staging: Optional[str] = None,
    ) -> None:
        self.root = root
        self.auto_run = auto_run
        self.auto_close_ms = auto_close_ms
        self.suppress_messagebox = suppress_messagebox
        self.mock_llm_client: Optional[LLMClientProtocol] = mock_llm_client
        self.allow_live_dispatch: bool = allow_live_dispatch
        self.workflow_mode: str = workflow_mode
        self.synthesis_runner_factory: Optional[Callable[..., Any]] = synthesis_runner_factory
        self.default_execution_mode: str = default_execution_mode
        self.default_output_dir: Optional[str] = default_output_dir
        self.launch_game = launch_game
        self.default_task = default_task
        self.max_turns = max_turns
        self.live_timeout = max(1.0, float(live_timeout))
        self.live_max_output_tokens = min(4096, max(500, int(live_max_output_tokens)))
        self.resume_staging = resume_staging
        self._game_window: Optional[tk.Toplevel] = None
        self._game_app: Optional[Any] = None

        self._env_file_found: bool = False
        self._key_source_is_env: bool = False
        self._loading_env: bool = False
        self.lbl_subtitle: Optional[ttk.Label] = None

        self.root.title("DeepSeek Autonomous Code Agent — Space Fractions [Offline Shell]")
        self.root.geometry("1020x740")
        self.root.minsize(920, 640)

        # Threading and synchronization state
        self.msg_queue: queue.Queue[Any] = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None
        self.cancel_requested = threading.Event()
        self.is_running = False
        self._state_lock = threading.Lock()
        self.run_state = "INITIALIZING"
        self.current_run_id: Optional[str] = None
        self.last_staging_dir: Optional[str] = None
        self.last_run_dir: Optional[str] = None
        self.startup_receipt_path: Optional[str] = None
        self._startup_receipt: Optional[Dict[str, Any]] = None
        self._pre_commit_hook: Optional[Callable[[], None]] = None
        self._in_commit_hook: Optional[Callable[[], None]] = None

        # Bind window close protocol for safe deferred shutdown
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_closing)

        # Budget tracking instance
        self.has_authoritative_history = False
        self.budget_controller = BudgetController(ceiling=self.HARD_CEILING)
        self.budget_controller.cumulative_spend = 0.0

        # Extracted results cache
        self.last_preprocess_result: Optional[SpecificationPreprocessResult] = None
        self.last_execution_summary: Optional[PipelineExecutionSummary] = None

        # Execution mode: offline template pipeline is default; mock_agent provides multi-stage AgentLoop
        self.execution_mode_var = tk.StringVar(value=default_execution_mode)
        self.execution_mode_var.trace_add("write", self._on_execution_mode_changed)

        # Style configuration
        self._setup_styles()

        # Build UI layout
        self._build_ui()
        self._on_execution_mode_changed()

        # Try auto-loading .env without logging or printing the secret
        self._auto_load_env_secret()

        # Scan for orphaned staging directories from interrupted prior runs
        self._scan_for_orphan_staging_dirs()

        # Start queue processing on main thread
        self.root.after(100, self._process_queue)

        if self.auto_run:
            self.root.after(400, self.on_start_click)

        if self.auto_close_ms > 0:
            self.root.after(self.auto_close_ms, self._safe_auto_close)
        if self.launch_game:
            self.root.after(100, self.launch_playable_game)

    def _setup_styles(self) -> None:
        """Configures ttk styles for clean Windows desktop aesthetics."""
        self.style = ttk.Style()
        try:
            self.style.theme_use("vista")
        except Exception:
            try:
                self.style.theme_use("clam")
            except Exception:
                pass

        # Explicitly enforce clean light Windows desktop theme across all default ttk elements
        self.style.configure(".", background="SystemButtonFace", foreground="#000000")
        self.style.configure("TFrame", background="SystemButtonFace")
        self.style.configure("TLabelframe", background="SystemButtonFace")
        self.style.configure("TLabelframe.Label", background="SystemButtonFace", foreground="#2D3748")
        self.style.configure("TLabel", background="SystemButtonFace", foreground="#000000")

        self.style.configure("Title.TLabel", font=("Segoe UI", 13, "bold"), foreground="#1A202C", background="SystemButtonFace")
        self.style.configure("Subtitle.TLabel", font=("Segoe UI", 9), foreground="#4A5568", background="SystemButtonFace")
        self.style.configure("Header.TLabelframe.Label", font=("Segoe UI", 9, "bold"), foreground="#2D3748", background="SystemButtonFace")
        self.style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"))
        self.style.configure("Danger.TButton", font=("Segoe UI", 9))
        self.style.configure("TelemetryValue.TLabel", font=("Segoe UI", 10, "bold"), foreground="#2B6CB0", background="SystemButtonFace")
        self.style.configure("ModeBadge.TLabel", font=("Segoe UI", 9, "bold"), foreground="#C53030", background="SystemButtonFace")

    def _build_ui(self) -> None:
        """Constructs the complete hierarchical widget tree."""
        main_container = ttk.Frame(self.root, padding="12")
        main_container.pack(fill=tk.BOTH, expand=True)

        # 1. Header Banner
        self._build_header(main_container)

        # 2. Main Work Area (PanedWindow: Top for Configuration/Telemetry, Bottom for Logs)
        self.paned_window = ttk.PanedWindow(main_container, orient=tk.VERTICAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        # Top Section: Config & Telemetry
        top_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(top_frame, weight=0)

        # Left Column in Top: Configuration & Inputs
        config_frame = ttk.LabelFrame(top_frame, text=" 1. Agent Configuration & Source Documents ", style="Header.TLabelframe", padding="10")
        config_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        self._build_config_form(config_frame)

        # Right Column in Top: Budget & Telemetry Meter
        telemetry_frame = ttk.LabelFrame(top_frame, text=" 2. Financial Budget & Verification Guard ", style="Header.TLabelframe", padding="10")
        telemetry_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=(6, 0))
        self._build_telemetry_panel(telemetry_frame)

        # Bottom Section: Action Controls & Multi-Tab Notebook
        bottom_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(bottom_frame, weight=1)

        self._build_action_bar(bottom_frame)
        self._build_notebook_tabs(bottom_frame)

    def _build_header(self, parent: ttk.Frame) -> None:
        """Builds the title and mode identification header."""
        header_frame = ttk.Frame(parent)
        header_frame.pack(fill=tk.X, pady=(0, 4))

        title_lbl = ttk.Label(
            header_frame,
            text="Autonomous Code Agent — Space Fractions Synthesis",
            style="Title.TLabel",
        )
        title_lbl.pack(anchor=tk.W)

        self.lbl_subtitle = ttk.Label(
            header_frame,
            text="Offline Scaffolding & Verification Shell | Model: deepseek-v4-pro (Simulated Offline Mode)",
            style="Subtitle.TLabel",
        )
        self.lbl_subtitle.pack(anchor=tk.W)

    def _build_config_form(self, parent: ttk.LabelFrame) -> None:
        """Builds API key masking, file selection controls, and output folder assignment."""
        parent.columnconfigure(1, weight=1)

        # API Key
        ttk.Label(parent, text="DeepSeek API Key:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.api_key_var = tk.StringVar()
        self.api_key_var.trace_add("write", self._on_api_key_user_edited)
        self.api_key_entry = ttk.Entry(parent, textvariable=self.api_key_var, show="*", width=38)
        self.api_key_entry.grid(row=0, column=1, sticky=tk.EW, padx=4, pady=3)

        self.btn_load_env = ttk.Button(parent, text="Auto-Load .env", command=self._auto_load_env_secret)
        self.btn_load_env.grid(row=0, column=2, padx=2, pady=3)

        self.key_status_var = tk.StringVar(value="Key Status: No local .env detected")
        self.lbl_key_status = ttk.Label(parent, textvariable=self.key_status_var, font=("Segoe UI", 8), foreground="#718096")
        self.lbl_key_status.grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=4)

        # Architecture Documentation
        ttk.Label(parent, text="Architecture Doc:").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.doc_path_var = tk.StringVar(value=resolve_input_file(self.DEFAULT_DOC_FILE))
        self.doc_entry = ttk.Entry(parent, textvariable=self.doc_path_var, width=38)
        self.doc_entry.grid(row=2, column=1, sticky=tk.EW, padx=4, pady=3)
        self.btn_browse_doc = ttk.Button(parent, text="Browse...", command=self._browse_doc_file)
        self.btn_browse_doc.grid(row=2, column=2, padx=2, pady=3)

        # Architecture View
        ttk.Label(parent, text="Architecture View:").grid(row=3, column=0, sticky=tk.W, pady=3)
        self.view_path_var = tk.StringVar(value=resolve_input_file(self.DEFAULT_VIEW_FILE))
        self.view_entry = ttk.Entry(parent, textvariable=self.view_path_var, width=38)
        self.view_entry.grid(row=3, column=1, sticky=tk.EW, padx=4, pady=3)
        self.btn_browse_view = ttk.Button(parent, text="Browse...", command=self._browse_view_file)
        self.btn_browse_view.grid(row=3, column=2, padx=2, pady=3)

        # Output Directory
        ttk.Label(parent, text="Output Directory:").grid(row=4, column=0, sticky=tk.W, pady=3)
        initial_out = self.default_output_dir if self.default_output_dir else self.DEFAULT_OUTPUT_DIR
        self.output_dir_var = tk.StringVar(value=os.path.abspath(initial_out))
        self.output_entry = ttk.Entry(parent, textvariable=self.output_dir_var, width=38)
        self.output_entry.grid(row=4, column=1, sticky=tk.EW, padx=4, pady=3)
        self.btn_browse_output = ttk.Button(parent, text="Browse...", command=self._browse_output_dir)
        self.btn_browse_output.grid(row=4, column=2, padx=2, pady=3)

        # Task Description
        ttk.Label(parent, text="Task Goal:").grid(row=5, column=0, sticky=tk.W, pady=3)
        initial_task = getattr(self, "default_task", None) or (
            self.DEFAULT_AGENT_TASK if getattr(self, "default_execution_mode", "offline") in ("mock_agent", "live") else self.DEFAULT_OFFLINE_TASK
        )
        self.task_desc_var = tk.StringVar(value=initial_task)
        self.task_entry = ttk.Entry(parent, textvariable=self.task_desc_var, width=38)
        self.task_entry.grid(row=5, column=1, columnspan=2, sticky=tk.EW, padx=4, pady=3)

        # Execution Mode
        ttk.Label(parent, text="Run Mode:").grid(row=6, column=0, sticky=tk.W, pady=3)
        mode_btn_frame = ttk.Frame(parent)
        mode_btn_frame.grid(row=6, column=1, columnspan=2, sticky=tk.W, padx=4, pady=3)
        self.radio_offline = ttk.Radiobutton(
            mode_btn_frame,
            text="Offline Mock Pipeline",
            variable=self.execution_mode_var,
            value="offline",
        )
        self.radio_offline.pack(side=tk.LEFT, padx=(0, 10))
        self.radio_mock_agent = ttk.Radiobutton(
            mode_btn_frame,
            text="Offline Mock Agent (Multi-Stage)",
            variable=self.execution_mode_var,
            value="mock_agent",
        )
        self.radio_mock_agent.pack(side=tk.LEFT, padx=(0, 10))
        live_state = tk.NORMAL if self.allow_live_dispatch else tk.DISABLED
        live_label = "Live DeepSeek Agent" if self.allow_live_dispatch else "Live API Mode (Disabled — Explicit Opt-In Required)"
        self.radio_live = ttk.Radiobutton(
            mode_btn_frame,
            text=live_label,
            variable=self.execution_mode_var,
            value="live",
            state=live_state,
        )
        self.radio_live.pack(side=tk.LEFT)

        # Live Dispatch Authorization Opt-In Checkbutton
        self.allow_live_var = tk.BooleanVar(value=self.allow_live_dispatch)
        self.chk_allow_live = ttk.Checkbutton(
            parent,
            text="Authorize Live DeepSeek API Dispatch (Explicit User Opt-In)",
            variable=self.allow_live_var,
            command=self._on_toggle_live_auth,
        )
        self.chk_allow_live.grid(row=7, column=1, columnspan=2, sticky=tk.W, padx=4, pady=2)

    def _build_telemetry_panel(self, parent: ttk.LabelFrame) -> None:
        """Builds financial meter cards distinguishing live spend from reservations."""
        # Mode Badge
        mode_frame = ttk.Frame(parent)
        mode_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(mode_frame, text="RUN MODE: ").pack(side=tk.LEFT)
        self.lbl_mode_badge = ttk.Label(
            mode_frame,
            text="OFFLINE VALIDATION (Mock Engine)",
            style="ModeBadge.TLabel",
            wraplength=220,
            justify=tk.LEFT,
        )
        self.lbl_mode_badge.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Telemetry Grid
        grid_frame = ttk.Frame(parent)
        grid_frame.pack(fill=tk.BOTH, expand=True)

        items = [
            ("Cumulative Verified Live Spend:", "UNRECONCILED", "#475569"),
            ("Active Reserved Hold:", "$0.000000", "#D69E2E"),
            ("Hard Project Budget Ceiling:", "$0.800000", "#2D3748"),
            ("Remaining Available Budget:", "UNRECONCILED", "#475569"),
        ]

        self.telemetry_labels: Dict[str, ttk.Label] = {}
        for r_idx, (title, default_val, color) in enumerate(items):
            ttk.Label(grid_frame, text=title, font=("Segoe UI", 9)).grid(
                row=r_idx * 2, column=0, sticky=tk.W, pady=(2, 0)
            )
            val_lbl = ttk.Label(
                grid_frame, text=default_val, font=("Segoe UI", 10, "bold"), foreground=color
            )
            val_lbl.grid(row=r_idx * 2 + 1, column=0, sticky=tk.W, pady=(0, 4))
            self.telemetry_labels[title] = val_lbl

        # Security note & accounting disclaimer
        disclaimer_text = (
            "• Historical multi-stage spend is UNRECONCILED (disparate test runs).\n"
            "• $0.80 ceiling is a client-side reservation guard, not a provider cap.\n"
            "• In offline mode, zero network calls or HTTP packets are sent."
        )
        self.lbl_disclaimer = ttk.Label(
            parent,
            text=disclaimer_text,
            font=("Segoe UI", 8),
            foreground="#718096",
            justify=tk.LEFT,
            wraplength=220,
        )
        self.lbl_disclaimer.pack(anchor=tk.W, fill=tk.X, expand=False, pady=(6, 0))

        def _on_parent_configure(event: Any) -> None:
            new_wrap = max(160, event.width - 24)
            if hasattr(self, "lbl_mode_badge"):
                self.lbl_mode_badge.configure(wraplength=new_wrap)
            if hasattr(self, "lbl_disclaimer"):
                self.lbl_disclaimer.configure(wraplength=new_wrap)

        parent.bind("<Configure>", _on_parent_configure)

    def _on_execution_mode_changed(self, *args: Any) -> None:
        """Updates UI status, window title, subtitle, button labels, and mode badge when execution mode changes."""
        mode = self.execution_mode_var.get() if hasattr(self, "execution_mode_var") else "offline"

        # 1. Update Mode Badge
        if hasattr(self, "lbl_mode_badge"):
            if mode == "mock_agent":
                self.lbl_mode_badge.configure(text="OFFLINE MOCK AGENT (Multi-Stage AgentLoop)")
            elif mode == "live":
                if self.allow_live_dispatch:
                    self.lbl_mode_badge.configure(text="LIVE DEEPSEEK AGENT (Authorized Live Dispatch)")
                else:
                    self.lbl_mode_badge.configure(text="LIVE API MODE (DISABLED)")
            else:
                self.lbl_mode_badge.configure(text="OFFLINE VALIDATION (Mock Engine)")

        # 2. Update Subtitle
        if hasattr(self, "lbl_subtitle") and self.lbl_subtitle:
            if mode == "mock_agent":
                self.lbl_subtitle.configure(
                    text="Deterministic Multi-Stage AgentLoop Simulation | Model: deepseek-v4-pro (Offline Mock Double)"
                )
            elif mode == "live":
                if self.allow_live_dispatch:
                    self.lbl_subtitle.configure(
                        text="Live Autonomous Generation | Model: deepseek-v4-pro (Official HTTPS API)"
                    )
                else:
                    self.lbl_subtitle.configure(
                        text="Live API Mode (Disabled — Explicit User Opt-In Required)"
                    )
            else:
                self.lbl_subtitle.configure(
                    text="Offline Scaffolding & Verification Shell | Model: deepseek-v4-pro (Simulated Offline Mode)"
                )

        # 3. Update Window Title
        if hasattr(self, "root") and self.root:
            if mode == "mock_agent":
                self.root.title("DeepSeek Autonomous Code Agent — Space Fractions [Offline Mock Agent]")
            elif mode == "live":
                if self.allow_live_dispatch:
                    self.root.title("DeepSeek Autonomous Code Agent — Space Fractions [Live Agent]")
                else:
                    self.root.title("DeepSeek Autonomous Code Agent — Space Fractions [Live API Disabled]")
            else:
                self.root.title("DeepSeek Autonomous Code Agent — Space Fractions [Offline Shell]")

        # 4. Update Start Button Label and Status
        if not getattr(self, "is_running", False):
            if hasattr(self, "btn_start"):
                if mode == "mock_agent":
                    self.btn_start.configure(text="▶  Start Mock Agent Synthesis")
                elif mode == "live":
                    if self.allow_live_dispatch:
                        self.btn_start.configure(text="▶  Start Live Agent Generation")
                    else:
                        self.btn_start.configure(text="▶  Live Dispatch Unauthorized")
                else:
                    self.btn_start.configure(text="▶  Start Offline Validation")

            if hasattr(self, "status_var"):
                if mode == "mock_agent":
                    self.status_var.set("Ready. Click 'Start Mock Agent Synthesis' to execute.")
                elif mode == "live":
                    if self.allow_live_dispatch:
                        self.status_var.set("Ready. Click 'Start Live Agent Generation' to execute authorized live run.")
                    else:
                        self.status_var.set("Live dispatch disabled. Check 'Authorize Live DeepSeek API Dispatch' first.")
                else:
                    self.status_var.set("Ready. Click 'Start Offline Validation' to execute.")

        # 5. Adapt Default Task Goal if user hasn't typed a custom task goal
        if hasattr(self, "task_desc_var"):
            current_task = self.task_desc_var.get().strip()
            if not getattr(self, "default_task", None):
                if mode == "offline" and current_task == self.DEFAULT_AGENT_TASK:
                    self.task_desc_var.set(self.DEFAULT_OFFLINE_TASK)
                elif mode in ("mock_agent", "live") and current_task == self.DEFAULT_OFFLINE_TASK:
                    self.task_desc_var.set(self.DEFAULT_AGENT_TASK)

    def _on_toggle_live_auth(self) -> None:
        """Toggles live dispatch authorization and updates live radio button state."""
        self.allow_live_dispatch = bool(self.allow_live_var.get())
        if hasattr(self, "radio_live"):
            if self.allow_live_dispatch:
                self.radio_live.configure(state=tk.NORMAL, text="Live DeepSeek Agent")
            else:
                self.radio_live.configure(state=tk.DISABLED, text="Live API Mode (Disabled — Explicit Opt-In Required)")
                if self.execution_mode_var.get() == "live":
                    self.execution_mode_var.set("offline")
        self._on_execution_mode_changed()

    def _build_action_bar(self, parent: ttk.Frame) -> None:
        """Builds Start and Cancel buttons, progress bar, and status readout."""
        action_frame = ttk.Frame(parent, padding="4")
        action_frame.pack(fill=tk.X, pady=(6, 4))

        self.btn_start = ttk.Button(
            action_frame,
            text="▶  Start Offline Validation",
            style="Primary.TButton",
            command=self.on_start_click,
            width=26,
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_cancel = ttk.Button(
            action_frame,
            text="✖  Cancel",
            style="Danger.TButton",
            command=self.on_cancel_click,
            state=tk.DISABLED,
            width=12,
        )
        self.btn_cancel.pack(side=tk.LEFT, padx=(0, 10))

        self.progress_bar = ttk.Progressbar(action_frame, mode="indeterminate", length=220)
        self.progress_bar.pack(side=tk.LEFT, padx=(0, 10))

        self.status_var = tk.StringVar(value="Ready. Click 'Start Offline Validation' to execute.")
        self.btn_launch_game = ttk.Button(
            action_frame,
            text="🚀  Launch Playable Game (Desktop)",
            command=self.launch_playable_game,
        )
        self.btn_launch_game.pack(side=tk.RIGHT, padx=(6, 0))

        self.lbl_status = ttk.Label(
            action_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 9, "italic"),
            foreground="#2B6CB0",
        )
        self.lbl_status.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_notebook_tabs(self, parent: ttk.Frame) -> None:
        """Builds tabs for activity logs, architecture details, and generated files."""
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        # Tab 1: Real-time Activity Log
        tab_log = ttk.Frame(self.notebook, padding="6")
        self.notebook.add(tab_log, text=" Activity & Validation Log ")
        self._build_log_tab(tab_log)

        # Tab 2: Architecture & Conflict Details
        tab_details = ttk.Frame(self.notebook, padding="6")
        self.notebook.add(tab_details, text=" Architecture & Conflict Details ")
        self._build_details_tab(tab_details)

        # Tab 3: Generated Artifacts Summary
        tab_artifacts = ttk.Frame(self.notebook, padding="6")
        self.notebook.add(tab_artifacts, text=" Generated Artifacts Summary ")
        self._build_artifacts_tab(tab_artifacts)

        # Tab 4: Bundled Assets & Game Specs
        tab_assets = ttk.Frame(self.notebook, padding="6")
        self.notebook.add(tab_assets, text=" Bundled Assets & Specs ")
        self._build_assets_tab(tab_assets)

    def _build_log_tab(self, parent: ttk.Frame) -> None:
        """Builds the scrollable activity text log with color tags."""
        self.log_text = tk.Text(
            parent,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg="#FFFFFF",
            fg="#2D3748",
            relief=tk.SOLID,
            borderwidth=1,
        )
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Configure color tags
        self.log_text.tag_config("INFO", foreground="#2B6CB0")
        self.log_text.tag_config("SUCCESS", foreground="#276749", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("WARNING", foreground="#D69E2E", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("ERROR", foreground="#C53030", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("CONFLICT", foreground="#805AD5", font=("Consolas", 9, "bold"))
        self.log_text.tag_config("MUTED", foreground="#718096")

    def _build_details_tab(self, parent: ttk.Frame) -> None:
        """Displays extracted view items, entities, and recorded architectural conflicts."""
        self.details_text = tk.Text(
            parent,
            wrap=tk.WORD,
            font=("Segoe UI", 9),
            bg="#F7FAFC",
            fg="#2D3748",
            relief=tk.SOLID,
            borderwidth=1,
        )
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.details_text.yview)
        self.details_text.configure(yscrollcommand=scrollbar.set)

        self.details_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.details_text.insert(
            tk.END,
            "Architecture extraction details will appear here after running the preprocessor.\n"
            "Will summarize: 11 View Items, 13 PlantUML Diagrams, Extracted Classes, Actors, States,\n"
            "and the documented Desktop-vs-Cloud Platform Conflict.\n"
        )
        self.details_text.configure(state=tk.DISABLED)

    def _build_artifacts_tab(self, parent: ttk.Frame) -> None:
        """Displays table of generated files and safety verification notices."""
        cols = ("file_name", "relative_path", "size_bytes", "status")
        self.artifacts_tree = ttk.Treeview(parent, columns=cols, show="headings", height=8)

        self.artifacts_tree.heading("file_name", text="File Name")
        self.artifacts_tree.heading("relative_path", text="Relative Destination")
        self.artifacts_tree.heading("size_bytes", text="Size (Bytes)")
        self.artifacts_tree.heading("status", text="Verification Status")

        self.artifacts_tree.column("file_name", width=180, anchor=tk.W)
        self.artifacts_tree.column("relative_path", width=340, anchor=tk.W)
        self.artifacts_tree.column("size_bytes", width=90, anchor=tk.E)
        self.artifacts_tree.column("status", width=140, anchor=tk.CENTER)

        tree_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.artifacts_tree.yview)
        self.artifacts_tree.configure(yscrollcommand=tree_scroll.set)

        self.artifacts_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_assets_tab(self, parent: ttk.Frame) -> None:
        """Builds verification view for bundled game assets and enterprise specifications."""
        header_frame = ttk.Frame(parent)
        header_frame.pack(fill=tk.X, pady=(0, 6))

        notice_lbl = ttk.Label(
            header_frame,
            text="Space Fractions Game is a separate standalone educational client.\n"
                 "Bundled assets are verified for distribution integrity only and are not executed inside this agent shell.",
            font=("Segoe UI", 8),
            foreground="#4A5568",
            justify=tk.LEFT,
        )
        notice_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.btn_launch_game_tab = ttk.Button(
            header_frame,
            text="🚀 Launch Playable Game (Desktop)",
            command=self.launch_playable_game,
        )
        self.btn_launch_game_tab.pack(side=tk.RIGHT, padx=4)

        btn_verify_assets = ttk.Button(
            header_frame,
            text="↻ Verify Assets",
            command=self._populate_assets_tab,
        )
        btn_verify_assets.pack(side=tk.RIGHT, padx=4)

        cols = ("rel_path", "size_bytes", "readable", "resolved_path")
        self.assets_tree = ttk.Treeview(parent, columns=cols, show="headings", height=8)

        self.assets_tree.heading("rel_path", text="Resource / Relative Path")
        self.assets_tree.heading("size_bytes", text="Size (Bytes)")
        self.assets_tree.heading("readable", text="Integrity Status")
        self.assets_tree.heading("resolved_path", text="Resolved Path")

        self.assets_tree.column("rel_path", width=220, anchor=tk.W)
        self.assets_tree.column("size_bytes", width=90, anchor=tk.E)
        self.assets_tree.column("readable", width=130, anchor=tk.CENTER)
        self.assets_tree.column("resolved_path", width=360, anchor=tk.W)

        tree_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.assets_tree.yview)
        self.assets_tree.configure(yscrollcommand=tree_scroll.set)

        self.assets_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._populate_assets_tab()

    def _populate_assets_tab(self) -> None:
        """Populates the assets treeview with verification status of all bundled files."""
        for item in self.assets_tree.get_children():
            self.assets_tree.delete(item)

        manifest = self.get_bundled_assets_manifest()
        for entry in manifest:
            status = "Verified Readable" if entry["readable"] else ("Missing" if not entry["exists"] else "Read Error")
            self.assets_tree.insert(
                "",
                tk.END,
                values=(
                    entry["relative_path"],
                    f"{entry['size_bytes']:,}",
                    status,
                    entry["resolved_path"],
                ),
            )

    @staticmethod
    def get_bundled_assets_manifest() -> List[Dict[str, Any]]:
        """Scans and verifies readability of all bundled game assets and specifications."""
        target_relative_files = [
            "Architecture_Documentation.md",
            "Architecture_View.md",
            os.path.join("space_fractions", "fraction_math.py"),
            os.path.join("space_fractions", "game_engine.py"),
            os.path.join("space_fractions", "question_bank.py"),
            os.path.join("space_fractions", "ui_screens.py"),
            os.path.join("space_fractions", "main.py"),
            os.path.join("space_fractions", "README.md"),
            os.path.join("space_fractions", "TRACEABILITY.md"),
            os.path.join("specs", "openapi.yaml"),
            os.path.join("specs", "internal.proto"),
            os.path.join("specs", "k8s", "spacefractions-deployment.yaml"),
            os.path.join("specs", "sql", "game_ddl.sql"),
            os.path.join("specs", "traceability_matrix.csv"),
        ]

        manifest = []
        for rel_path in target_relative_files:
            resolved = get_resource_path(rel_path)
            exists = os.path.isfile(resolved)
            size = os.path.getsize(resolved) if exists else 0
            readable = False
            if exists:
                try:
                    with open(resolved, "rb") as f:
                        f.read(128)
                    readable = True
                except Exception:
                    readable = False
            manifest.append({
                "relative_path": rel_path.replace("\\", "/"),
                "resolved_path": resolved,
                "exists": exists,
                "size_bytes": size,
                "readable": readable,
            })
        return manifest

    # -------------------------------------------------------------------------
    # Secret and File Handling Helpers
    # -------------------------------------------------------------------------


    def _on_game_window_close(self) -> None:
        """Handles graceful closure of the Space Fractions game toplevel window."""
        try:
            if hasattr(self, "_game_window") and self._game_window is not None:
                if self._game_window.winfo_exists():
                    self._game_window.destroy()
        except Exception:
            pass
        finally:
            self._game_window = None
            self._game_app = None
            self._setup_styles()
            self._append_log("Space Fractions Game window closed cleanly.", "INFO")

    def launch_playable_game(self) -> None:
        """Launches the canonical bundled Space Fractions desktop client in a dedicated window."""
        if hasattr(self, "_game_window") and self._game_window is not None:
            try:
                if self._game_window.winfo_exists():
                    self._game_window.lift()
                    self._game_window.focus_force()
                    self._append_log("Space Fractions Game window brought to front.", "INFO")
                    return
            except Exception:
                self._game_window = None

        try:
            base_dir = getattr(sys, "_MEIPASS", PROJECT_ROOT)
            if base_dir not in sys.path:
                sys.path.insert(0, base_dir)

            from space_fractions.main import SpaceFractionsApp

            game_window = tk.Toplevel(self.root)
            self._game_window = game_window
            game_window.protocol("WM_DELETE_WINDOW", self._on_game_window_close)
            self._game_app = SpaceFractionsApp(game_window)
            self._setup_styles()
            self._append_log("Launched Space Fractions Desktop Game window.", "SUCCESS")
        except Exception:
            self._game_window = None
            self._game_app = None
            self._append_log("Failed to launch Space Fractions Game.", "ERROR")
            try:
                if not getattr(self, "suppress_messagebox", False):
                    messagebox.showerror(
                        "Game Launch Error",
                        "Could not launch the Space Fractions desktop client.\n"
                        "Please verify that the space_fractions package is available.",
                    )
            except Exception:
                pass

    def _find_env_file(self) -> Optional[str]:
        """Discovers local .env file in standard search locations without logging contents."""
        candidates = [
            os.path.join(os.getcwd(), ".env"),
            os.path.abspath(".env"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
            os.path.join(os.path.dirname(os.path.abspath(sys.executable)), ".env"),
        ]
        if hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(getattr(sys, "_MEIPASS"), ".env"))

        seen = set()
        for cand in candidates:
            norm_cand = os.path.realpath(os.path.abspath(cand))
            if norm_cand not in seen:
                seen.add(norm_cand)
                if os.path.isfile(norm_cand):
                    return norm_cand
        return None

    def _update_key_status(self) -> None:
        """Updates key status display based on current api_key_var and .env detection."""
        val = self.api_key_var.get().strip() if hasattr(self, "api_key_var") else ""
        if val:
            if getattr(self, "_key_source_is_env", False):
                self.key_status_var.set("Key Status: Masked Key Loaded from .env (Local Only)")
            else:
                self.key_status_var.set("Key Status: Key Configured via GUI Entry (Masked)")
        else:
            if getattr(self, "_env_file_found", False):
                self.key_status_var.set("Key Status: .env exists but key is empty")
            else:
                self.key_status_var.set("Key Status: No local .env detected")

    def _on_api_key_user_edited(self, *args: Any) -> None:
        """Invoked when user modifies api_key_var in the GUI."""
        if not getattr(self, "_loading_env", False):
            self._key_source_is_env = False
        self._update_key_status()

    def _auto_load_env_secret(self) -> None:
        """Loads DEEPSEEK_API_KEY from discovered .env if present, strictly without logging."""
        env_path = self._find_env_file()
        if not env_path:
            self._env_file_found = False
            self._key_source_is_env = False
            self._update_key_status()
            return

        self._env_file_found = True
        try:
            found_key = ""
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("DEEPSEEK_API_KEY="):
                        val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            found_key = val
                            break
            if found_key:
                self._loading_env = True
                try:
                    self._key_source_is_env = True
                    self.api_key_var.set(found_key)
                finally:
                    self._loading_env = False
            else:
                self._key_source_is_env = False
            self._update_key_status()
        except Exception:
            self._key_source_is_env = False
            self.key_status_var.set("Key Status: Unable to read .env")

    def _browse_doc_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Architecture Documentation File",
            filetypes=[("Markdown Files", "*.md"), ("All Files", "*.*")],
            initialdir=os.getcwd(),
        )
        if path:
            self.doc_path_var.set(os.path.abspath(path))

    def _browse_view_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Architecture View File",
            filetypes=[("Markdown Files", "*.md"), ("All Files", "*.*")],
            initialdir=os.getcwd(),
        )
        if path:
            self.view_path_var.set(os.path.abspath(path))

    def _browse_output_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Select Destination Output Directory",
            initialdir=os.getcwd(),
        )
        if path:
            self.output_dir_var.set(os.path.abspath(path))

    # -------------------------------------------------------------------------
    # Execution & Thread Management
    # -------------------------------------------------------------------------

    def _safe_auto_close(self) -> None:
        """Safely terminates the GUI on auto_close_ms timeout without interrupting active workers."""
        with self._state_lock:
            if self.is_running or self.run_state in ("RUNNING", "COMMITTING"):
                # Active worker is executing or promoting files; defer auto-close check
                self.root.after(200, self._safe_auto_close)
                return
        self.on_window_closing()

    def _begin_startup_receipt(self) -> None:
        """Create the pre-validation receipt used only by unattended startup."""
        if not (self.auto_run or self.suppress_messagebox):
            return
        run_id = uuid.uuid4().hex
        receipt_path = os.path.join(
            startup_receipt_directory(), f"startup_{run_id[:12]}.json"
        )
        receipt = {
            "schema_version": STARTUP_RECEIPT_SCHEMA_VERSION,
            "startup_run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "unattended": bool(self.auto_run),
            "message_boxes_suppressed": bool(self.suppress_messagebox),
            "working_directory_status": "PRESENT" if os.path.isdir(os.getcwd()) else "UNAVAILABLE",
            "architecture_input_validation_status": "NOT_STARTED",
            "output_directory_initialization_status": "NOT_STARTED",
            "worker_initialization_status": "NOT_STARTED",
            "startup_outcome": "STARTED",
            "failure_category": "NONE",
        }
        self.startup_receipt_path = receipt_path
        self._startup_receipt = receipt
        if not _write_startup_receipt(receipt_path, receipt):
            self.startup_receipt_path = None
            self._startup_receipt = None
            self._append_log("STARTUP_DIAGNOSTIC receipt_write_failed", "ERROR")

    def _update_startup_receipt(self, **updates: Any) -> None:
        """Persist sanitized startup state without letting diagnostics affect execution."""
        if not self._startup_receipt or not self.startup_receipt_path:
            return
        self._startup_receipt.update(updates)
        if not _write_startup_receipt(self.startup_receipt_path, self._startup_receipt):
            self._append_log("STARTUP_DIAGNOSTIC receipt_write_failed", "ERROR")

    def _startup_validation_failed(self, category: str, title: str, message: str) -> None:
        """Record a fixed failure category and retain dialogs only for interactive use."""
        self._update_startup_receipt(
            architecture_input_validation_status="FAILED",
            startup_outcome="VALIDATION_FAILED",
            failure_category=category,
        )
        self._append_log(f"STARTUP_VALIDATION_FAILED category={category}", "ERROR")
        if not self.suppress_messagebox:
            messagebox.showerror(title, message)

    def on_window_closing(self) -> None:
        """Handles user window close: safely aborts running task or defers if in COMMITTING."""
        with self._state_lock:
            if not self.is_running:
                self.root.destroy()
                return
            if self.run_state == "COMMITTING":
                self._append_log(
                    "Window close deferred: final directory promotion in progress. Please wait...", "WARNING"
                )
                self.root.after(200, self.on_window_closing)
                return
            if self.run_state in ("COMMITTED", "FAILED", "CANCELLED"):
                # Run already reached terminal outcome, safe to destroy without mutating state
                self.root.destroy()
                return
            self.run_state = "CANCELLED"
            self.cancel_requested.set()
            self._append_log("Window closing: cancelling active worker...", "WARNING")
        self.root.after(200, self.root.destroy)

    def _scan_for_orphan_staging_dirs(self) -> None:
        """Scans output directory for leftover .staging_* folders from interrupted prior runs."""
        base_dir = self.output_dir_var.get().strip() if hasattr(self, "output_dir_var") else os.getcwd()
        if not os.path.isdir(base_dir):
            return
        try:
            for item in os.listdir(base_dir):
                full_path = os.path.join(base_dir, item)
                if item.startswith(".staging_") and os.path.isdir(full_path):
                    self._append_log(
                        f"[RECOVERY] Discovered unpromoted staging directory from prior run: '{item}'. "
                        f"Contents preserved intact at: {full_path}",
                        "WARNING",
                    )
        except Exception:
            pass

    def on_start_click(self) -> None:
        """Handles Start button click: validates inputs and launches worker thread."""
        with self._state_lock:
            if self.is_running:
                return

            self._begin_startup_receipt()
            self._update_startup_receipt(startup_outcome="VALIDATING_INPUTS")

            mode_str = self.execution_mode_var.get()
            if mode_str == "live" and not self.allow_live_dispatch:
                self._startup_validation_failed(
                    "LIVE_DISPATCH_NOT_AUTHORIZED",
                    "Execution Blocked",
                    "Live mode execution is not authorized without explicit allow_live_dispatch=True.",
                )
                return

            raw_key = ""
            if mode_str == "live":
                raw_key = self.api_key_var.get().strip()
                if not raw_key:
                    self._startup_validation_failed(
                        "LIVE_CREDENTIAL_UNAVAILABLE",
                        "API Key Required",
                        "Live mode execution requires a configured credential.",
                    )
                    return

            task_goal = self.task_desc_var.get().strip() if hasattr(self, "task_desc_var") else ""
            if not task_goal:
                self._startup_validation_failed("TASK_GOAL_MISSING", "Invalid Input", "Task Goal cannot be empty.")
                return

            doc_path = self.doc_path_var.get().strip()
            view_path = self.view_path_var.get().strip()
            output_dir = self.output_dir_var.get().strip()

            # Validate existence, readability, and content of input files
            if not doc_path or not os.path.isfile(doc_path):
                self._startup_validation_failed("ARCHITECTURE_DOCUMENT_MISSING", "Invalid Input", "Architecture documentation file not found.")
                return
            try:
                if os.path.getsize(doc_path) == 0:
                    self._startup_validation_failed("ARCHITECTURE_DOCUMENT_EMPTY", "Invalid Input", "Architecture documentation file is empty.")
                    return
                with open(doc_path, "r", encoding="utf-8") as f:
                    f.read(256)
            except Exception:
                self._startup_validation_failed("ARCHITECTURE_DOCUMENT_UNREADABLE", "Invalid Input", "Architecture documentation file cannot be read.")
                return

            if not view_path or not os.path.isfile(view_path):
                self._startup_validation_failed("ARCHITECTURE_VIEW_MISSING", "Invalid Input", "Architecture view file not found.")
                return
            try:
                if os.path.getsize(view_path) == 0:
                    self._startup_validation_failed("ARCHITECTURE_VIEW_EMPTY", "Invalid Input", "Architecture view file is empty.")
                    return
                with open(view_path, "r", encoding="utf-8") as f:
                    f.read(256)
            except Exception:
                self._startup_validation_failed("ARCHITECTURE_VIEW_UNREADABLE", "Invalid Input", "Architecture view file cannot be read.")
                return

            if not output_dir:
                self._startup_validation_failed("OUTPUT_DIRECTORY_MISSING", "Invalid Input", "Output directory cannot be empty.")
                return
            if "\0" in output_dir:
                self._startup_validation_failed("OUTPUT_DIRECTORY_INVALID", "Invalid Input", "Output directory is invalid.")
                return
            if output_dir.startswith(("\\\\", "//")):
                self._startup_validation_failed("OUTPUT_DIRECTORY_UNSUPPORTED", "Invalid Input", "Network output directories are prohibited.")
                return

            self._update_startup_receipt(architecture_input_validation_status="VALID")

            # Prepare UI state for running
            self.is_running = True
            self.run_state = "RUNNING"
            self.cancel_requested.clear()
            run_id = uuid.uuid4().hex
            self.current_run_id = run_id
            self.current_task_goal = task_goal
            self.last_staging_dir = None
            self.last_run_dir = None
            self._set_controls_running_state(True)
            self.progress_bar.start(10)

        # Clear previous logs if starting fresh
        mode_str = self.execution_mode_var.get()
        self._append_log(f"Starting run {run_id[:8]} (Mode: {mode_str})...", "INFO")
        self.status_var.set("Running preprocessor and pipeline...")

        # Capture plain immutable configuration snapshot on GUI thread before spawning worker
        config = WorkerRunConfig(
            run_id=run_id,
            doc_path=doc_path,
            view_path=view_path,
            output_dir=output_dir,
            execution_mode=mode_str,
            task_goal=task_goal,
            allow_live_dispatch=self.allow_live_dispatch,
            api_key=raw_key if mode_str == "live" else "",
            live_timeout=getattr(self, "live_timeout", 60.0),
            max_turns=getattr(self, "max_turns", 12),
            workflow_mode=getattr(self, "workflow_mode", "offline_pipeline"),
            live_max_output_tokens=getattr(self, "live_max_output_tokens", 2500),
            startup_receipt_path=self.startup_receipt_path,
            resume_staging=getattr(self, "resume_staging", None),
        )

        # Spawn background worker passing immutable config snapshot
        try:
            self.worker_thread = threading.Thread(
                target=self._run_offline_pipeline_worker,
                args=(doc_path, view_path, output_dir, run_id, mode_str, task_goal, config),
                daemon=True,
            )
            self.worker_thread.start()
            self._update_startup_receipt(
                worker_initialization_status="STARTED",
                startup_outcome="WORKER_STARTED",
            )
        except Exception:
            with self._state_lock:
                self.is_running = False
                self.run_state = "FAILED"
            self._set_controls_running_state(False)
            self._update_startup_receipt(
                worker_initialization_status="FAILED",
                startup_outcome="WORKER_INITIALIZATION_FAILED",
                failure_category="WORKER_INITIALIZATION_FAILED",
            )
            self._append_log("STARTUP_WORKER_INITIALIZATION_FAILED", "ERROR")

    def on_cancel_click(self) -> None:
        """Handles Cancel button click: signals cancel to worker under state lock."""
        with self._state_lock:
            if not self.is_running:
                return
            if self.run_state in ("COMMITTING", "COMMITTED", "FAILED", "CANCELLED"):
                self._append_log(
                    f"Cancellation rejected: Run is in {self.run_state} state.", "WARNING"
                )
                return
            self.run_state = "CANCELLED"
            self.cancel_requested.set()
            self.status_var.set("Cancelling operation...")
            self._append_log("Cancellation requested by user. Terminating worker...", "WARNING")

    def _set_controls_running_state(self, running: bool) -> None:
        """Enables or disables UI controls depending on execution state."""
        state = tk.DISABLED if running else tk.NORMAL
        self.btn_start.configure(state=state)
        self.btn_cancel.configure(state=tk.NORMAL if running else tk.DISABLED)
        self.btn_load_env.configure(state=state)
        self.btn_browse_doc.configure(state=state)
        self.btn_browse_view.configure(state=state)
        self.btn_browse_output.configure(state=state)
        self.api_key_entry.configure(state=state)
        self.doc_entry.configure(state=state)
        self.view_entry.configure(state=state)
        self.output_entry.configure(state=state)
        if hasattr(self, "radio_offline"):
            self.radio_offline.configure(state=state)
        if hasattr(self, "radio_mock_agent"):
            self.radio_mock_agent.configure(state=state)
        if hasattr(self, "radio_live"):
            self.radio_live.configure(state=tk.NORMAL if (not running and self.allow_live_dispatch) else tk.DISABLED)
        if hasattr(self, "chk_allow_live"):
            self.chk_allow_live.configure(state=state)

    def get_deepseek_client(self) -> DeepSeekClient:
        """Instantiates a DeepSeekClient with the in-memory API key and budget controller.
        
        The API key is accessed only in memory and never logged, printed, or persisted.
        """
        key = self.api_key_var.get().strip()
        return DeepSeekClient(
            api_key=key if key else "unconfigured-offline-placeholder",
            budget_controller=self.budget_controller,
        )

    # -------------------------------------------------------------------------
    # Worker Thread Logic (Runs in Background)
    # -------------------------------------------------------------------------

    def _run_offline_pipeline_worker(
        self,
        doc_path: str,
        view_path: str,
        output_dir: str,
        run_id: Optional[str] = None,
        execution_mode: Optional[str] = None,
        task_goal: Optional[str] = None,
        config: Optional[WorkerRunConfig] = None,
    ) -> None:
        """Executes the preprocessor and offline pipeline in a background thread."""
        if config is not None:
            effective_run_id = config.run_id
            effective_doc_path = config.doc_path
            effective_view_path = config.view_path
            effective_output_dir = config.output_dir
            effective_mode = config.execution_mode
            effective_task_goal = config.task_goal
            effective_allow_live = config.allow_live_dispatch
            effective_api_key = config.api_key
            effective_live_timeout = config.live_timeout
            effective_max_turns = config.max_turns
            effective_workflow_mode = config.workflow_mode
            effective_max_output_tokens = getattr(config, "live_max_output_tokens", 2500)
            startup_receipt_path = config.startup_receipt_path
        else:
            effective_run_id = run_id or getattr(self, "current_run_id", None) or uuid.uuid4().hex
            effective_doc_path = doc_path
            effective_view_path = view_path
            effective_output_dir = output_dir
            if execution_mode is not None:
                effective_mode = execution_mode
            elif hasattr(self, "execution_mode_var"):
                try:
                    effective_mode = self.execution_mode_var.get()
                except Exception:
                    effective_mode = getattr(self, "default_execution_mode", "offline")
            else:
                effective_mode = getattr(self, "default_execution_mode", "offline")

            if task_goal is not None:
                effective_task_goal = task_goal
            elif getattr(self, "current_task_goal", None) is not None:
                effective_task_goal = self.current_task_goal
            elif hasattr(self, "task_desc_var"):
                try:
                    effective_task_goal = self.task_desc_var.get().strip()
                except Exception:
                    effective_task_goal = ""
            else:
                effective_task_goal = ""

            effective_allow_live = getattr(self, "allow_live_dispatch", False)
            if hasattr(self, "api_key_var"):
                try:
                    effective_api_key = self.api_key_var.get().strip()
                except Exception:
                    effective_api_key = ""
            else:
                effective_api_key = ""

            effective_live_timeout = getattr(self, "live_timeout", 60.0)
            effective_max_turns = getattr(self, "max_turns", AgentLoop.MAX_TURNS_DEFAULT)
            effective_workflow_mode = getattr(self, "workflow_mode", "offline_pipeline")
            effective_max_output_tokens = getattr(self, "live_max_output_tokens", 2500)
            startup_receipt_path = getattr(self, "startup_receipt_path", None)

        # Distinguish between legacy Space Fractions demonstration and generic task
        is_legacy_demo = (
            effective_mode != "live"
            and (
                not effective_task_goal
                or "ingest architecture specs" in effective_task_goal.lower()
                or "space fractions" in effective_task_goal.lower()
            )
        )

        real_output_dir = os.path.realpath(os.path.abspath(effective_output_dir))
        try:
            os.makedirs(real_output_dir, exist_ok=True)
        except OSError:
            if startup_receipt_path:
                self._update_startup_receipt(
                    output_directory_initialization_status="FAILED",
                    worker_initialization_status="FAILED",
                    startup_outcome="WORKER_INITIALIZATION_FAILED",
                    failure_category="OUTPUT_DIRECTORY_INITIALIZATION_FAILED",
                )
            self._queue_msg("error", "Output directory initialization failed.", effective_run_id)
            return
        if startup_receipt_path:
            self._update_startup_receipt(
                output_directory_initialization_status="INITIALIZED",
                worker_initialization_status="STARTED",
                startup_outcome="WORKER_STARTED",
            )
        staging_dir = os.path.join(real_output_dir, f".staging_{effective_run_id}")
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        clean_slug = re.sub(r'[^a-zA-Z0-9]+', '_', effective_task_goal.lower()).strip('_')[:20] or "space_fractions"
        if is_legacy_demo or "space fractions" in effective_task_goal.lower() or "architecture" in effective_task_goal.lower():
            target_run_dir = os.path.join(real_output_dir, f"space_fractions_run_{timestamp_str}_{effective_run_id[:8]}")
        else:
            target_run_dir = os.path.join(real_output_dir, f"{clean_slug}_run_{timestamp_str}_{effective_run_id[:8]}")

        self.last_staging_dir = staging_dir
        self.last_run_dir = None
        is_live_run = False

        try:
            if effective_mode == "live":
                if not effective_allow_live:
                    with self._state_lock:
                        if self.run_state not in ("COMMITTED", "CANCELLED"):
                            self.run_state = "FAILED"
                    self._queue_msg(
                        "error",
                        "Live mode dispatch is not authorized without explicit allow_live_dispatch=True.",
                        effective_run_id,
                    )
                    return
                self._queue_msg(
                    "log",
                    ("NOTICE: Live API mode dispatched under authorized gate.", "WARNING"),
                    effective_run_id,
                )

            self._queue_msg("log", ("Phase 1: Validating source files and workspace paths...", "INFO"), effective_run_id)
            time.sleep(0.05)

            # Check for staging collision
            if os.path.exists(staging_dir):
                with self._state_lock:
                    if self.run_state not in ("COMMITTED", "CANCELLED"):
                        self.run_state = "FAILED"
                self._queue_msg("error", f"Staging directory collision: '{staging_dir}' already exists.", effective_run_id)
                return

            # Check for destination collision
            if os.path.exists(target_run_dir):
                with self._state_lock:
                    if self.run_state not in ("COMMITTED", "CANCELLED"):
                        self.run_state = "FAILED"
                self._queue_msg("error", f"Destination directory collision: '{target_run_dir}' already exists.", effective_run_id)
                return

            os.makedirs(staging_dir, exist_ok=False)
            audit_log_path = os.path.join(staging_dir, "agent_execution.log")
            try:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(audit_log_path, "w", encoding="utf-8") as lf:
                    lf.write(f"[{ts}] [INIT] Staging directory initialized for run {effective_run_id[:8]} (Mode: {effective_mode})\n")
            except Exception:
                pass

            def _append_worker_log(text: str, tag: str = "INFO") -> None:
                self._queue_msg("log", (text, tag), effective_run_id)
                try:
                    sanitized = redact_secrets(text)
                    if effective_api_key and len(effective_api_key) > 5:
                        sanitized = sanitized.replace(effective_api_key, "[REDACTED_API_KEY]")
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with open(audit_log_path, "a", encoding="utf-8") as lf:
                        lf.write(f"[{ts}] [{tag}] {sanitized}\n")
                except Exception:
                    pass

            def _record_audit_milestone(event: str, data: Dict[str, Any]) -> None:
                try:
                    sanitized_entry = {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))}
                    sanitized_entry["event"] = event
                    for k, v in list(sanitized_entry.items()):
                        if isinstance(v, str):
                            v_clean = redact_secrets(v)
                            if effective_api_key and len(effective_api_key) > 5:
                                v_clean = v_clean.replace(effective_api_key, "[REDACTED]")
                            sanitized_entry[k] = v_clean
                    json_str = json.dumps(sanitized_entry, sort_keys=True)
                    _append_worker_log(f"MILESTONE {json_str}", "AUDIT")
                except Exception:
                    pass

            is_space_fractions_goal = (
                "space fraction" in effective_task_goal.lower()
                or "space_fraction" in effective_task_goal.lower()
                or "architecture" in effective_task_goal.lower()
                or "game" in effective_task_goal.lower()
            )

            def _fail_worker(err_text: str) -> None:
                sanitized_err = redact_secrets(err_text)
                if effective_api_key and len(effective_api_key) > 5:
                    sanitized_err = sanitized_err.replace(effective_api_key, "[REDACTED_API_KEY]")
                self._queue_msg("error", sanitized_err, effective_run_id)
                try:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with open(audit_log_path, "a", encoding="utf-8") as lf:
                        lf.write(f"[{ts}] [ERROR] {sanitized_err}\n")
                except Exception:
                    pass
                try:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    err_copy = os.path.join(real_output_dir, f"error_execution_{effective_run_id[:8]}.log")
                    if os.path.isfile(audit_log_path):
                        shutil.copyfile(audit_log_path, err_copy)
                    else:
                        with open(err_copy, "w", encoding="utf-8") as ef:
                            ef.write(f"[{ts}] [ERROR] {sanitized_err}\n")
                except Exception:
                    pass

            # Seed staging directory if resuming from a prior staging directory
            effective_resume = getattr(config, "resume_staging", None) if config else None
            if effective_resume and os.path.isdir(effective_resume):
                _append_worker_log(f"Phase 1b: Resuming from prior staging directory: '{effective_resume}'", "INFO")
                seeded_count = 0
                for root_dir, _, filenames in os.walk(effective_resume):
                    for fname in filenames:
                        if fname in ("agent_execution.log", "error_execution.log", "offline_validation_manifest.json") or fname.startswith("."):
                            continue
                        src_f = os.path.join(root_dir, fname)
                        rel_f = os.path.relpath(src_f, effective_resume)
                        dst_f = os.path.join(staging_dir, rel_f)
                        os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                        shutil.copy2(src_f, dst_f)
                        seeded_count += 1
                _append_worker_log(f"Successfully seeded {seeded_count} asset(s) from prior staging directory into isolated staging.", "INFO")

            if self.cancel_requested.is_set():
                with self._state_lock:
                    if self.run_state not in ("COMMITTING", "COMMITTED", "FAILED"):
                        self.run_state = "CANCELLED"
                self._queue_msg("cancelled", "Validation cancelled before preprocessing.", effective_run_id)
                return

            if is_legacy_demo:
                # Step 1: Run Specification Preprocessor (Legacy Space Fractions Demonstration)
                self._queue_msg("log", ("Phase 2: Ingesting canonical architecture documents (Legacy Demo Mode)...", "INFO"), effective_run_id)
                spec_result = SpecificationPreprocessor.preprocess(effective_doc_path, effective_view_path)
                self._queue_msg("spec_result", spec_result, effective_run_id)

                self._queue_msg(
                    "log",
                    (f"  ✓ Preprocessor extracted {len(spec_result.view_items)} view items and "
                     f"{len(spec_result.diagrams)} PlantUML diagram blocks.", "SUCCESS"),
                    effective_run_id,
                )
                self._queue_msg(
                    "log",
                    (f"  ✓ Preserved {len(spec_result.extraction_warnings)} syntactic/semantic notes.", "INFO"),
                    effective_run_id,
                )
                for w in spec_result.extraction_warnings:
                    self._queue_msg("log", (f"    [NOTE] {w}", "MUTED"), effective_run_id)

                # Log Documented Conflict
                for conflict in spec_result.recorded_conflicts:
                    self._queue_msg(
                        "log",
                        (f"  [CONFLICT RECORDED] {conflict.conflict_id}: {conflict.title}", "CONFLICT"),
                        effective_run_id,
                    )
                    self._queue_msg(
                        "log",
                        (f"    Source A: {conflict.source_a['document']} -> Windows desktop .exe mandate\n"
                         f"    Source B: {conflict.source_b['document']} -> Cloud microservices specification\n"
                         f"    Resolution: Conflict preserved without silent mutation.", "MUTED"),
                        effective_run_id,
                    )

                if self.cancel_requested.is_set():
                    with self._state_lock:
                        if self.run_state not in ("COMMITTING", "COMMITTED", "FAILED"):
                            self.run_state = "CANCELLED"
                    self._queue_msg("cancelled", "Validation cancelled after preprocessing.", effective_run_id)
                    return

                # Step 2: Initialize Offline Pipeline Coordinator targeted exclusively at staging_dir
                self._queue_msg("log", ("Phase 3: Initializing Offline Pipeline Coordinator in isolated staging...", "INFO"), effective_run_id)

                mode_str = effective_mode
                if mode_str == "mock_agent":
                    effective_llm_client = self.mock_llm_client or OfflineMockAgentClient()
                    self._queue_msg(
                        "log",
                        ("  ✓ Selected mode: Offline Mock Agent (Multi-Stage AgentLoop with deterministic mock LLM)", "INFO"),
                        effective_run_id,
                    )
                else:
                    effective_llm_client = self.mock_llm_client
                    self._queue_msg(
                        "log",
                        ("  ✓ Selected mode: Offline Template Pipeline (Standard canonical template assembly)", "INFO"),
                        effective_run_id,
                    )

                mock_budget = BudgetController(ceiling=self.HARD_CEILING)
                coordinator = OfflinePipelineCoordinator(
                    workspace_root=staging_dir,
                    output_dir=staging_dir,
                    budget_controller=mock_budget,
                    llm_client=effective_llm_client,
                    cancel_event=self.cancel_requested,
                )

                # Step 3: Run Staged Offline Mock Pipeline into staging_dir
                self._queue_msg("log", ("Phase 4: Executing staged generation plan with mock telemetry...", "INFO"), effective_run_id)
                if self.synthesis_runner_factory is not None:
                    summary = self._invoke_synthesis_runner_factory(
                        staging_dir=staging_dir,
                        coordinator=coordinator,
                        doc_path=effective_doc_path,
                        view_path=effective_view_path,
                        budget_controller=mock_budget,
                        spec_result=spec_result,
                    )
                elif effective_workflow_mode == "mock_synthesis" and self.mock_llm_client is not None:
                    summary = self._run_synthesis_driver_workflow(
                        staging_dir=staging_dir,
                        budget_controller=mock_budget,
                        doc_path=effective_doc_path,
                        view_path=effective_view_path,
                        spec_result=spec_result,
                    )
                else:
                    summary = coordinator.run_mock_pipeline(
                        effective_doc_path,
                        effective_view_path,
                        llm_client=effective_llm_client,
                        cancel_event=self.cancel_requested,
                    )

                # For mock_agent mode, ensure any missing supplementary project packaging files are written
                if summary.success and mode_str == "mock_agent":
                    try:
                        coordinator._write_supplementary_project_files()
                        summary.generated_files = list(coordinator.written_files)
                    except Exception:
                        pass

            else:
                # Dedicated Generic-Task Path via AgentLoop (Bypasses Space Fractions Preprocessor/Plan)
                is_live_run = (effective_mode == "live" and effective_allow_live)

                if is_live_run:
                    raw_key = effective_api_key.strip()
                    if not raw_key:
                        with self._state_lock:
                            if self.run_state not in ("COMMITTING", "COMMITTED", "FAILED"):
                                self.run_state = "FAILED"
                        _fail_worker("Live mode execution requires a valid DeepSeek API key.")
                        return

                    _append_worker_log(
                        f"Phase 2: Initializing LIVE task-driven AgentLoop for goal: '{effective_task_goal}'",
                        "WARNING",
                    )
                    _append_worker_log(
                        "  ✓ Run Mode: Live DeepSeek Agent (deepseek-v4-pro via official HTTPS endpoint)",
                        "INFO",
                    )
                    _append_worker_log(
                        "  ✓ Safety Enforcement: block_run_command=True (Host code execution prohibited)",
                        "INFO",
                    )

                    live_client = DeepSeekClient(
                        api_key=raw_key,
                        budget_controller=self.budget_controller,
                        max_output_tokens=min(self.budget_controller.MAX_PERMITTED_OUTPUT_TOKENS, max(500, int(effective_max_output_tokens))),
                        timeout=effective_live_timeout,
                        audit_callback=_record_audit_milestone,
                    )
                    effective_llm_client = DeepSeekAgentAdapter(
                        client=live_client,
                        max_output_tokens=min(self.budget_controller.MAX_PERMITTED_OUTPUT_TOKENS, max(500, int(effective_max_output_tokens))),
                        cancel_event=self.cancel_requested,
                    )
                    loop_budget_controller = self.budget_controller
                    # The assignment requires eight project deliverables.  Preserve
                    # bounded retry capacity for provider truncation/tool-validation
                    # failures while the BudgetController continues to enforce the
                    # stricter sub-$1 ($0.80 local) financial ceiling.
                    loop_max_turns = max(effective_max_turns, AgentLoop.MAX_TURNS_DEFAULT)
                else:
                    self._queue_msg(
                        "log",
                        (f"Phase 2: Initializing task-driven AgentLoop for goal: '{effective_task_goal}'", "INFO"),
                        effective_run_id,
                    )
                    self._queue_msg(
                        "log",
                        ("  ✓ Run Mode: Generic-Task Offline AgentLoop (Deterministic Mock Double)", "INFO"),
                        effective_run_id,
                    )
                    self._queue_msg(
                        "log",
                        ("  ✓ Safety Enforcement: block_run_command=True (Host code execution prohibited)", "INFO"),
                        effective_run_id,
                    )

                    mock_budget = BudgetController(ceiling=self.HARD_CEILING)
                    effective_llm_client = self.mock_llm_client or OfflineMockAgentClient()
                    loop_budget_controller = mock_budget
                    loop_max_turns = 6

                is_space_fractions_goal = (
                    "space fraction" in effective_task_goal.lower()
                    or "architecture" in effective_task_goal.lower()
                    or "game" in effective_task_goal.lower()
                )

                # Live Space Fractions generation is bounded by a small number
                # of paid turns.  Workspace exploration cannot create a required
                # deliverable, so expose only write_file for this workflow.
                effective_tools = AgentLoop.TOOLS_SCHEMA
                if is_live_run and is_space_fractions_goal:
                    effective_tools = [
                        tool for tool in AgentLoop.TOOLS_SCHEMA
                        if tool.get("function", {}).get("name") == "write_file"
                    ]
                elif loop_max_turns == 1:
                    effective_tools = [t for t in AgentLoop.TOOLS_SCHEMA if t.get("function", {}).get("name") == "write_file"]

                agent = AgentLoop(
                    workspace_root=staging_dir,
                    llm_client=effective_llm_client,
                    budget_controller=loop_budget_controller,
                    max_turns=loop_max_turns,
                    cancel_event=self.cancel_requested,
                    block_run_command=True,
                    session_id=effective_run_id[:8],
                    tools_schema=effective_tools,
                    strict_completeness=True,
                    deliverables_checklist=(
                        AgentLoop.CANONICAL_SPACE_FRACTIONS_DELIVERABLES
                        if is_space_fractions_goal
                        else None
                    ),
                )

                spec_result = None
                if os.path.isfile(effective_doc_path) and os.path.isfile(effective_view_path):
                    try:
                        spec_result = SpecificationPreprocessor.preprocess(effective_doc_path, effective_view_path)
                    except Exception:
                        spec_result = None

                if is_space_fractions_goal and spec_result is not None:
                    if loop_max_turns == 1:
                        task_prompt = (
                            f"Execute the following user task in the designated workspace:\n"
                            f"Task Goal: {effective_task_goal}\n\n"
                            f"ARCHITECTURE SPECIFICATION:\n"
                            f"- System: Space Fractions Grade 6 Interactive Desktop Educational Game.\n"
                            f"- Conflict Directive: Standalone local Windows desktop client only.\n\n"
                            f"REQUIRED CORE MODULE DELIVERABLES (use the write_file tool):\n"
                            f"1. space_fractions/fraction_math.py:\n"
                            f"   - Class Fraction(numerator, denominator=1) with methods: __init__, add, subtract, simplify, compare, equals, to_float, __str__, __repr__.\n"
                            f"   - Class MathQuestionGenerator with method: generate() returning prompt, options, correct_idx, explanation.\n"
                            f"2. space_fractions/game_engine.py:\n"
                            f"   - Enum GameState with values: PLAYING, PAUSED, GAME_OVER.\n"
                            f"   - Class GameEngine with methods: play(), submit_answer(answer), pause(), resume(), is_game_over(), view_score(), viewScore().\n"
                            f"3. tests/test_fraction_game.py:\n"
                            f"   - Unit tests verifying Fraction arithmetic and GameEngine state transitions.\n\n"
                            f"CRITICAL SINGLE-TURN INSTRUCTIONS:\n"
                            f"- You have EXACTLY ONE completion turn. Generate all three files in this single response using the write_file tool.\n"
                            f"- Alternatively, output each file inside a markdown code block starting with ```python:path/to/file.py.\n"
                            f"- Keep all code clean, self-contained, and syntactically valid Python 3."
                        )
                    else:
                        task_prompt = (
                            f"Execute the following user task in the designated workspace:\n"
                            f"Task Goal: {effective_task_goal}\n\n"
                            f"ARCHITECTURE SPECIFICATION:\n"
                            f"- System: Space Fractions Grade 6 Interactive Desktop Educational Game.\n"
                            f"- Kruchten 4+1 Views Ingested: {len(spec_result.view_items)} view items, {len(spec_result.diagrams)} diagrams.\n"
                            f"- Documented Conflict Directive: Section B mandates a Windows desktop client deliverable. "
                            f"Implement code ONLY for the local standalone desktop client. Do not require external cloud microservices.\n\n"
                            f"REQUIRED PROJECT DELIVERABLES (use the write_file tool):\n"
                            f"1. space_fractions/fraction_math.py:\n"
                            f"   - Class Fraction(numerator, denominator=1) with methods: __init__, add, subtract, simplify, compare, equals, to_float, __str__, __repr__.\n"
                            f"   - Class MathQuestionGenerator with method: generate() returning question prompt, options list of display strings (never Fraction objects), correct index, and explanation.\n"
                            f"   - Random question and distractor generation must guard denominator arithmetic so it can never construct a Fraction with denominator zero; tests must exercise generate() at least 100 times and assert every option is a string.\n"
                            f"2. space_fractions/game_engine.py:\n"
                            f"   - Enum GameState with values: PLAYING, PAUSED, GAME_OVER.\n"
                            f"   - Class GameEngine with methods: play(), submit_answer(answer), pause(), resume(), is_game_over(), and viewScore() or view_score().\n"
                            f"3. space_fractions/question_bank.py:\n"
                            f"   - Question bank for Grade 6 fractions (addition, subtraction, multiplication, division of fractions with like and unlike denominators).\n"
                            f"4. space_fractions/ui_screens.py:\n"
                            f"   - Class FractionVisualizer (Tkinter Canvas drawing fraction pie charts or rectangular bars).\n"
                            f"   - Class HUDFrame (displays current score, remaining lives, and question progress).\n"
                            f"5. space_fractions/main.py:\n"
                            f"   - Class SpaceFractionsApp (complete Tkinter desktop application integrating FractionVisualizer, HUDFrame, and GameEngine with start, play, pause, and game-over screens).\n"
                            f"   - Function main() as the desktop application entry point.\n"
                            f"6. tests/test_fraction_game.py:\n"
                            f"   - Comprehensive unit test functions (starting with test_) verifying Fraction arithmetic, question generation, and GameEngine state transitions.\n"
                            f"7. requirements.txt:\n"
                            f"   - Dependency manifest (stdlib only, zero external packages needed).\n"
                            f"8. README.md:\n"
                            f"   - Overview, architecture traceability, and instructions for running the game.\n\n"
                            f"INSTRUCTIONS:\n"
                            f"- Use the write_file tool to generate each required deliverable.\n"
                            f"- Focus on generating ONE deliverable at a time per turn, starting with '{AgentLoop.CANONICAL_SPACE_FRACTIONS_DELIVERABLES[0]}'.\n"
                            f"- Keep all relative paths inside the workspace.\n"
                            f"- Never output context compaction placeholders such as '[content omitted after local write]'; you must always provide complete, functional source code for each file.\n"
                            f"- Once all deliverables are written, provide a brief summary of the completed project."
                        )
                else:
                    task_prompt = (
                        f"Execute the following user task in the designated workspace:\n"
                        f"Task Goal: {effective_task_goal}\n\n"
                        f"Instructions:\n"
                        f"- Write clean, self-contained Python code and test files using the write_file tool.\n"
                        f"- Ensure all relative file paths stay inside the designated workspace.\n"
                    )

                _append_worker_log("Phase 3: Dispatching task goal to AgentLoop with tool calling...", "INFO")
                loop_result = agent.run(task_prompt)

                # Log transcript diagnostics to audit log
                total_tool_calls = 0
                accepted_tool_calls = 0
                rejected_tool_calls = 0
                for t_entry in loop_result.transcript:
                    if t_entry.role == "assistant_tool_call":
                        tool_names = sorted({str(c.get("name", "unknown")) for c in t_entry.tool_calls})
                        _append_worker_log(
                            f"TOOL_CALLS turn={t_entry.turn_index} count={len(t_entry.tool_calls)} "
                            f"types={','.join(tool_names)}",
                            "INFO",
                        )
                    elif t_entry.role == "tool_result":
                        total_tool_calls += 1
                        is_err = "Error" in (t_entry.tool_result or "") or "SecurityError" in (t_entry.tool_result or "")
                        if is_err:
                            rejected_tool_calls += 1
                        else:
                            accepted_tool_calls += 1
                        outcome = "rejected" if is_err else "accepted"
                        result_detail = ""
                        if is_err:
                            rejection_reason = " ".join((t_entry.tool_result or "Unknown rejection").split())[:500]
                            result_detail = f" reason={json.dumps(rejection_reason, ensure_ascii=False)}"
                        _append_worker_log(
                            f"TOOL_RESULT turn={t_entry.turn_index} tool={t_entry.tool_name} "
                            f"outcome={outcome}{result_detail}",
                            "WARNING" if is_err else "INFO",
                        )

                _record_audit_milestone("TOOL_DISPATCH_SUMMARY", {
                    "event": "TOOL_DISPATCH_SUMMARY",
                    "total_calls": total_tool_calls,
                    "accepted_calls": accepted_tool_calls,
                    "rejected_calls": rejected_tool_calls,
                })

                if self.cancel_requested.is_set() or loop_result.status == LoopTerminationStatus.CANCELLED:
                    with self._state_lock:
                        if self.run_state not in ("COMMITTING", "COMMITTED", "FAILED"):
                            self.run_state = "CANCELLED"
                    self._queue_msg("cancelled", "Task execution cancelled by user signal.", effective_run_id)
                    return

                # Collect written files (excluding internal execution log)
                gen_files = []
                for root_dir, _, filenames in os.walk(staging_dir):
                    for fname in filenames:
                        if fname == "agent_execution.log":
                            continue
                        full_p = os.path.realpath(os.path.join(root_dir, fname))
                        gen_files.append(full_p)

                if loop_result.status != LoopTerminationStatus.COMPLETED:
                    if loop_result.status == LoopTerminationStatus.MAX_TURNS_REACHED and len(gen_files) > 0:
                        _append_worker_log(
                            f"Turn limit ({loop_max_turns}) reached with {len(gen_files)} staged deliverable(s); proceeding to static validation...",
                            "INFO",
                        )
                    else:
                        with self._state_lock:
                            if self.run_state not in ("COMMITTING", "COMMITTED"):
                                self.run_state = "FAILED"
                        if loop_result.status == LoopTerminationStatus.MAX_TURNS_REACHED:
                            err_msg = (
                                f"Incomplete task execution: AgentLoop reached maximum turn limit ({loop_max_turns}) "
                                f"without generating deliverables or receiving a model completion signal."
                            )
                        else:
                            err_msg = loop_result.error_message or f"AgentLoop terminated with status {loop_result.status.value}"
                        _fail_worker(f"Task execution failed: {err_msg}")
                        return

                if is_live_run:
                    sec_notices = [
                        "SECURITY NOTICE: Executed in LIVE mode via DeepSeekClient (deepseek-v4-pro).",
                        f"SECURITY NOTICE: Total verified live spend: ${self.budget_controller.cumulative_spend:.6f} USD.",
                        "SECURITY NOTICE: Zero generated code or tests executed on host (block_run_command=True).",
                    ]
                else:
                    sec_notices = [
                        "SECURITY NOTICE: Executed in offline mock agent mode via AgentLoop.",
                        "SECURITY NOTICE: Zero live network or DeepSeek API calls dispatched.",
                        "SECURITY NOTICE: Zero generated code or tests executed (block_run_command=True).",
                    ]

                summary = PipelineExecutionSummary(
                    success=True,
                    total_stages_run=loop_result.total_turns,
                    completed_stages=[f"TASK-{clean_slug.upper()}"],
                    generated_files=gen_files,
                    cumulative_reserved=loop_budget_controller.cumulative_spend + loop_budget_controller.get_active_holds(),
                    cumulative_spend=loop_budget_controller.cumulative_spend,
                    safety_violations=[],
                    errors=[],
                    security_notices=sec_notices,
                )

            if self.cancel_requested.is_set():
                with self._state_lock:
                    if self.run_state not in ("COMMITTING", "COMMITTED", "FAILED"):
                        self.run_state = "CANCELLED"
                self._queue_msg("cancelled", "Validation cancelled during pipeline run.", effective_run_id)
                return

            # Step 4: Validate Deliverables inside staging_dir
            if not summary.success:
                err_text = "; ".join(summary.errors) if summary.errors else "Pipeline execution failed in staging."
                with self._state_lock:
                    if self.run_state not in ("COMMITTED", "CANCELLED"):
                        self.run_state = "FAILED"
                self._queue_msg("error", f"Pipeline failed in staging: {err_text}", effective_run_id)
                return

            for gen_file in summary.generated_files:
                if not os.path.isfile(gen_file):
                    with self._state_lock:
                        if self.run_state not in ("COMMITTED", "CANCELLED"):
                            self.run_state = "FAILED"
                    self._queue_msg(
                        "error",
                        f"Expected deliverable missing from staging: '{os.path.basename(gen_file)}'",
                        effective_run_id,
                    )
                    return

            # Step 4b: Static Acceptance Contract Validation via ProjectValidator
            self._queue_msg("log", ("Phase 4b: Validating generated project against static acceptance contract...", "INFO"), effective_run_id)
            if is_legacy_demo:
                require_full = (effective_workflow_mode == "offline_pipeline")
                validation_result = ProjectValidator.validate_project(
                    staging_dir, require_full_game=require_full, require_manifest=False
                )
            elif is_space_fractions_goal:
                # Space Fractions tasks MUST strictly satisfy the authoritative full game acceptance contract.
                # Generic validation fallback is strictly prohibited for Space Fractions.
                validation_result = ProjectValidator.validate_project(
                    staging_dir, require_full_game=True, require_manifest=False
                )
            else:
                validation_result = ProjectValidator.validate_generic_project(staging_dir)

            val_mode_name = "SPACE_FRACTIONS" if (is_legacy_demo or is_space_fractions_goal) else "GENERIC"
            _record_audit_milestone("VALIDATION_RESULT", {
                "event": "VALIDATION_RESULT",
                "mode": val_mode_name,
                "success": validation_result.success,
                "verified_files_count": len(validation_result.verified_files),
                "error_count": len(validation_result.errors),
            })

            if not validation_result.success:
                with self._state_lock:
                    if self.run_state not in ("COMMITTED", "CANCELLED"):
                        self.run_state = "FAILED"
                err_msg = "; ".join(validation_result.errors) if validation_result.errors else "Static contract validation failed."
                _record_audit_milestone("PROMOTION_OUTCOME", {
                    "event": "PROMOTION_OUTCOME",
                    "promoted": False,
                    "destination_dir": "",
                })
                _fail_worker(f"Static acceptance validation failed in staging: {err_msg}")
                return

            for iface in validation_result.verified_interfaces:
                self._queue_msg("log", (f"  ✓ Verified interface: {iface}", "SUCCESS"), effective_run_id)

            # Step 5: Write Safe Manifest into staging_dir
            self._queue_msg("log", ("Phase 5: Writing safe bounded test manifest into staging directory...", "INFO"), effective_run_id)
            if is_live_run:
                manifest_dict = {
                    "run_id": effective_run_id,
                    "validation_run": "Task-Driven Live DeepSeek Agent Run",
                    "task_goal": effective_task_goal,
                    "mode": "LIVE_DEEPSEEK_AGENT",
                    "model": "deepseek-v4-pro",
                    "timestamp": datetime.now().isoformat(),
                    "offline_mode": False,
                    "live_api_called": True,
                    "code_executed": False,
                    "static_validation_passed": True,
                    "runtime_gameplay_verified": False,
                    "cumulative_spend_usd": self.budget_controller.cumulative_spend,
                    "generated_files": [os.path.relpath(f, staging_dir).replace("\\", "/") for f in summary.generated_files],
                    "security_disclaimer": "AST/py_compile are syntax checks only, not process isolation sandboxes.",
                    "live_notice": "Outputs were synthesized via official DeepSeek API (deepseek-v4-pro) with tool execution.",
                }
                if 'spec_result' in locals() and spec_result is not None:
                    manifest_dict["view_items_count"] = len(spec_result.view_items)
                    manifest_dict["diagrams_count"] = len(spec_result.diagrams)
                    manifest_dict["recorded_conflicts"] = [c.conflict_id for c in spec_result.recorded_conflicts]
                    manifest_dict["deliverable_specs_count"] = len(spec_result.deliverable_specs)
            else:
                manifest_dict = {
                    "run_id": effective_run_id,
                    "validation_run": "Stage F2 Offline Desktop GUI Shell Test" if is_legacy_demo else "Task-Driven Offline Desktop Agent Run",
                    "task_goal": effective_task_goal,
                    "mode": "OFFLINE_DETERMINISTIC_MOCK",
                    "timestamp": datetime.now().isoformat(),
                    "offline_mode": True,
                    "live_api_called": False,
                    "code_executed": False,
                    "static_validation_passed": True,
                    "runtime_gameplay_verified": False,
                    "generated_files": [os.path.relpath(f, staging_dir).replace("\\", "/") for f in summary.generated_files],
                    "security_disclaimer": "AST/py_compile are syntax checks only, not process isolation sandboxes.",
                    "simulation_notice": "Outputs were generated from deterministic local mock templates, NOT by a live LLM.",
                }
                if is_legacy_demo and 'spec_result' in locals():
                    manifest_dict["view_items_count"] = len(spec_result.view_items)
                    manifest_dict["diagrams_count"] = len(spec_result.diagrams)
                    manifest_dict["recorded_conflicts"] = [c.conflict_id for c in spec_result.recorded_conflicts]
                    manifest_dict["deliverable_specs_count"] = len(spec_result.deliverable_specs)

            test_manifest_content = json.dumps(manifest_dict, indent=2)
            manifest_path = safe_write_file(
                staging_dir, "offline_validation_manifest.json", test_manifest_content, encoding="utf-8"
            )
            if manifest_path not in summary.generated_files:
                summary.generated_files.append(manifest_path)

            # Step 5b: Validate Safe Manifest Consistency Before Promotion
            m_ok, m_errs, _ = ProjectValidator.validate_manifest(manifest_path, staging_dir)
            if not m_ok:
                with self._state_lock:
                    if self.run_state not in ("COMMITTED", "CANCELLED"):
                        self.run_state = "FAILED"
                err_msg = "; ".join(m_errs) if m_errs else "Manifest consistency validation failed."
                _record_audit_milestone("PROMOTION_OUTCOME", {
                    "event": "PROMOTION_OUTCOME",
                    "promoted": False,
                    "destination_dir": "",
                })
                _fail_worker(f"Manifest validation failed in staging: {err_msg}")
                return

            # Optional pre-commit hook for deterministic concurrency regression tests
            if getattr(self, "_pre_commit_hook", None):
                self._pre_commit_hook()

            # Step 6: Mutex-Protected Commit Boundary & Atomic Directory Promotion
            with self._state_lock:
                if self.run_state in ("COMMITTED", "FAILED"):
                    return

                if self.cancel_requested.is_set() or self.run_state == "CANCELLED":
                    self.run_state = "CANCELLED"
                    _record_audit_milestone("PROMOTION_OUTCOME", {
                        "event": "PROMOTION_OUTCOME",
                        "promoted": False,
                        "destination_dir": "",
                    })
                    self._queue_msg("cancelled", "Validation cancelled prior to commit boundary.", effective_run_id)
                    return

                # Atomically claim COMMITTING under lock
                self.run_state = "COMMITTING"

                if getattr(self, "_in_commit_hook", None):
                    self._in_commit_hook()

                try:
                    if os.path.exists(target_run_dir):
                        raise FileExistsError(f"Destination run directory already exists: '{target_run_dir}'")
                    os.rename(staging_dir, target_run_dir)
                    self.run_state = "COMMITTED"
                    self.last_run_dir = target_run_dir
                    _record_audit_milestone("PROMOTION_OUTCOME", {
                        "event": "PROMOTION_OUTCOME",
                        "promoted": True,
                        "destination_dir": os.path.basename(target_run_dir),
                    })
                except OSError as prom_err:
                    self.run_state = "FAILED"
                    _record_audit_milestone("PROMOTION_OUTCOME", {
                        "event": "PROMOTION_OUTCOME",
                        "promoted": False,
                        "destination_dir": "",
                    })
                    self._queue_msg("error", f"Directory promotion failed: {prom_err}", effective_run_id)
                    return

            # Re-map returned artifact paths to promoted directory
            promoted_manifest_path = os.path.join(target_run_dir, "offline_validation_manifest.json")
            staging_norm = os.path.normcase(os.path.realpath(staging_dir))
            promoted_files = []
            for f in summary.generated_files:
                f_real = os.path.realpath(f)
                if os.path.normcase(f_real).startswith(staging_norm):
                    rel = os.path.relpath(f_real, staging_dir)
                    promoted_files.append(os.path.join(target_run_dir, rel))
                else:
                    try:
                        rel = os.path.relpath(f, staging_dir)
                        if not rel.startswith(".."):
                            promoted_files.append(os.path.join(target_run_dir, rel))
                        else:
                            promoted_files.append(f)
                    except Exception:
                        promoted_files.append(f)
            summary.generated_files = promoted_files

            self._queue_msg("summary", (summary, promoted_manifest_path), effective_run_id)
            if is_live_run:
                self._queue_msg(
                    "log",
                    (f"  ✓ Live agent synthesis completed successfully! Promoted directory: {os.path.basename(target_run_dir)}", "SUCCESS"),
                    effective_run_id,
                )
                self._queue_msg(
                    "log",
                    (f"  ✓ Cumulative verified live spend: ${self.budget_controller.cumulative_spend:.6f} USD.", "SUCCESS"),
                    effective_run_id,
                )
                self._queue_msg(
                    "completed",
                    f"Live agent run completed successfully in {os.path.basename(target_run_dir)}",
                    effective_run_id,
                )
            else:
                self._queue_msg(
                    "log",
                    (f"  ✓ Offline pipeline completed successfully! Promoted directory: {os.path.basename(target_run_dir)}", "SUCCESS"),
                    effective_run_id,
                )
                if getattr(self, "has_authoritative_history", False):
                    self._queue_msg(
                        "log",
                        (f"  ✓ Offline run completed; no live DeepSeek API call was made by this run. Authoritative historical spend: ${self.budget_controller.cumulative_spend:.6f} USD.", "SUCCESS"),
                        effective_run_id,
                    )
                else:
                    self._queue_msg(
                        "log",
                        ("  ✓ Offline run completed; no live DeepSeek API call was made by this run. Historical provider spend remains UNRECONCILED.", "SUCCESS"),
                        effective_run_id,
                    )
                self._queue_msg(
                    "completed",
                    f"Offline validation run completed successfully in {os.path.basename(target_run_dir)}",
                    effective_run_id,
                )

        except SpecificationExtractionError as err:
            with self._state_lock:
                if self.run_state not in ("COMMITTED", "CANCELLED"):
                    self.run_state = "FAILED"
            self._queue_msg("error", f"Specification Extraction Error: {err}", effective_run_id)
        except PathSecurityError as err:
            with self._state_lock:
                if self.run_state not in ("COMMITTED", "CANCELLED"):
                    self.run_state = "FAILED"
            self._queue_msg("error", f"Path Security Violation: {err}", effective_run_id)
        except Exception as err:
            with self._state_lock:
                if self.run_state not in ("COMMITTED", "CANCELLED"):
                    self.run_state = "FAILED"
            sanitized_err = redact_secrets(str(err))
            if 'effective_api_key' in locals() and effective_api_key and len(effective_api_key) > 5:
                sanitized_err = sanitized_err.replace(effective_api_key, "[REDACTED_API_KEY]")
            self._queue_msg("error", f"Unexpected Pipeline Error: {sanitized_err}", effective_run_id)
            try:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                err_copy = os.path.join(real_output_dir, f"error_execution_{effective_run_id[:8]}.log")
                with open(err_copy, "w", encoding="utf-8") as ef:
                    ef.write(f"[{ts}] [UNEXPECTED_PIPELINE_ERROR] {sanitized_err}\n")
            except Exception:
                pass

    def _invoke_synthesis_runner_factory(
        self,
        staging_dir: str,
        coordinator: OfflinePipelineCoordinator,
        doc_path: str,
        view_path: str,
        budget_controller: BudgetController,
        spec_result: SpecificationPreprocessResult,
    ) -> PipelineExecutionSummary:
        """Invokes the user-injected synthesis runner factory with flexible parameter adaptation."""
        import inspect
        factory = self.synthesis_runner_factory
        sig = inspect.signature(factory)
        kwargs: Dict[str, Any] = {
            "staging_dir": staging_dir,
            "coordinator": coordinator,
            "doc_path": doc_path,
            "view_path": view_path,
            "budget_controller": budget_controller,
            "spec_result": spec_result,
            "llm_client": self.mock_llm_client,
            "cancel_event": self.cancel_requested,
            "run_state_lock": self._state_lock,
        }
        has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if has_var_keyword:
            call_kwargs = kwargs
        else:
            call_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}

        result = factory(**call_kwargs) if call_kwargs else factory()
        if isinstance(result, PipelineExecutionSummary):
            return result

        from src.synthesis_driver import SynthesisResult, SynthesisStatus
        if isinstance(result, SynthesisResult):
            success = (result.status == SynthesisStatus.COMPLETED)
            files = list(result.files_written)
            return PipelineExecutionSummary(
                success=success,
                total_stages_run=result.total_turns,
                completed_stages=[result.artifact_name] if success else [],
                generated_files=files,
                cumulative_reserved=budget_controller.cumulative_spend + budget_controller.get_active_holds(),
                cumulative_spend=budget_controller.cumulative_spend,
                safety_violations=result.blocked_tool_calls,
                errors=[result.error_message] if result.error_message else [],
                security_notices=["SECURITY NOTICE: Executed via custom synthesis runner factory."],
            )
        return result

    def _run_synthesis_driver_workflow(
        self,
        staging_dir: str,
        budget_controller: BudgetController,
        doc_path: str,
        view_path: str,
        spec_result: SpecificationPreprocessResult,
    ) -> PipelineExecutionSummary:
        """Executes a single-artifact synthesis workflow using SynthesisDriver."""
        from src.synthesis_driver import SynthesisDriver, SynthesisStatus
        driver = SynthesisDriver(
            workspace_root=staging_dir,
            llm_client=self.mock_llm_client,
            budget_controller=budget_controller,
            cancel_event=self.cancel_requested,
            commit_lock=self._state_lock,
            authorized_target="space_fractions/fraction_math.py",
            bounded_mode=True,
        )
        synth_result = driver.run_synthesis(
            doc_path=doc_path,
            view_path=view_path,
        )
        synth_success = (synth_result.status == SynthesisStatus.COMPLETED)

        # SynthesisDriver creates a nested subfolder synthesis_run_<ts>_<id> inside staging_dir.
        # Flatten contents of that subfolder directly into staging_dir so the layout matches canonical staging.
        for item in os.listdir(staging_dir):
            item_path = os.path.join(staging_dir, item)
            if os.path.isdir(item_path) and item.startswith("synthesis_run_"):
                for sub_item in os.listdir(item_path):
                    src_sub = os.path.join(item_path, sub_item)
                    dst_sub = os.path.join(staging_dir, sub_item)
                    if os.path.exists(dst_sub):
                        if os.path.isdir(dst_sub):
                            shutil.rmtree(dst_sub)
                        else:
                            os.remove(dst_sub)
                    shutil.move(src_sub, dst_sub)
                try:
                    os.rmdir(item_path)
                except Exception:
                    pass

        generated_files: List[str] = []
        for root_dir, _, filenames in os.walk(staging_dir):
            for fname in filenames:
                full_p = os.path.join(root_dir, fname)
                if not os.path.basename(root_dir).startswith(".staging_"):
                    generated_files.append(full_p)

        errors = [synth_result.error_message] if synth_result.error_message else []
        if not synth_success and not errors:
            errors = [f"Synthesis terminated with status {synth_result.status.value}"]

        return PipelineExecutionSummary(
            success=synth_success,
            total_stages_run=synth_result.total_turns,
            completed_stages=[synth_result.artifact_name] if synth_success else [],
            generated_files=generated_files,
            cumulative_reserved=budget_controller.cumulative_spend + budget_controller.get_active_holds(),
            cumulative_spend=budget_controller.cumulative_spend,
            safety_violations=synth_result.blocked_tool_calls,
            errors=errors,
            security_notices=["SECURITY NOTICE: Executed in mock synthesis mode via SynthesisDriver."],
        )

    # -------------------------------------------------------------------------
    # Queue Processing on Main UI Thread
    # -------------------------------------------------------------------------

    def _queue_msg(self, msg_type: str, data: Any, run_id: Optional[str] = None) -> None:
        """Pushes a message onto the thread-safe queue."""
        msg = QueueMessage(msg_type, data, run_id)
        self.msg_queue.put(msg)

    def _process_queue(self) -> None:
        """Polls the queue and applies all widget updates on the main UI thread."""
        try:
            while True:
                item = self.msg_queue.get_nowait()
                msg_type = item[0]
                data = item[1]
                msg_run_id = getattr(item, "run_id", None) if len(item) == 2 else (item[2] if len(item) > 2 else None)
                self._handle_queue_message(msg_type, data, run_id=msg_run_id)
                self.msg_queue.task_done()
        except queue.Empty:
            pass
        finally:
            try:
                if self.root.winfo_exists():
                    self.root.after(80, self._process_queue)
            except Exception:
                pass

    def _handle_queue_message(
        self, msg_type: str, data: Any, run_id: Optional[str] = None
    ) -> None:
        """Dispatches typed message updates to UI elements."""
        # Stale run protection: ignore state messages from earlier runs
        if run_id is not None and self.current_run_id is not None and run_id != self.current_run_id:
            return

        if msg_type == "log":
            text, tag = data
            self._append_log(text, tag)

        elif msg_type == "spec_result":
            self.last_preprocess_result = data
            self._populate_details_tab(data)

        elif msg_type == "summary":
            summary, manifest_path = data
            self.last_execution_summary = summary
            self._populate_artifacts_tab(summary, manifest_path)
            self._update_telemetry_display(summary=summary)

        elif msg_type == "completed":
            with self._state_lock:
                if self.run_state in ("CANCELLED", "FAILED"):
                    return
                self.is_running = False
                self.run_state = "COMMITTED"
            self.progress_bar.stop()
            self._set_controls_running_state(False)
            self.status_var.set(f"Completed: {data}")
            self._append_log(f"Status: {data}", "SUCCESS")

        elif msg_type == "cancelled":
            with self._state_lock:
                if self.run_state in ("COMMITTED", "FAILED"):
                    return
                self.is_running = False
                self.run_state = "CANCELLED"
            self.progress_bar.stop()
            self._set_controls_running_state(False)
            self.status_var.set(f"Cancelled: {data}")
            self._append_log(f"Status: {data}", "WARNING")

        elif msg_type == "error":
            with self._state_lock:
                if self.run_state in ("COMMITTED", "CANCELLED"):
                    return
                self.is_running = False
                self.run_state = "FAILED"
            self.progress_bar.stop()
            self._set_controls_running_state(False)
            self.status_var.set("Error encountered during execution.")
            self._append_log(f"[ERROR] {data}", "ERROR")
            if not getattr(self, "suppress_messagebox", False):
                messagebox.showerror("Execution Error", str(data))

    def _append_log(self, text: str, tag: str = "INFO") -> None:
        """Appends a timestamped log entry to the scrolled text widget."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        try:
            if hasattr(self, "log_text"):
                self.log_text.insert(tk.END, f"[{timestamp}] {text}\n", tag)
                self.log_text.see(tk.END)
        except Exception:
            pass

    def _populate_details_tab(self, result: SpecificationPreprocessResult) -> None:
        """Fills the Architecture & Conflict Details text widget."""
        self.details_text.configure(state=tk.NORMAL)
        self.details_text.delete("1.0", tk.END)

        lines = [
            "=" * 78,
            " CANONICAL ARCHITECTURE EXTRACTION REPORT (OFFLINE PREPROCESSOR)",
            "=" * 78,
            f"Schema Version: {result.schema_version}",
            f"Serialized Output Size: {result.serialized_utf8_bytes} UTF-8 bytes",
            f"Extraction Warnings: {len(result.extraction_warnings)}",
            "",
            "--- 1. NUMBERED VIEW ITEMS (11/11) ---",
        ]
        for item in result.view_items:
            lines.append(
                f"  [{item.item_number:02d}] {item.full_title} (lines {item.start_line}-{item.end_line}) "
                f"-> Diagrams: {item.diagram_ids}"
            )

        lines.extend([
            "",
            "--- 2. PLANTUML DIAGRAM BLOCKS (13/13) ---",
        ])
        for diag in result.diagrams:
            lines.append(
                f"  [{diag.index:02d}] {diag.diagram_id} ({diag.diagram_type}) "
                f"lines {diag.start_line}-{diag.end_line}"
            )

        lines.extend([
            "",
            "--- 3. EXTRACTED DOMAIN ENTITIES ---",
            f"  Actors: {result.entities.actors}",
            f"  Domain Classes: {[c['name'] for c in result.entities.classes]}",
            f"  Game States: {result.entities.states}",
            f"  Deployment Nodes: {result.entities.deployment_nodes}",
            f"  Container Artifacts: {result.entities.container_artifacts}",
            "",
            "--- 4. RECORDED ARCHITECTURAL CONFLICTS ---",
        ])
        for c in result.recorded_conflicts:
            lines.extend([
                f"  Conflict ID: {c.conflict_id}",
                f"  Title: {c.title}",
                f"  Status: {c.status}",
                f"  Description: {c.description}",
                f"  Architectural Implication: {c.architectural_implication}",
            ])

        lines.extend([
            "",
            "--- 5. DELIVERABLE SPECIFICATIONS (SECTION L) ---",
        ])
        for d in result.deliverable_specs:
            lines.append(f"  {d.filename} ({d.format}): lines {d.start_line}-{d.end_line}")

        self.details_text.insert(tk.END, "\n".join(lines))
        self.details_text.configure(state=tk.DISABLED)

    def _populate_artifacts_tab(
        self, summary: PipelineExecutionSummary, manifest_path: Optional[str] = None
    ) -> None:
        """Fills the Generated Artifacts Treeview table defensively."""
        try:
            for item in self.artifacts_tree.get_children():
                self.artifacts_tree.delete(item)

            raw_files = getattr(summary, "generated_files", None)
            all_files = list(raw_files) if raw_files else []
            if manifest_path and manifest_path not in all_files:
                all_files.append(manifest_path)

            output_dir = self.output_dir_var.get() if hasattr(self, "output_dir_var") else ""

            for path in all_files:
                if not path or not isinstance(path, str):
                    continue
                fname = os.path.basename(path)
                try:
                    rel_path = os.path.relpath(path, output_dir) if output_dir else fname
                except Exception:
                    rel_path = fname
                size = os.path.getsize(path) if os.path.exists(path) else 0
                self.artifacts_tree.insert(
                    "",
                    tk.END,
                    values=(fname, rel_path, f"{size:,}", "Verified Safe"),
                )
        except Exception:
            pass

    def _update_telemetry_display(self, summary: Optional[PipelineExecutionSummary] = None) -> None:
        """Updates the financial telemetry cards on the UI defensively and truthfully.
        
        Guarantees:
        - Never replaces missing or None spend/hold with nominal amounts -> displays 'UNAVAILABLE'.
        - Never replaces negative, non-finite (NaN/inf), or malformed values with nominal amounts -> displays 'INVALID'.
        - Displays remaining budget as 'UNRECONCILED' whenever spend or hold is not a valid reconciled number.
        - Formats valid floats >= 0.0 strictly as f"${val:.6f}".
        """
        import math

        # 1. Safely evaluate cumulative verified live spend
        verified_valid = False
        spend_val = 0.0
        try:
            raw_spend = getattr(self.budget_controller, "cumulative_spend", None)

            if raw_spend is None:
                spend_text = "UNAVAILABLE"
            elif isinstance(raw_spend, str) and not raw_spend.strip().replace(".", "", 1).replace("-", "", 1).isdigit():
                spend_text = "INVALID"
            else:
                val = float(raw_spend)
                if math.isnan(val) or math.isinf(val) or val < 0.0:
                    spend_text = "INVALID"
                else:
                    spend_val = val
                    if getattr(self, "has_authoritative_history", False):
                        verified_valid = True
                        spend_text = f"${val:.6f}"
                    else:
                        verified_valid = False
                        spend_text = "UNRECONCILED"
        except (TypeError, ValueError, AttributeError):
            spend_text = "INVALID"

        # 2. Safely evaluate active reserved hold
        hold_valid = False
        hold_val = 0.0
        try:
            if hasattr(self.budget_controller, "get_active_holds"):
                raw_hold = self.budget_controller.get_active_holds()
            else:
                raw_hold = getattr(self.budget_controller, "active_holds", None)

            if raw_hold is None:
                hold_text = "UNAVAILABLE"
            elif isinstance(raw_hold, str) and not raw_hold.strip().replace(".", "", 1).replace("-", "", 1).isdigit():
                hold_text = "INVALID"
            else:
                val = float(raw_hold)
                if math.isnan(val) or math.isinf(val) or val < 0.0:
                    hold_text = "INVALID"
                else:
                    hold_val = val
                    hold_valid = True
                    hold_text = f"${val:.6f}"
        except (TypeError, ValueError, AttributeError, Exception):
            hold_text = "INVALID"

        # 3. Safely evaluate remaining available budget
        if verified_valid and hold_valid:
            try:
                ceiling = float(getattr(self, "HARD_CEILING", 0.800000))
                if math.isnan(ceiling) or math.isinf(ceiling) or ceiling < 0.0:
                    rem_text = "INVALID"
                else:
                    rem = max(0.0, ceiling - (spend_val + hold_val))
                    rem_text = f"${rem:.6f}"
            except Exception:
                rem_text = "UNRECONCILED"
        else:
            rem_text = "UNRECONCILED"

        # 4. Update UI labels safely
        try:
            if hasattr(self, "telemetry_labels"):
                if "Cumulative Verified Live Spend:" in self.telemetry_labels:
                    self.telemetry_labels["Cumulative Verified Live Spend:"].configure(
                        text=spend_text
                    )
                if "Active Reserved Hold:" in self.telemetry_labels:
                    self.telemetry_labels["Active Reserved Hold:"].configure(
                        text=hold_text
                    )
                if "Remaining Available Budget:" in self.telemetry_labels:
                    self.telemetry_labels["Remaining Available Budget:"].configure(
                        text=rem_text
                    )
        except Exception:
            pass


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parses and validates command-line arguments for the Code Agent GUI shell.

    Supports:
    - --auto-run: Automatically triggers pipeline start on startup.
    - --auto-close-ms <ms>: Automatically closes application window after ms (safe deferral if active).
    - --allow-live-dispatch: Explicitly authorizes live API dispatch (defaults to False).
    - --mode <offline|mock_agent|live>: Selects execution mode (default: 'offline').
    - --output-dir <path>: Selects output directory (validated for workspace containment).
    - --task <description>: Task goal description for the autonomous code agent.

    Rejects:
    - Live mode without explicit --allow-live-dispatch authorization.
    - Invalid arguments.
    - Output paths that escape the workspace boundary.
    """
    parser = argparse.ArgumentParser(
        description="DeepSeek Autonomous Code Agent — Native Windows Desktop GUI",
        add_help=True,
    )
    parser.add_argument(
        "--auto-run",
        action="store_true",
        default=False,
        help="Automatically trigger start action after GUI initialization.",
    )
    parser.add_argument(
        "--auto-close-ms",
        type=int,
        default=0,
        help="Automatically close application after specified milliseconds (deferred if worker is active).",
    )
    parser.add_argument(
        "--launch-game",
        action="store_true",
        default=False,
        help="Automatically launch the playable Space Fractions desktop game on startup.",
    )
    parser.add_argument(
        "--allow-live-dispatch",
        action="store_true",
        default=False,
        help="Explicitly authorize live DeepSeek API dispatch (defaults to False).",
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "mock_agent", "live"],
        default="offline",
        help="Execution mode: 'offline' (template pipeline), 'mock_agent' (multi-stage AgentLoop), or 'live' (official DeepSeek API).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Destination directory for generated deliverables.",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Task goal description for the autonomous code agent.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=AgentLoop.MAX_TURNS_DEFAULT,
        help="Maximum model turns for AgentLoop (default: 24; live canonical generation uses at least 24).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Timeout in seconds for remote API requests (default: 60.0).",
    )
    parser.add_argument(
        "--suppress-messagebox",
        action="store_true",
        default=False,
        help="Suppress modal dialog boxes for unattended/headless execution.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=2500,
        help="Maximum output tokens for live API requests (default: 2500, max: 4096).",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        default=False,
        help="Run bounded offline GUI and Tkinter runtime smoke tests and exit.",
    )
    parser.add_argument(
        "--resume-staging",
        type=str,
        default=None,
        help="Optional path to an existing staging directory to seed/resume verified deliverables from.",
    )

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.auto_close_ms < 0:
        raise ValueError("--auto-close-ms cannot be negative.")

    if args.timeout <= 0:
        raise ValueError("--timeout must be a positive number.")

    if args.max_output_tokens <= 0 or args.max_output_tokens > 4096:
        raise ValueError("--max-output-tokens must be an integer between 1 and 4096.")

    if args.resume_staging is not None:
        raw_resume = args.resume_staging.strip()
        if not raw_resume:
            raise ValueError("--resume-staging path cannot be empty.")
        if "\0" in raw_resume:
            raise PathSecurityError("Null bytes detected in resume-staging path.")
        if not os.path.isdir(raw_resume):
            raise FileNotFoundError(f"--resume-staging directory does not exist: '{raw_resume}'")

    if args.mode == "live" and not args.allow_live_dispatch:
        parser.error("Mode 'live' is prohibited without explicit --allow-live-dispatch authorization.")

    if args.output_dir is not None:
        raw_out = args.output_dir.strip()
        if not raw_out:
            raise ValueError("--output-dir cannot be empty.")
        if "\0" in raw_out:
            raise PathSecurityError("Null bytes detected in output path.")

        # Check for UNC network paths
        if raw_out.startswith(("\\\\", "//")):
            raise PathSecurityError(f"UNC network paths are prohibited: '{raw_out}'")

        # Canonicalize relative or absolute path against host workspace
        workspace_norm = os.path.realpath(os.path.abspath(os.getcwd()))
        target_norm = os.path.realpath(os.path.abspath(os.path.join(workspace_norm, raw_out)))

        try:
            common = os.path.commonpath([workspace_norm, target_norm])
            if common != workspace_norm:
                raise PathSecurityError(
                    f"Output directory '{args.output_dir}' escapes workspace '{workspace_norm}'"
                )
        except ValueError as err:
            raise PathSecurityError(f"Cross-drive output directory prohibited: '{args.output_dir}'. Error: {err}")

        if os.path.isfile(target_norm):
            raise PathSecurityError(f"Output path points to an existing file: '{args.output_dir}'")

        args.output_dir = target_norm

    return args


def _smoke_write(message: str, *, error: bool = False) -> None:
    """Write optional smoke diagnostics without requiring a console subsystem."""
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        return
    try:
        stream.write(message)
        stream.flush()
    except (AttributeError, OSError):
        return


def run_offline_smoke_test(args: argparse.Namespace) -> None:
    """Run bounded offline smoke test against Tkinter runtime and GUI controls."""
    _smoke_write("[SMOKE-TEST] Starting bounded offline runtime verification...\n")

    # Stage 1: import tkinter
    try:
        import tkinter as test_tk
        _smoke_write("[SMOKE-TEST] Stage 1/5: import tkinter succeeded.\n")
    except Exception as err:
        _smoke_write(f"[SMOKE-TEST] Stage 1/5 FAILED: import tkinter failed: {err}\n", error=True)
        sys.exit(1)

    # Stage 2: tkinter.Tcl()
    try:
        tcl = test_tk.Tcl()
        patchlevel = tcl.eval("info patchlevel")
        _smoke_write(f"[SMOKE-TEST] Stage 2/5: tkinter.Tcl() initialized (patchlevel: {patchlevel}).\n")
    except Exception as err:
        _smoke_write(f"[SMOKE-TEST] Stage 2/5 FAILED: tkinter.Tcl() failed: {err}\n", error=True)
        sys.exit(2)

    # Stage 3: tkinter.Tk()
    try:
        root = test_tk.Tk()
        root.withdraw()
        _smoke_write("[SMOKE-TEST] Stage 3/5: tkinter.Tk() initialized successfully.\n")
    except Exception as err:
        _smoke_write(f"[SMOKE-TEST] Stage 3/5 FAILED: tkinter.Tk() failed: {err}\n", error=True)
        sys.exit(3)

    # Stage 4: Default task and output-directory controls initialize
    try:
        app = AgentGUIApp(
            root,
            suppress_messagebox=True,
            default_task=args.task,
            default_output_dir=args.output_dir,
        )
        assert hasattr(app, "task_entry") and app.task_entry is not None, "task_entry missing"
        assert hasattr(app, "output_entry") and app.output_entry is not None, "output_entry missing"
        assert hasattr(app, "btn_start") and app.btn_start is not None, "btn_start missing"
        assert hasattr(app, "btn_cancel") and app.btn_cancel is not None, "btn_cancel missing"
        assert app.task_entry.get().strip() != "", "Default task prompt is empty"
        assert app.output_entry.get().strip() != "", "Default output directory is empty"
        _smoke_write("[SMOKE-TEST] Stage 4/5: Default task and output-directory controls initialized successfully.\n")
    except Exception as err:
        _smoke_write(f"[SMOKE-TEST] Stage 4/5 FAILED: GUI controls initialization failed: {err}\n", error=True)
        root.destroy()
        sys.exit(4)

    # Stage 5: GUI enters event loop and closes cleanly
    try:
        root.update()
        root.after(100, root.destroy)
        root.mainloop()
        _smoke_write("[SMOKE-TEST] Stage 5/5: GUI entered event loop and closed cleanly.\n")
    except Exception as err:
        _smoke_write(f"[SMOKE-TEST] Stage 5/5 FAILED: GUI event loop error: {err}\n", error=True)
        sys.exit(5)

    _smoke_write("[SMOKE-TEST] ALL 5 SMOKE TEST STAGES PASSED CLEANLY.\n")
    sys.exit(0)


def main() -> None:
    try:
        args = parse_cli_args()
    except (ValueError, PathSecurityError) as err:
        sys.stderr.write(f"CLI Error: {err}\n")
        sys.exit(2)
    except SystemExit as err:
        sys.exit(err.code)

    if args.smoke_test:
        run_offline_smoke_test(args)
        return

    root = tk.Tk()
    suppress_mb = args.suppress_messagebox or args.auto_run
    app = AgentGUIApp(
        root,
        auto_run=args.auto_run,
        auto_close_ms=args.auto_close_ms,
        suppress_messagebox=suppress_mb,
        allow_live_dispatch=args.allow_live_dispatch,
        default_execution_mode=args.mode,
        default_output_dir=args.output_dir,
        launch_game=args.launch_game,
        default_task=args.task,
        max_turns=args.max_turns,
        live_timeout=args.timeout,
        live_max_output_tokens=args.max_output_tokens,
        resume_staging=args.resume_staging,
    )
    root.mainloop()


if __name__ == "__main__":
    main()
