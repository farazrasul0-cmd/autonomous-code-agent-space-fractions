"""Unit and integration tests for Space Fractions Game Engine and Question Bank.

Covers:
1. Question entity validation, option distinctness, and ClassDiagram interfaces (getPrompt, getOptions).
2. QuestionBank dynamic generation across curriculum levels 1-3 and custom question management.
3. GameEngine state transitions (TITLE -> PLAYING <-> PAUSED, PLAYING -> GAMEOVER).
4. Scoring, streak multipliers, and shield depletion mechanics.
5. End-of-mission summaries, accuracy calculation, and rank assignment.
6. Seed-based determinism and reproducibility.
7. User entity ClassDiagram integration (playGame, viewScore).
"""

import pytest

from space_fractions.game_engine import GameEngine, GameEngineError, GameState, User
from space_fractions.question_bank import Question, QuestionBank, QuestionError


# =============================================================================
# Question Model Tests
# =============================================================================

def test_question_valid_initialization():
    q = Question(
        id="TEST-01",
        prompt="Simplify 2/4",
        options=["1/2", "2/3", "1/4", "3/4"],
        correct_index=0,
        explanation="Divide numerator and denominator by 2.",
        level=1,
        topic="Simplification",
    )
    assert q.id == "TEST-01"
    assert q.getPrompt() == "Simplify 2/4"
    assert q.get_prompt() == "Simplify 2/4"
    assert q.getOptions() == ["1/2", "2/3", "1/4", "3/4"]
    assert q.get_options() == ["1/2", "2/3", "1/4", "3/4"]
    assert q.correct_answer == "1/2"
    assert q.check_answer(0) is True
    assert q.check_answer(1) is False
    assert q.check_answer(-1) is False
    assert q.check_answer(99) is False


def test_question_validation_failures():
    # Empty prompt
    with pytest.raises(QuestionError, match="prompt must be a non-empty string"):
        Question(id="T1", prompt="", options=["A", "B", "C", "D"], correct_index=0)

    # Empty id
    with pytest.raises(QuestionError, match="id must be a non-empty string"):
        Question(id="", prompt="Valid", options=["A", "B", "C", "D"], correct_index=0)

    # Fewer than 4 options
    with pytest.raises(QuestionError, match="must contain exactly 4 choices"):
        Question(id="T2", prompt="P", options=["A", "B", "C"], correct_index=0)

    # Duplicate options
    with pytest.raises(QuestionError, match="must be 4 distinct choices"):
        Question(id="T3", prompt="P", options=["A", "A", "C", "D"], correct_index=0)

    # Invalid correct_index
    with pytest.raises(QuestionError, match="correct_index must be an integer in"):
        Question(id="T4", prompt="P", options=["A", "B", "C", "D"], correct_index=4)

    with pytest.raises(QuestionError, match="correct_index must be an integer in"):
        Question(id="T5", prompt="P", options=["A", "B", "C", "D"], correct_index=-1)

    # Invalid level
    with pytest.raises(QuestionError, match="level must be a positive integer"):
        Question(id="T6", prompt="P", options=["A", "B", "C", "D"], correct_index=0, level=0)


# =============================================================================
# Question Bank Tests
# =============================================================================

def test_question_bank_generation_levels():
    qb = QuestionBank(seed=123)

    for level in (1, 2, 3):
        for index in (1, 2, 3, 4):
            q = qb.get_question(level=level, index=index)
            assert isinstance(q, Question)
            assert q.level == level
            assert len(q.options) == 4
            assert len(set(q.options)) == 4
            assert 0 <= q.correct_index < 4
            assert q.options[q.correct_index] is not None
            assert len(q.prompt) > 0


def test_question_bank_invalid_inputs():
    qb = QuestionBank()

    with pytest.raises(QuestionError, match="Unsupported level"):
        qb.get_question(level=0, index=1)

    with pytest.raises(QuestionError, match="Unsupported level"):
        qb.get_question(level=4, index=1)

    with pytest.raises(QuestionError, match="index must be a positive integer"):
        qb.get_question(level=1, index=0)

    with pytest.raises(QuestionError, match="count must be a positive integer"):
        qb.get_questions_for_level(level=1, count=0)


def test_question_bank_custom_questions_and_admin_update():
    qb = QuestionBank()
    q_custom = Question(
        id="CUSTOM-L1-Q1",
        prompt="Special Admin Challenge: What is 1/2 + 1/2?",
        options=["1", "2/4", "1/4", "2"],
        correct_index=0,
        explanation="1/2 + 1/2 = 2/2 = 1.",
        level=1,
    )
    qb.add_question(q_custom)
    assert qb.count_custom_questions() == 1

    # Retrieval of custom question by ID convention
    fetched = qb.get_question(level=1, index=1)
    assert fetched.id == "CUSTOM-L1-Q1"
    assert fetched.prompt == "Special Admin Challenge: What is 1/2 + 1/2?"

    # Batch update matching Admin.updateQuestions
    q2 = Question(id="ADMIN-02", prompt="P2", options=["1", "2", "3", "4"], correct_index=0)
    qb.update_questions([q2])
    assert qb.count_custom_questions() == 2


# =============================================================================
# Game Engine State Machine Tests
# =============================================================================

def test_game_engine_initial_state_and_play():
    game = GameEngine(level=1, total_questions=5, seed=42)
    assert game.state == GameState.TITLE
    assert game.viewScore() == 0
    assert game.shields == 3
    assert game.streak == 0
    assert game.current_question is None

    game.play()
    assert game.state == GameState.PLAYING
    assert game.current_question_index == 1
    assert game.current_question is not None
    assert game.viewScore() == 0


def test_game_engine_state_transitions():
    game = GameEngine(level=1, seed=42)
    game.play()
    assert game.state == GameState.PLAYING

    # Playing -> Paused
    game.pause()
    assert game.state == GameState.PAUSED

    # Paused -> Playing
    game.resume()
    assert game.state == GameState.PLAYING

    # Cannot pause when not Playing
    game.pause()
    with pytest.raises(GameEngineError, match="Cannot pause when game state is PAUSED"):
        game.pause()

    # Resume back to Playing then trigger game over
    game.resume()
    game.gameOver(victory=True)
    assert game.state == GameState.GAMEOVER
    assert game.victory is True

    # Cannot pause or resume when GameOver
    with pytest.raises(GameEngineError, match="Cannot pause when game state is GAMEOVER"):
        game.pause()
    with pytest.raises(GameEngineError, match="Cannot resume when game state is GAMEOVER"):
        game.resume()

    # Playing again resets to PLAYING
    game.play()
    assert game.state == GameState.PLAYING


# =============================================================================
# Scoring, Streaks, and Shield Depletion Tests
# =============================================================================

def test_game_engine_scoring_and_streaks():
    game = GameEngine(level=1, total_questions=5, seed=999)
    game.play()

    # Question 1: Answer correctly -> +100
    res1 = game.submit_answer(game.current_question.correct_index)
    assert res1.is_correct is True
    assert res1.energy_awarded == 100
    assert res1.current_score == 100
    assert res1.current_streak == 1
    assert game.viewScore() == 100
    assert game.shields == 3

    game.next_question()

    # Question 2: Answer correctly -> +100 + (2-1)*20 = +120 -> Total 220
    res2 = game.submit_answer(game.current_question.correct_index)
    assert res2.is_correct is True
    assert res2.energy_awarded == 120
    assert res2.current_score == 220
    assert res2.current_streak == 2
    assert game.viewScore() == 220

    game.next_question()

    # Question 3: Answer correctly -> +100 + (3-1)*20 = +140 -> Total 360
    res3 = game.submit_answer(game.current_question.correct_index)
    assert res3.is_correct is True
    assert res3.energy_awarded == 140
    assert res3.current_score == 360
    assert res3.current_streak == 3

    game.next_question()

    # Question 4: Answer incorrectly -> Shields down to 2, Streak reset to 0
    wrong_idx = (game.current_question.correct_index + 1) % 4
    res4 = game.submit_answer(wrong_idx)
    assert res4.is_correct is False
    assert res4.energy_awarded == 0
    assert res4.current_score == 360
    assert res4.current_streak == 0
    assert res4.current_shields == 2
    assert game.shields == 2


def test_game_engine_shield_depletion_game_over():
    game = GameEngine(level=1, total_questions=5, seed=42)
    game.play()

    # Wrong answer 1 -> Shields 2
    w1 = (game.current_question.correct_index + 1) % 4
    res1 = game.submit_answer(w1)
    assert res1.current_shields == 2
    assert game.state == GameState.PLAYING

    game.next_question()

    # Wrong answer 2 -> Shields 1
    w2 = (game.current_question.correct_index + 1) % 4
    res2 = game.submit_answer(w2)
    assert res2.current_shields == 1
    assert game.state == GameState.PLAYING

    game.next_question()

    # Wrong answer 3 -> Shields 0 -> GameOver(victory=False)
    w3 = (game.current_question.correct_index + 1) % 4
    res3 = game.submit_answer(w3)
    assert res3.current_shields == 0
    assert res3.is_game_over is True
    assert res3.victory is False
    assert game.state == GameState.GAMEOVER
    assert game.victory is False

    summary = game.get_summary()
    assert summary.victory is False
    assert summary.shields == 0
    assert summary.questions_solved == 0
    assert summary.accuracy_percentage == 0
    assert summary.rank == "Ensign"


def test_game_engine_victory_and_rank_tiers():
    # 10 questions, perfect score -> Fleet Admiral
    game = GameEngine(level=2, total_questions=10, seed=777)
    game.play()

    for i in range(10):
        res = game.submit_answer(game.current_question.correct_index)
        if i < 9:
            game.next_question()

    assert game.state == GameState.GAMEOVER
    assert game.victory is True
    summary = game.get_summary()
    assert summary.accuracy_percentage == 100
    assert summary.rank == "Fleet Admiral"
    assert summary.questions_solved == 10

    # Test rank calculation helper directly
    game.questions_solved = 9
    game.current_question_index = 10
    assert game.calculate_rank() == "Fleet Admiral"

    game.questions_solved = 8
    assert game.calculate_rank() == "Chief Navigator"

    game.questions_solved = 6
    assert game.calculate_rank() == "Flight Lieutenant"

    game.questions_solved = 4
    assert game.calculate_rank() == "Ensign"


def test_game_engine_determinism():
    g1 = GameEngine(level=1, seed=42)
    g1.play()

    g2 = GameEngine(level=1, seed=42)
    g2.play()

    for _ in range(5):
        assert g1.current_question.prompt == g2.current_question.prompt
        assert g1.current_question.options == g2.current_question.options
        assert g1.current_question.correct_index == g2.current_question.correct_index
        g1.next_question()
        g2.next_question()


# =============================================================================
# Architecture_View.md User Entity Tests
# =============================================================================

def test_user_entity_interaction():
    user = User(user_id="U-101", username="CadetJane")
    game = GameEngine(level=1, seed=42)

    assert user.id == "U-101"
    assert user.username == "CadetJane"

    user.playGame(game)
    assert game.state == GameState.PLAYING
    assert user.viewScore(game) == 0

    game.submit_answer(game.current_question.correct_index)
    assert user.viewScore(game) == 100
    assert user.view_score(game) == 100
