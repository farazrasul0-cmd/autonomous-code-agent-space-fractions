from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import uuid
from space_fractions.question_bank import Question, QuestionBank, QuestionError

class GameState(str, Enum):
    TITLE = "TITLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    GAMEOVER = "GAMEOVER"

class GameEngineError(Exception): pass

@dataclass
class TurnResult:
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
    INITIAL_SHIELDS: int = 3
    BASE_CORRECT_SCORE: int = 100
    STREAK_BONUS_MULTIPLIER: int = 20
    DEFAULT_TOTAL_QUESTIONS: int = 10

    def __init__(self, game_id: Optional[str] = None, level: int = 1, total_questions: int = DEFAULT_TOTAL_QUESTIONS, seed: Optional[int] = 42, question_bank: Optional[QuestionBank] = None) -> None:
        if not isinstance(level, int) or level not in (1, 2, 3): raise GameEngineError(f"Invalid level: {level}")
        if not isinstance(total_questions, int) or total_questions < 1: raise GameEngineError(f"Invalid total_q: {total_questions}")
        self.id: str = game_id or f"GAME-{uuid.uuid4().hex[:8].upper()}"
        self.level: int = level
        self.total_questions: int = total_questions
        self.seed: Optional[int] = seed
        self.question_bank: QuestionBank = question_bank or QuestionBank(seed=seed)
        self.state: GameState = GameState.TITLE
        self.score: int = 0
        self.shields: int = self.INITIAL_SHIELDS
        self.streak: int = 0
        self.current_question_index: int = 0
        self.questions_solved: int = 0
        self.current_question: Optional[Question] = None
        self.last_turn_result: Optional[TurnResult] = None
        self.victory: bool = False

    def play(self) -> None:
        self.state = GameState.PLAYING
        self.score = 0; self.shields = self.INITIAL_SHIELDS; self.streak = 0
        self.current_question_index = 0; self.questions_solved = 0; self.victory = False
        if self.seed is not None: self.question_bank = QuestionBank(seed=self.seed)
        self.next_question()

    def viewScore(self) -> int: return self.score
    def view_score(self) -> int: return self.score

    def pause(self) -> None:
        if self.state != GameState.PLAYING: raise GameEngineError(f"Cannot pause when game state is {self.state.value}. Must be PLAYING.")
        self.state = GameState.PAUSED

    def resume(self) -> None:
        if self.state != GameState.PAUSED: raise GameEngineError(f"Cannot resume when game state is {self.state.value}. Must be PAUSED.")
        self.state = GameState.PLAYING

    def gameOver(self, victory: bool = True) -> None:
        if self.state not in (GameState.PLAYING, GameState.PAUSED): raise GameEngineError("Cannot end game.")
        self.state = GameState.GAMEOVER
        self.victory = victory

    def game_over(self, victory: bool = True) -> None: self.gameOver(victory)

    def next_question(self) -> Optional[Question]:
        if self.state != GameState.PLAYING: raise GameEngineError(f"Cannot advance in {self.state.value}.")
        if self.current_question_index >= self.total_questions:
            self.gameOver(victory=True); self.current_question = None; return None
        self.current_question_index += 1
        self.current_question = self.question_bank.get_question(level=self.level, index=self.current_question_index)
        return self.current_question

    def submit_answer(self, selected_index: int) -> TurnResult:
        if self.state != GameState.PLAYING: raise GameEngineError(f"Cannot submit in {self.state.value}.")
        if self.current_question is None: raise GameEngineError("No question.")
        if not isinstance(selected_index, int) or not (0 <= selected_index < 4): raise GameEngineError("selected_index in [0, 3].")
        is_correct = (selected_index == self.current_question.correct_index)
        energy_awarded = 0
        if is_correct:
            self.streak += 1
            bonus = (self.streak - 1) * self.STREAK_BONUS_MULTIPLIER if self.streak > 1 else 0
            energy_awarded = self.BASE_CORRECT_SCORE + bonus
            self.score += energy_awarded; self.questions_solved += 1
        else:
            self.streak = 0; self.shields -= 1
        is_game_over = False; victory = False
        if self.shields <= 0:
            is_game_over = True; self.gameOver(victory=False)
        elif self.current_question_index >= self.total_questions:
            is_game_over = True; victory = True; self.gameOver(victory=True)
        res = TurnResult(is_correct, selected_index, self.current_question.correct_index, self.current_question.explanation, energy_awarded, self.score, self.shields, self.streak, is_game_over, victory)
        self.last_turn_result = res
        return res

    def calculate_accuracy(self) -> int:
        return 0 if self.current_question_index == 0 else round((self.questions_solved / self.current_question_index) * 100)

    def calculate_rank(self) -> str:
        acc = self.calculate_accuracy()
        if acc >= 90: return "Fleet Admiral"
        if acc >= 75: return "Chief Navigator"
        if acc >= 50: return "Flight Lieutenant"
        return "Ensign"

    def get_summary(self) -> GameSummary:
        return GameSummary(self.id, self.level, self.score, self.shields, self.current_question_index, self.questions_solved, self.calculate_accuracy(), self.calculate_rank(), self.victory)

class User:
    def __init__(self, user_id: str, username: str) -> None:
        self.id = user_id; self.username = username
    def playGame(self, game: GameEngine) -> None: game.play()
    def play_game(self, game: GameEngine) -> None: game.play()
    def viewScore(self, game: GameEngine) -> int: return game.viewScore()
    def view_score(self, game: GameEngine) -> int: return game.view_score()

Game = GameEngine
