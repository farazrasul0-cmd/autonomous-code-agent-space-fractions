"""Space Fractions Question Bank Module.

Implements the Question entity and QuestionBank management as specified in:
- Architecture_View.md:
  - ClassDiagram: Question (id, prompt, options, getPrompt(), getOptions()),
                  Admin.updateQuestions(questions: List<Question>)
  - SequenceDiagram1: Game -> Question: getPrompt(), check answer
  - SequenceDiagram2: Admin -> Question: update()
- Grade 6 Common Core Mathematics Standards (CCSS.MATH.CONTENT.6.NS)

Reuses space_fractions.fraction_math.MathQuestionGenerator without duplicating
fraction arithmetic or GCD/LCM implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from space_fractions.fraction_math import MathQuestion, MathQuestionGenerator


class QuestionError(ValueError):
    """Raised when question parameters or inputs are invalid."""
    pass


@dataclass
class Question:
    """Represents an interactive multiple-choice fraction challenge.
    
    Directly realizes the Question class from Architecture_View.md ClassDiagram:
    - id: string
    - prompt: string
    - options: List<string>
    - getPrompt(): string
    - getOptions(): List<string>
    """
    id: str
    prompt: str
    options: List[str]
    correct_index: int
    explanation: str = ""
    level: int = 1
    topic: str = "Fractions"

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise QuestionError("Question id must be a non-empty string.")
        if not self.prompt or not isinstance(self.prompt, str):
            raise QuestionError("Question prompt must be a non-empty string.")
        if not isinstance(self.options, (list, tuple)) or len(self.options) != 4:
            raise QuestionError(f"Question options must contain exactly 4 choices, got {len(self.options) if isinstance(self.options, (list, tuple)) else type(self.options)}.")
        if len(set(self.options)) != 4:
            raise QuestionError(f"Question options must be 4 distinct choices, got duplicates: {self.options}")
        if not isinstance(self.correct_index, int) or not (0 <= self.correct_index < 4):
            raise QuestionError(f"correct_index must be an integer in [0, 3], got {self.correct_index}.")
        if not isinstance(self.level, int) or self.level < 1:
            raise QuestionError(f"level must be a positive integer >= 1, got {self.level}.")

    def getPrompt(self) -> str:
        """ClassDiagram method: + getPrompt(): string."""
        return self.prompt

    def get_prompt(self) -> str:
        """Pythonic alias for getPrompt()."""
        return self.prompt

    def getOptions(self) -> List[str]:
        """ClassDiagram method: + getOptions(): List<string>."""
        return list(self.options)

    def get_options(self) -> List[str]:
        """Pythonic alias for getOptions()."""
        return list(self.options)

    @property
    def correct_answer(self) -> str:
        """Returns the text of the correct option."""
        return self.options[self.correct_index]

    def check_answer(self, selected_index: int) -> bool:
        """Validates a player's selected option index against the correct option."""
        if not isinstance(selected_index, int) or not (0 <= selected_index < 4):
            return False
        return selected_index == self.correct_index

    @classmethod
    def from_math_question(cls, mq: MathQuestion) -> 'Question':
        """Adapts a fraction_math.MathQuestion dataclass into a Question entity."""
        return cls(
            id=mq.id,
            prompt=mq.prompt,
            options=list(mq.options),
            correct_index=mq.correct_index,
            explanation=mq.explanation,
            level=mq.level,
            topic=mq.topic,
        )


class QuestionBank:
    """Manages question retrieval, caching, and dynamic generation for Space Fractions."""

    SUPPORTED_LEVELS = (1, 2, 3)

    def __init__(
        self,
        seed: Optional[int] = 42,
        custom_questions: Optional[Sequence[Question]] = None,
    ) -> None:
        self.seed = seed
        self.generator = MathQuestionGenerator(seed=seed if seed is not None else 42)
        self._custom_bank: Dict[str, Question] = {}
        if custom_questions:
            self.update_questions(custom_questions)

    def add_question(self, question: Question) -> None:
        """Adds a custom or administrative question to the question bank."""
        if not isinstance(question, Question):
            raise TypeError("question must be an instance of Question.")
        self._custom_bank[question.id] = question

    def update_questions(self, questions: Sequence[Question]) -> None:
        """Realizes Architecture_View.md ClassDiagram Admin.updateQuestions."""
        if not isinstance(questions, (list, tuple)):
            raise TypeError("questions must be a sequence of Question instances.")
        for q in questions:
            self.add_question(q)

    def get_question(self, level: int = 1, index: int = 1) -> Question:
        """Generates or retrieves a validated question for the specified level and index."""
        if not isinstance(level, int) or level not in self.SUPPORTED_LEVELS:
            raise QuestionError(f"Unsupported level: {level}. Supported levels: {self.SUPPORTED_LEVELS}.")
        if not isinstance(index, int) or index < 1:
            raise QuestionError(f"index must be a positive integer >= 1, got {index}.")

        custom_id = f"CUSTOM-L{level}-Q{index}"
        if custom_id in self._custom_bank:
            return self._custom_bank[custom_id]

        # Generate dynamically via verified MathQuestionGenerator
        mq = self.generator.generate(level=level, index=index)
        return Question.from_math_question(mq)

    def get_questions_for_level(self, level: int = 1, count: int = 10) -> List[Question]:
        """Generates a batch of sequential questions for a full mission sector."""
        if not isinstance(count, int) or count < 1:
            raise QuestionError(f"count must be a positive integer >= 1, got {count}.")
        return [self.get_question(level=level, index=i) for i in range(1, count + 1)]

    def count_custom_questions(self) -> int:
        """Returns the number of custom/admin questions registered."""
        return len(self._custom_bank)
