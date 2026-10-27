from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
from space_fractions.fraction_math import MathQuestion, MathQuestionGenerator

class QuestionError(ValueError): pass

@dataclass
class Question:
    id: str
    prompt: str
    options: List[str]
    correct_index: int
    explanation: str = ""
    level: int = 1
    topic: str = "Fractions"

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str): raise QuestionError("id must be a non-empty string.")
        if not self.prompt or not isinstance(self.prompt, str): raise QuestionError("prompt must be a non-empty string.")
        if not isinstance(self.options, (list, tuple)) or len(self.options) != 4: raise QuestionError("must contain exactly 4 choices.")
        if len(set(self.options)) != 4: raise QuestionError("must be 4 distinct choices.")
        if not isinstance(self.correct_index, int) or not (0 <= self.correct_index < 4): raise QuestionError("correct_index must be an integer in range [0, 3].")
        if not isinstance(self.level, int) or self.level < 1: raise QuestionError("level must be a positive integer >= 1.")

    def getPrompt(self) -> str: return self.prompt
    def get_prompt(self) -> str: return self.prompt
    def getOptions(self) -> List[str]: return list(self.options)
    def get_options(self) -> List[str]: return list(self.options)
    @property
    def correct_answer(self) -> str: return self.options[self.correct_index]
    def check_answer(self, i: int) -> bool: return i == self.correct_index if isinstance(i, int) and 0 <= i < 4 else False
    @classmethod
    def from_math_question(cls, mq: MathQuestion) -> 'Question':
        return cls(id=mq.id, prompt=mq.prompt, options=list(mq.options), correct_index=mq.correct_index, explanation=mq.explanation, level=mq.level, topic=mq.topic)

CURATED_QUESTIONS: List[Question] = [
    Question("L1-Q1", "Simplify 4/8.", ["2/4", "1/2", "1/4", "2/3"], 1, "4/8 = 1/2.", 1, "Simplification"),
    Question("L1-Q2", "Equivalent to 2/3?", ["4/6", "3/4", "5/8", "2/6"], 0, "(2*2)/(3*2) = 4/6.", 1, "Equivalent Fractions"),
    Question("L1-Q3", "Simplify 6/9.", ["1/3", "3/4", "2/3", "4/6"], 2, "6/9 = 2/3.", 1, "Simplification"),
    Question("L1-Q4", "Equivalent to 3/5?", ["6/12", "8/15", "5/3", "9/15"], 3, "(3*3)/(5*3) = 9/15.", 1, "Equivalent Fractions"),
    Question("L1-Q5", "Simplify 10/25.", ["1/5", "2/5", "2/3", "5/10"], 1, "10/25 = 2/5.", 1, "Simplification"),
    Question("L1-Q6", "Equivalent to 1/4?", ["3/12", "2/6", "4/12", "1/8"], 0, "1/4 = 3/12.", 1, "Equivalent Fractions"),
    Question("L1-Q7", "Simplify 12/16.", ["2/3", "6/8", "3/4", "4/5"], 2, "12/16 = 3/4.", 1, "Simplification"),
    Question("L1-Q8", "Equivalent to 5/6?", ["10/14", "12/16", "20/25", "15/18"], 3, "5/6 = 15/18.", 1, "Equivalent Fractions"),
    Question("L2-Q1", "What is 1/5 + 2/5?", ["3/5", "3/10", "2/25", "1/10"], 0, "(1+2)/5 = 3/5.", 2, "Addition"),
    Question("L2-Q2", "Compare 3/4 and 2/3.", ["3/4 < 2/3", "3/4 > 2/3", "3/4 = 2/3", "Cannot determine"], 1, "9/12 > 8/12.", 2, "Comparison"),
    Question("L2-Q3", "What is 7/8 - 3/8?", ["5/8", "4/16", "1/2", "1/4"], 2, "4/8 = 1/2.", 2, "Subtraction"),
    Question("L2-Q4", "Compare 2/5 and 3/7.", ["2/5 > 3/7", "2/5 = 3/7", "Cannot determine", "2/5 < 3/7"], 3, "14 < 15.", 2, "Comparison"),
    Question("L2-Q5", "What is 2/7 + 3/7?", ["5/14", "5/7", "6/49", "1/7"], 1, "Sum is 5/7.", 2, "Addition"),
    Question("L2-Q6", "Compare 5/6 and 7/8.", ["5/6 < 7/8", "5/6 > 7/8", "5/6 = 7/8", "5/6 >= 1"], 0, "20/24 < 21/24.", 2, "Comparison"),
    Question("L2-Q7", "What is 5/9 - 2/9?", ["4/9", "3/18", "1/3", "7/9"], 2, "3/9 = 1/3.", 2, "Subtraction"),
    Question("L2-Q8", "Compare 4/5 and 8/10.", ["4/5 > 8/10", "4/5 < 8/10", "4/5 != 8/10", "4/5 = 8/10"], 3, "Equal: 4/5.", 2, "Comparison"),
    Question("L3-Q1", "What is 1/2 + 1/3?", ["5/6", "2/5", "2/6", "1/6"], 0, "3/6 + 2/6 = 5/6.", 3, "Addition"),
    Question("L3-Q2", "What is 3/4 - 1/2?", ["2/2", "1/4", "2/4", "1/8"], 1, "3/4 - 2/4 = 1/4.", 3, "Subtraction"),
    Question("L3-Q3", "What is 2/3 + 1/4?", ["3/7", "3/12", "11/12", "8/12"], 2, "8/12 + 3/12 = 11/12.", 3, "Addition"),
    Question("L3-Q4", "What is 5/6 - 1/3?", ["4/3", "4/6", "2/6", "1/2"], 3, "5/6 - 2/6 = 1/2.", 3, "Subtraction"),
    Question("L3-Q5", "What is 3/5 + 1/2?", ["4/7", "11/10", "4/10", "1/10"], 1, "6/10 + 5/10 = 11/10.", 3, "Addition"),
    Question("L3-Q6", "What is 4/5 - 2/3?", ["2/15", "2/2", "2/8", "6/15"], 0, "12/15 - 10/15 = 2/15.", 3, "Subtraction"),
    Question("L3-Q7", "What is 3/8 + 1/4?", ["4/12", "4/8", "5/8", "1/2"], 2, "3/8 + 2/8 = 5/8.", 3, "Addition"),
    Question("L3-Q8", "What is 7/10 - 2/5?", ["5/5", "5/10", "1/2", "3/10"], 3, "7/10 - 4/10 = 3/10.", 3, "Subtraction"),
]

CURATED_BY_LEVEL: Dict[int, List[Question]] = {
    1: [q for q in CURATED_QUESTIONS if q.level == 1],
    2: [q for q in CURATED_QUESTIONS if q.level == 2],
    3: [q for q in CURATED_QUESTIONS if q.level == 3],
}

class QuestionBank:
    SUPPORTED_LEVELS = (1, 2, 3)
    CURATED_QUESTIONS = CURATED_QUESTIONS

    def __init__(self, seed: Optional[int] = 42, custom_questions: Optional[Sequence[Question]] = None, use_curated: bool = True) -> None:
        self.seed = seed
        self.use_curated = use_curated
        self.generator = MathQuestionGenerator(seed=seed if seed is not None else 42)
        self._custom_bank: Dict[str, Question] = {}
        if custom_questions: self.update_questions(custom_questions)

    def add_question(self, q: Question) -> None:
        if not isinstance(q, Question): raise TypeError("Question instance required.")
        self._custom_bank[q.id] = q

    def update_questions(self, qs: Sequence[Question]) -> None:
        if not isinstance(qs, (list, tuple)): raise TypeError("Sequence required.")
        for q in qs: self.add_question(q)

    def get_question(self, level: int = 1, index: int = 1) -> Question:
        if not isinstance(level, int) or level not in self.SUPPORTED_LEVELS: raise QuestionError(f"Unsupported level: {level}.")
        if not isinstance(index, int) or index < 1: raise QuestionError(f"index must be a positive integer >= 1, got {index}.")
        cid = f"CUSTOM-L{level}-Q{index}"
        if cid in self._custom_bank: return self._custom_bank[cid]
        if self.use_curated:
            cur = CURATED_BY_LEVEL.get(level, [])
            if 1 <= index <= len(cur): return cur[index - 1]
        mq = self.generator.generate(level=level, index=index)
        return Question.from_math_question(mq)

    def get_questions_for_level(self, level: int = 1, count: int = 10) -> List[Question]:
        if not isinstance(count, int) or count < 1: raise QuestionError(f"count must be a positive integer >= 1, got {count}.")
        return [self.get_question(level=level, index=i) for i in range(1, count + 1)]

    def count_custom_questions(self) -> int: return len(self._custom_bank)
    @classmethod
    def get_curated_questions(cls, level: Optional[int] = None) -> List[Question]:
        return list(CURATED_QUESTIONS) if level is None else list(CURATED_BY_LEVEL.get(level, []))
    @classmethod
    def total_curated_questions(cls) -> int: return len(CURATED_QUESTIONS)
