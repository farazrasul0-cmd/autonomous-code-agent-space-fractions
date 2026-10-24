"""Space Fractions Game Engine Module.

Implements the Game entity, state machine, and gameplay mechanics as specified in:
- Architecture_View.md:
  - ClassDiagram: Game (id, score, play(), viewScore()), User (id, username, playGame(), viewScore())
  - StateDiagram: Playing <-> Paused, Playing -> GameOver
  - ActivityDiagram: Play Game -> Check Game Over -> Calculate Score -> Display Score
  - SequenceDiagram1: User -> Game -> Question: play, getPrompt, submit answer, check answer, display result
- space_fractions/game.js reference gameplay rules:
  - Initial Shields: 3, Base Correct Energy: +100, Streak Bonus: (streak - 1) * 20
  - Shield depletion at 0 shields triggers GameOver (emergency warp out / defeat)
  - Clearing all questions triggers GameOver (mission success / victory)
  - Accuracy and rank tiers: Fleet Admiral (>=90%), Chief Navigator (>=75%), Flight Lieutenant (>=50%), Ensign (<50%)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from space_fractions.question_bank import Question, QuestionBank, QuestionError


class GameState(str, Enum):
    TITLE = "TITLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    GAMEOVER = "GAMEOVER"


class GameEngineError(Exception):
    """Raised when an illegal game state transition or invalid operation is attempted."""
    pass


@dataclass
class TurnResult:
    """Reports the outcome of submitting an answer to the active challenge."""
    is_correct: bool
    selected_index: int
    correct_index: int
    explanation: str
    energy_awarded: int
    current_score: int
    current_shields: int
    current_streak: int
    is_game_over: bool
    victory: bool


@dataclass
class GameSummary:
    """End-of-mission debriefing and performance statistics."""
    game_id: str
    level: int
    score: int
    shields: int
    questions_attempted: int
    questions_solved: int
    accuracy_percentage: int
    rank: str
    victory: bool


class GameEngine:
    """Core game controller and state machine for Space Fractions.
    
    Directly realizes the Game entity from Architecture_View.md ClassDiagram:
    - id: string
    - score: int
    - play(): void
    - viewScore(): int
    """

    INITIAL_SHIELDS: int = 3
    BASE_CORRECT_SCORE: int = 100
    STREAK_BONUS_MULTIPLIER: int = 20
    DEFAULT_TOTAL_QUESTIONS: int = 10

    def __init__(
        self,
        game_id: Optional[str] = None,
        level: int = 1,
        total_questions: int = DEFAULT_TOTAL_QUESTIONS,
        seed: Optional[int] = 42,
        question_bank: Optional[QuestionBank] = None,
    ) -> None:
        if not isinstance(level, int) or level not in (1, 2, 3):
            raise GameEngineError(f"Level must be 1, 2, or 3, got {level}.")
        if not isinstance(total_questions, int) or total_questions < 1:
            raise GameEngineError(f"total_questions must be positive integer >= 1, got {total_questions}.")

        self.id: str = game_id or f"GAME-{uuid.uuid4().hex[:8].upper()}"
        self.level: int = level
        self.total_questions: int = total_questions
        self.seed: Optional[int] = seed
        self.question_bank: QuestionBank = question_bank or QuestionBank(seed=seed)

        # State variables matching Architecture_View.md StateDiagram
        self.state: GameState = GameState.TITLE
        self.score: int = 0
        self.shields: int = self.INITIAL_SHIELDS
        self.streak: int = 0
        self.current_question_index: int = 0
        self.questions_solved: int = 0
        self.current_question: Optional[Question] = None
        self.last_turn_result: Optional[TurnResult] = None
        self.victory: bool = False

    # -------------------------------------------------------------------------
    # Architecture_View.md ClassDiagram Interface
    # -------------------------------------------------------------------------
    def play(self) -> None:
        """Starts a game session, transitioning state to PLAYING.
        
        ClassDiagram interface: + play(): void
        StateDiagram: [*] -> Playing
        """
        self.state = GameState.PLAYING
        self.score = 0
        self.shields = self.INITIAL_SHIELDS
        self.streak = 0
        self.current_question_index = 0
        self.questions_solved = 0
        self.victory = False
        self.last_turn_result = None

        if self.seed is not None:
            self.question_bank = QuestionBank(seed=self.seed)

        self.next_question()

    def viewScore(self) -> int:
        """Returns the current accumulated score.
        
        ClassDiagram interface: + viewScore(): int
        """
        return self.score

    def view_score(self) -> int:
        """Pythonic alias for viewScore()."""
        return self.score

    # -------------------------------------------------------------------------
    # Architecture_View.md StateDiagram Transitions
    # -------------------------------------------------------------------------
    def pause(self) -> None:
        """Pauses the active mission.
        
        StateDiagram: Playing --> Paused : pause()
        """
        if self.state != GameState.PLAYING:
            raise GameEngineError(f"Cannot pause when game state is {self.state.value}. Must be PLAYING.")
        self.state = GameState.PAUSED

    def resume(self) -> None:
        """Resumes a paused mission.
        
        StateDiagram: Paused --> Playing : resume()
        """
        if self.state != GameState.PAUSED:
            raise GameEngineError(f"Cannot resume when game state is {self.state.value}. Must be PAUSED.")
        self.state = GameState.PLAYING

    def gameOver(self, victory: bool = True) -> None:
        """Terminates the game session with victory or shield depletion.
        
        StateDiagram: Playing --> GameOver : gameOver()
        """
        if self.state not in (GameState.PLAYING, GameState.PAUSED):
            raise GameEngineError(f"Cannot trigger gameOver from state {self.state.value}.")
        self.state = GameState.GAMEOVER
        self.victory = victory

    def game_over(self, victory: bool = True) -> None:
        """Pythonic alias for gameOver()."""
        self.gameOver(victory=victory)

    # -------------------------------------------------------------------------
    # SequenceDiagram1 & ActivityDiagram Workflow
    # -------------------------------------------------------------------------
    def next_question(self) -> Optional[Question]:
        """Advances to the next question in the mission sequence."""
        if self.state != GameState.PLAYING:
            raise GameEngineError(f"Cannot advance question in state {self.state.value}.")

        if self.current_question_index >= self.total_questions:
            self.gameOver(victory=True)
            self.current_question = None
            return None

        self.current_question_index += 1
        self.current_question = self.question_bank.get_question(
            level=self.level,
            index=self.current_question_index,
        )
        return self.current_question

    def submit_answer(self, selected_index: int) -> TurnResult:
        """Validates player choice, updates shields/score, and manages state transitions.
        
        SequenceDiagram1: submit answer -> check answer -> return result -> display result
        """
        if self.state != GameState.PLAYING:
            raise GameEngineError(f"Cannot submit answer when state is {self.state.value}. Must be PLAYING.")
        if self.current_question is None:
            raise GameEngineError("No active question to answer.")
        if not isinstance(selected_index, int) or not (0 <= selected_index < 4):
            raise GameEngineError(f"selected_index must be an integer in range [0, 3], got {selected_index}.")

        is_correct = (selected_index == self.current_question.correct_index)

        energy_awarded = 0
        if is_correct:
            self.streak += 1
            streak_bonus = (self.streak - 1) * self.STREAK_BONUS_MULTIPLIER if self.streak > 1 else 0
            energy_awarded = self.BASE_CORRECT_SCORE + streak_bonus
            self.score += energy_awarded
            self.questions_solved += 1
        else:
            self.streak = 0
            self.shields -= 1

        is_game_over = False
        victory = False
        if self.shields <= 0:
            is_game_over = True
            victory = False
            self.gameOver(victory=False)
        elif self.current_question_index >= self.total_questions:
            is_game_over = True
            victory = True
            self.gameOver(victory=True)

        result = TurnResult(
            is_correct=is_correct,
            selected_index=selected_index,
            correct_index=self.current_question.correct_index,
            explanation=self.current_question.explanation,
            energy_awarded=energy_awarded,
            current_score=self.score,
            current_shields=self.shields,
            current_streak=self.streak,
            is_game_over=is_game_over,
            victory=victory,
        )
        self.last_turn_result = result
        return result

    # -------------------------------------------------------------------------
    # Scoring, Accuracy, and Rank Calculation (Matching game.js)
    # -------------------------------------------------------------------------
    def calculate_accuracy(self) -> int:
        """Calculates percentage accuracy rounded to the nearest integer."""
        if self.current_question_index == 0:
            return 0
        return round((self.questions_solved / self.current_question_index) * 100)

    def calculate_rank(self) -> str:
        """Determines naval space rank based on mission accuracy."""
        acc = self.calculate_accuracy()
        if acc >= 90:
            return "Fleet Admiral"
        elif acc >= 75:
            return "Chief Navigator"
        elif acc >= 50:
            return "Flight Lieutenant"
        return "Ensign"

    def get_summary(self) -> GameSummary:
        """Returns end-of-mission performance summary."""
        return GameSummary(
            game_id=self.id,
            level=self.level,
            score=self.score,
            shields=self.shields,
            questions_attempted=self.current_question_index,
            questions_solved=self.questions_solved,
            accuracy_percentage=self.calculate_accuracy(),
            rank=self.calculate_rank(),
            victory=self.victory,
        )


class User:
    """Represents a student user interacting with the Space Fractions Game.
    
    Directly realizes the User class from Architecture_View.md ClassDiagram:
    - id: string
    - username: string
    - playGame(game: Game): void
    - viewScore(game: Game): int
    """

    def __init__(self, user_id: str, username: str) -> None:
        self.id: str = user_id
        self.username: str = username

    def playGame(self, game: GameEngine) -> None:
        """ClassDiagram method: + playGame(game: Game): void."""
        game.play()

    def play_game(self, game: GameEngine) -> None:
        """Pythonic alias for playGame()."""
        self.playGame(game)

    def viewScore(self, game: GameEngine) -> int:
        """ClassDiagram method: + viewScore(game: Game): int."""
        return game.viewScore()

    def view_score(self, game: GameEngine) -> int:
        """Pythonic alias for viewScore()."""
        return self.viewScore(game)


# Alias Game to GameEngine for exact ClassDiagram naming parity
Game = GameEngine
