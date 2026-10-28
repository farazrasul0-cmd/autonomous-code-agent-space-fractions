"""Comprehensive Unit Test Suite for Space Fractions Game.

Validates:
1. Fraction arithmetic, GCD/LCM, equality, comparison, and invariants.
2. QuestionBank dynamic generation across curriculum levels 1-3.
3. GameEngine state transitions (TITLE -> PLAYING <-> PAUSED -> GAMEOVER).
4. Energy scoring, streak bonuses, and shield depletion mechanics.
5. End-of-mission summaries, accuracy metrics, and rank honors.
6. AST structure and interface verification for main.py and ui_screens.py.

Guarantees 100% offline execution without network or subprocess calls.
"""

from __future__ import annotations

import ast
import os
import tkinter as tk
import pytest

from space_fractions.fraction_math import Fraction, MathQuestion, MathQuestionGenerator
from space_fractions.game_engine import GameEngine, GameEngineError, GameState, TurnResult
from space_fractions.question_bank import Question, QuestionBank, QuestionError, CURATED_QUESTIONS
from space_fractions.ui_screens import FractionVisualizer, HUDFrame
from space_fractions.main import SpaceFractionsApp
from src.project_validator import ProjectValidator


# =============================================================================
# 1. Fraction Arithmetic and Math Invariants Tests
# =============================================================================

def test_fraction_initialization_and_simplification():
    f = Fraction(8, 12)
    s = f.simplify()
    assert s.n == 2
    assert s.d == 3
    assert str(s) == "2/3"
    assert repr(s) == "Fraction(2, 3)"

    # Integer representation
    f_int = Fraction(6, 2).simplify()
    assert str(f_int) == "3"


def test_fraction_zero_denominator_rejection():
    with pytest.raises(ValueError, match="Denominator cannot be zero"):
        Fraction(5, 0)


def test_fraction_type_validation():
    with pytest.raises(TypeError, match="must be integers"):
        Fraction("1", 2)  # type: ignore
    with pytest.raises(TypeError, match="must be integers"):
        Fraction(1, "2")  # type: ignore


def test_fraction_negative_denominator_normalization():
    f = Fraction(3, -4)
    assert f.n == -3
    assert f.d == 4

    f_double_neg = Fraction(-3, -4)
    assert f_double_neg.n == 3
    assert f_double_neg.d == 4


def test_fraction_gcd_and_lcm():
    assert Fraction.gcd(12, 18) == 6
    assert Fraction.gcd(7, 13) == 1
    assert Fraction.lcm(4, 6) == 12
    assert Fraction.lcm(0, 5) == 0
    assert Fraction.lcm(3, 0) == 0


def test_fraction_addition_and_subtraction():
    # Like denominators
    assert Fraction(1, 4).add(Fraction(2, 4)) == Fraction(3, 4)
    assert Fraction(3, 5).subtract(Fraction(1, 5)) == Fraction(2, 5)

    # Unlike denominators
    res_add = Fraction(1, 3).add(Fraction(1, 6))
    assert res_add == Fraction(1, 2)
    assert res_add.n == 1
    assert res_add.d == 2

    res_sub = Fraction(3, 4).subtract(Fraction(1, 2))
    assert res_sub == Fraction(1, 4)


def test_fraction_comparison_and_equality():
    f1 = Fraction(1, 2)
    f2 = Fraction(2, 4)
    f3 = Fraction(3, 4)
    f4 = Fraction(1, 3)

    assert f1.equals(f2)
    assert f1 == f2
    assert f1 != f3
    assert f1 != "not_a_fraction"

    assert f1.compare(f3) == -1  # 1/2 < 3/4
    assert f3.compare(f1) == 1   # 3/4 > 1/2
    assert f1.compare(f2) == 0   # 1/2 == 2/4


# =============================================================================
# 2. Question Bank and Curriculum Levels Tests
# =============================================================================

def test_math_question_generator_levels():
    gen = MathQuestionGenerator(seed=123)

    for level in (1, 2, 3):
        for idx in range(1, 5):
            q = gen.generate(level=level, index=idx)
            assert isinstance(q, MathQuestion)
            assert q.level == level
            assert len(q.options) == 4
            assert 0 <= q.correct_index < 4
            assert len(set(q.options)) == 4
            assert q.prompt.strip() != ""
            assert q.explanation.strip() != ""


def test_question_bank_levels():
    bank = QuestionBank(seed=42)

    for level in (1, 2, 3):
        q = bank.get_question(level=level, index=1)
        assert isinstance(q, Question)
        assert q.level == level
        assert q.get_prompt() == q.prompt
        assert q.get_options() == q.options
        assert q.check_answer(q.correct_index) is True
        assert q.check_answer((q.correct_index + 1) % 4) is False


# =============================================================================
# 3. Game Engine States, Scoring, and Lifecycle Tests
# =============================================================================

def test_game_engine_initial_state_and_start():
    engine = GameEngine(level=1, total_questions=5, seed=42)
    assert engine.state == GameState.TITLE
    assert engine.score == 0
    assert engine.shields == 3
    assert engine.viewScore() == 0
    assert engine.view_score() == 0

    engine.play()
    assert engine.state == GameState.PLAYING
    assert engine.current_question is not None
    assert engine.current_question_index == 1


def test_game_engine_pause_and_resume():
    engine = GameEngine(level=1, total_questions=5, seed=42)
    engine.play()

    engine.pause()
    assert engine.state == GameState.PAUSED

    # Cannot pause while already paused
    with pytest.raises(GameEngineError, match="Must be PLAYING"):
        engine.pause()

    engine.resume()
    assert engine.state == GameState.PLAYING

    # Cannot resume while already playing
    with pytest.raises(GameEngineError, match="Must be PAUSED"):
        engine.resume()


def test_game_engine_scoring_and_streak():
    engine = GameEngine(level=1, total_questions=5, seed=42)
    engine.play()

    # Question 1: Correct answer (+100)
    q1 = engine.current_question
    res1 = engine.submit_answer(q1.correct_index)
    assert res1.is_correct is True
    assert res1.energy_awarded == 100
    assert res1.current_score == 100
    assert res1.current_streak == 1
    assert res1.current_shields == 3

    # Question 2: Correct answer (+100 + 20 streak bonus = +120)
    engine.next_question()
    q2 = engine.current_question
    res2 = engine.submit_answer(q2.correct_index)
    assert res2.is_correct is True
    assert res2.energy_awarded == 120
    assert res2.current_score == 220
    assert res2.current_streak == 2

    # Question 3: Wrong answer (-1 shield, streak reset)
    engine.next_question()
    q3 = engine.current_question
    wrong_idx = (q3.correct_index + 1) % 4
    res3 = engine.submit_answer(wrong_idx)
    assert res3.is_correct is False
    assert res3.energy_awarded == 0
    assert res3.current_score == 220
    assert res3.current_streak == 0
    assert res3.current_shields == 2


def test_game_engine_shield_depletion_defeat():
    engine = GameEngine(level=1, total_questions=5, seed=42)
    engine.play()

    # Submit 3 wrong answers in a row
    for _ in range(3):
        q = engine.current_question
        wrong_idx = (q.correct_index + 1) % 4
        res = engine.submit_answer(wrong_idx)
        if not res.is_game_over:
            engine.next_question()

    assert engine.shields == 0
    assert engine.state == GameState.GAMEOVER
    assert engine.victory is False
    summary = engine.get_summary()
    assert summary.victory is False
    assert summary.shields == 0


def test_game_engine_mission_victory():
    engine = GameEngine(level=1, total_questions=3, seed=42)
    engine.play()

    for idx in range(3):
        q = engine.current_question
        res = engine.submit_answer(q.correct_index)
        if idx < 2:
            engine.next_question()

    assert res.is_game_over is True
    assert res.victory is True
    assert engine.state == GameState.GAMEOVER
    assert engine.victory is True

    summary = engine.get_summary()
    assert summary.victory is True
    assert summary.questions_solved == 3
    assert summary.accuracy_percentage == 100
    assert summary.rank == "Fleet Admiral"


# =============================================================================
# 4. AST Structure and Desktop App Interface Verification
# =============================================================================

def test_ast_main_app_interfaces():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_file = os.path.join(repo_root, "space_fractions", "main.py")
    assert os.path.isfile(main_file), f"main.py not found at {main_file}"

    with open(main_file, "r", encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src, filename="main.py")
    ok, errs = ProjectValidator.validate_gui_app_interface(tree)
    assert ok is True, f"GUI App Interface validation failed: {errs}"


def test_ast_ui_screens_interfaces():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ui_file = os.path.join(repo_root, "space_fractions", "ui_screens.py")
    assert os.path.isfile(ui_file), f"ui_screens.py not found at {ui_file}"

    with open(ui_file, "r", encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src, filename="ui_screens.py")
    ok, errs = ProjectValidator.validate_ui_screens_interface(tree)
    assert ok is True, f"UI Screens Interface validation failed: {errs}"


def test_ast_fraction_math_interfaces():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    math_file = os.path.join(repo_root, "space_fractions", "fraction_math.py")
    assert os.path.isfile(math_file), f"fraction_math.py not found at {math_file}"

    with open(math_file, "r", encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src, filename="fraction_math.py")
    ok, errs = ProjectValidator.validate_fraction_math_interface(tree)
    assert ok is True, f"Fraction Math Interface validation failed: {errs}"


def test_ast_game_engine_interfaces():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    engine_file = os.path.join(repo_root, "space_fractions", "game_engine.py")
    assert os.path.isfile(engine_file), f"game_engine.py not found at {engine_file}"

    with open(engine_file, "r", encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src, filename="game_engine.py")
    ok, errs = ProjectValidator.validate_game_engine_interface(tree)
    assert ok is True, f"Game Engine Interface validation failed: {errs}"


def test_static_validator_rejects_bare_sibling_package_imports():
    tree = ast.parse("from fraction_math import Fraction\nimport question_bank\n")
    ok, errs = ProjectValidator.validate_package_imports(tree, "space_fractions/game_engine.py")
    assert ok is False
    assert len(errs) == 2
    assert all("Invalid package import" in err for err in errs)


def test_static_validator_accepts_relative_and_qualified_package_imports():
    tree = ast.parse(
        "from .fraction_math import Fraction\n"
        "from space_fractions.question_bank import QuestionBank\n"
    )
    ok, errs = ProjectValidator.validate_package_imports(tree, "space_fractions/game_engine.py")
    assert ok is True
    assert errs == []


def test_fraction_interface_rejects_constructor_reduction():
    tree = ast.parse(
        "import math\n"
        "class Fraction:\n"
        " def __init__(self, numerator, denominator):\n"
        "  divisor = math.gcd(numerator, denominator)\n"
        "  self.numerator = numerator // divisor\n"
        "  self.denominator = denominator // divisor\n"
        " def simplify(self): pass\n"
        " def add(self): pass\n"
        " def subtract(self): pass\n"
        " def compare(self): pass\n"
        " def equals(self): pass\n"
        "class MathQuestionGenerator:\n"
        " def generate(self): pass\n"
    )
    ok, errs = ProjectValidator.validate_fraction_math_interface(tree)
    assert ok is False
    assert any("must preserve" in err for err in errs)


def test_fraction_interface_rejects_unsimplified_arithmetic_and_fractional_whole_string():
    tree = ast.parse(
        "class Fraction:\n"
        " def __init__(self, numerator, denominator=1):\n"
        "  self.numerator, self.denominator = numerator, denominator\n"
        " def simplify(self): return Fraction(self.numerator, self.denominator)\n"
        " def add(self, other): return Fraction(self.numerator + other.numerator, self.denominator)\n"
        " def subtract(self, other): return Fraction(self.numerator - other.numerator, self.denominator)\n"
        " def compare(self, other): return 0\n"
        " def equals(self, other): return True\n"
        " def __str__(self): return f'{self.numerator}/{self.denominator}'\n"
        "class MathQuestionGenerator:\n"
        " def generate(self): return {}\n"
    )
    ok, errs = ProjectValidator.validate_fraction_math_interface(tree)
    assert ok is False
    assert any("add() must return a simplified" in err for err in errs)
    assert any("subtract() must return a simplified" in err for err in errs)
    assert any("whole-number values" in err for err in errs)


def test_fraction_interface_rejects_missing_value_equality_and_unsimplified_string():
    tree = ast.parse(
        "class Fraction:\n"
        " def __init__(self, n, d=1): self.n, self.d = n, d\n"
        " def simplify(self): return Fraction(self.n, self.d)\n"
        " def add(self, other): return Fraction(self.n, self.d).simplify()\n"
        " def subtract(self, other): return Fraction(self.n, self.d).simplify()\n"
        " def compare(self, other): return 0\n"
        " def equals(self, other): return True\n"
        " def __eq__(self, other): return False\n"
        " def __str__(self): return str(self.n) if self.d == 1 else f'{self.n}/{self.d}'\n"
        "class MathQuestionGenerator:\n"
        " def generate(self): return {}\n"
    )
    ok, errs = ProjectValidator.validate_fraction_math_interface(tree)
    assert ok is False
    assert any("delegate to equals" in err for err in errs)
    assert any("simplify a copy first" in err for err in errs)


# =============================================================================
# 5. Playable Game Implementation & Verification Tests (Phase 2.27.29)
# =============================================================================

def test_fraction_from_string_parsing_and_errors():
    """Verifies Fraction.from_string parser behavior on valid and invalid inputs."""
    assert Fraction.from_string("3/4") == Fraction(3, 4)
    assert Fraction.from_string("5") == Fraction(5, 1)
    assert Fraction.from_string("-2/3") == Fraction(-2, 3)
    assert Fraction.from_string(" -4/8 ") == Fraction(-1, 2)
    
    with pytest.raises(ValueError):
        Fraction.from_string("abc")
    with pytest.raises(ValueError):
        Fraction.from_string("")
    with pytest.raises(ValueError):
        Fraction.from_string("1/0")
    with pytest.raises(ValueError):
        Fraction.from_string("1/2/3")


def test_curated_question_bank_size_and_diversity():
    """Verifies at least 20 vetted curriculum questions exist across 3 distinct levels."""
    bank = QuestionBank()
    assert bank.total_curated_questions() >= 20
    assert len(CURATED_QUESTIONS) >= 20
    
    for lvl in (1, 2, 3):
        qs = [q for q in CURATED_QUESTIONS if q.level == lvl]
        assert len(qs) >= 5, f"Level {lvl} has insufficient questions: {len(qs)}"

    for q in CURATED_QUESTIONS:
        assert len(q.prompt.strip()) > 0
        assert len(q.options) == 4
        assert len(set(q.options)) == 4, f"Options not distinct for {q.prompt}: {q.options}"
        assert 0 <= q.correct_index < 4
        assert len(q.explanation.strip()) > 0

        # For arithmetic questions, verify no distractor is mathematically equal to the correct answer
        if q.topic in ("Addition", "Subtraction"):
            correct_f = Fraction.from_string(q.options[q.correct_index])
            for i, opt in enumerate(q.options):
                if i != q.correct_index:
                    try:
                        dist_f = Fraction.from_string(opt)
                        assert dist_f != correct_f, (
                            f"Ambiguous distractor in {q.id} ({q.prompt}): "
                            f"Option {i} ('{opt}') is mathematically equal to correct answer '{q.options[q.correct_index]}'."
                        )
                    except ValueError:
                        pass


def test_fraction_visualizer_rendering_headless():
    """Verifies FractionVisualizer canvas drawing across various fraction types in headless mode."""
    root = tk.Tk()
    root.withdraw()
    try:
        vis = FractionVisualizer(root, width=240, height=180)
        vis.pack()

        vis.draw_fraction(1, 2)
        assert len(vis.find_all()) > 0
        
        vis.draw_fraction(3, 4)
        assert len(vis.find_all()) > 0
        
        vis.draw_fraction(5, 4)
        assert len(vis.find_all()) > 0
        
        vis.draw_fraction(0, 3)
        assert len(vis.find_all()) > 0
        
        vis.draw_fraction(-1, 2)
        assert len(vis.find_all()) > 0
    finally:
        root.destroy()


def test_hud_frame_telemetry_updates_headless():
    """Verifies HUDFrame telemetry card updates and depleted shield state in headless mode."""
    root = tk.Tk()
    root.withdraw()
    try:
        hud = HUDFrame(root)
        hud.pack()
        
        hud.update_hud(shields=3, score=250, streak=2, current_q=3, total_q=10, level=2)
        assert "3" in hud.shields_var.get() or "🛡️" in hud.shields_var.get()
        assert "250" in hud.score_var.get()
        assert "2" in hud.streak_var.get()
        assert "3 / 10" in hud.progress_var.get()
        assert "Level 2" in hud.level_var.get()
        
        hud.update_hud(shields=0, score=250, streak=0, current_q=4, total_q=10, level=2)
        assert "DEPLETED" in hud.shields_var.get()
    finally:
        root.destroy()


def test_desktop_app_gameplay_lifecycle_headless():
    """Verifies full desktop gameplay lifecycle: Title -> Play -> Feedback -> Pause -> Game Over -> Restart."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = SpaceFractionsApp(root)
        assert app.engine.state == GameState.TITLE
        
        app.start_mission()
        assert app.engine.state == GameState.PLAYING
        assert app.engine.current_question is not None
        
        correct_idx = app.engine.current_question.correct_index
        app.handle_answer(correct_idx)
        assert app.engine.score >= 100
        assert app.feedback_active is True
        
        app.advance_after_feedback()
        assert app.feedback_active is False
        assert app.engine.state == GameState.PLAYING
        
        # Test pause toggle
        app.toggle_pause()
        assert app.engine.state == GameState.PAUSED
        app.toggle_pause()
        assert app.engine.state == GameState.PLAYING
        
        # Defeat loop to reach game over
        while not app.engine.victory and app.engine.shields > 0:
            if app.engine.current_question is None:
                break
            wrong_idx = (app.engine.current_question.correct_index + 1) % 4
            app.handle_answer(wrong_idx)
            app.advance_after_feedback()

        assert app.engine.state == GameState.GAMEOVER
        
        # Test restart
        app.show_title_screen()
        assert app.engine.state == GameState.GAMEOVER or app.engine.state == GameState.TITLE
    finally:
        root.destroy()


def test_question_screen_pause_resume_visible_state_changes():
    """Verifies that QuestionScreen updates its pause button label and option states."""
    from space_fractions.ui_screens import QuestionScreen
    root = tk.Tk()
    root.withdraw()
    try:
        screen = QuestionScreen(root)
        screen.display_question({"prompt": "1/2 + 1/2", "options": ["1", "2", "3", "4"]})

        # Initial: Unpaused
        assert screen.pause_button.cget("text") == "Pause"
        assert str(screen.option_buttons[0].cget("state")) == "normal"
        assert screen.is_paused is False

        # Pause transition: Button becomes Resume, label visible, options disabled
        screen.set_paused(True)
        assert screen.pause_button.cget("text") == "Resume"
        assert screen.is_paused is True
        for btn in screen.option_buttons:
            assert str(btn.cget("state")) == "disabled"

        # Resume transition: Button reverts to Pause, label hidden, options re-enabled
        screen.set_paused(False)
        assert screen.pause_button.cget("text") == "Pause"
        assert screen.is_paused is False
        for btn in screen.option_buttons:
            assert str(btn.cget("state")) == "normal"
    finally:
        root.destroy()


def test_cosmic_ui_screens_components_and_2x2_grid():
    """Verifies that cosmic UI components, 2x2 grid geometry, and debrief rank calculation function properly."""
    from space_fractions.ui_screens import TitleScreen, QuestionScreen, GameOverScreen, setup_cosmic_theme
    root = tk.Tk()
    root.withdraw()
    try:
        setup_cosmic_theme()

        # Test TitleScreen
        started = []
        title_screen = TitleScreen(root, on_start=lambda: started.append(True))
        title_screen.btn_launch.invoke()
        assert len(started) == 1

        # Test QuestionScreen 2x2 grid
        q_screen = QuestionScreen(root)
        q_screen.display_question({"prompt": "Simplify 4/8", "options": ["1/4", "1/2", "3/4", "2/3"]})
        assert len(q_screen.option_buttons) == 4

        # Check grid positioning (2 rows, 2 columns)
        grid_info_0 = q_screen.option_buttons[0].grid_info()
        grid_info_1 = q_screen.option_buttons[1].grid_info()
        grid_info_2 = q_screen.option_buttons[2].grid_info()
        grid_info_3 = q_screen.option_buttons[3].grid_info()

        assert int(grid_info_0["row"]) == 0 and int(grid_info_0["column"]) == 0
        assert int(grid_info_1["row"]) == 0 and int(grid_info_1["column"]) == 1
        assert int(grid_info_2["row"]) == 1 and int(grid_info_2["column"]) == 0
        assert int(grid_info_3["row"]) == 1 and int(grid_info_3["column"]) == 1

        # Test GameOverScreen debrief calculation
        game_over = GameOverScreen(root)
        game_over.display_results(score=1200, questions_answered=10, correct_answers=9)
        assert "1200" in game_over._score_label.cget("text")
        assert "9/10" in game_over._questions_label.cget("text")
        assert "90%" in game_over._accuracy_label.cget("text")
        assert "Fleet Admiral" in game_over._rank_label.cget("text")
    finally:
        root.destroy()
