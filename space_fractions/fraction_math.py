import math
import random
from dataclasses import dataclass
from typing import List, Tuple


class Fraction:
    def __init__(self, numerator: int, denominator: int = 1):
        if not isinstance(numerator, int) or not isinstance(denominator, int):
            raise TypeError('Numerator and denominator must be integers.')
        if denominator == 0:
            raise ValueError('Denominator cannot be zero.')
        if denominator < 0:
            numerator, denominator = -numerator, -denominator
        self.n = numerator
        self.d = denominator

    @staticmethod
    def gcd(a: int, b: int) -> int:
        return math.gcd(a, b)

    @staticmethod
    def lcm(a: int, b: int) -> int:
        return 0 if a == 0 or b == 0 else abs(a * b) // math.gcd(a, b)

    def simplify(self) -> 'Fraction':
        g = math.gcd(self.n, self.d)
        return Fraction(self.n // g, self.d // g)

    def add(self, other: 'Fraction') -> 'Fraction':
        cd = self.lcm(self.d, other.d)
        return Fraction(self.n * (cd // self.d) + other.n * (cd // other.d), cd).simplify()

    def subtract(self, other: 'Fraction') -> 'Fraction':
        cd = self.lcm(self.d, other.d)
        return Fraction(self.n * (cd // self.d) - other.n * (cd // other.d), cd).simplify()

    def equals(self, other: 'Fraction') -> bool:
        sa, sb = self.simplify(), other.simplify()
        return sa.n == sb.n and sa.d == sb.d

    def compare(self, other: 'Fraction') -> int:
        diff = (self.n * other.d) - (other.n * self.d)
        return 1 if diff > 0 else (-1 if diff < 0 else 0)

    def __str__(self) -> str:
        return str(self.n) if self.d == 1 else f'{self.n}/{self.d}'

    def __repr__(self) -> str:
        return f'Fraction({self.n}, {self.d})'

    def __eq__(self, other: object) -> bool:
        return self.equals(other) if isinstance(other, Fraction) else False


@dataclass
class MathQuestion:
    id: str
    level: int
    topic: str
    prompt: str
    options: List[str]
    correct_index: int
    explanation: str


class MathQuestionGenerator:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def _make_options(self, correct: str, distractors: List[str]) -> Tuple[List[str], int]:
        opts = [correct]
        for d in distractors:
            if d not in opts:
                opts.append(d)
        while len(opts) < 4:
            opts.append(f'{correct}?')
        opts = opts[:4]
        self.rng.shuffle(opts)
        return opts, opts.index(correct)

    def generate(self, level: int = 1, index: int = 1) -> MathQuestion:
        if level == 1:
            if index % 2 == 0:
                n = self.rng.randint(2, 9)
                d = self.rng.randint(2, 9) * n
                f = Fraction(n, d)
                s = f.simplify()
                correct = str(s)
                distractors = [
                    f'{n}/{d}',
                    f'{s.n}/{d}',
                    f'{n}/{s.d}',
                    f'{s.n + 1}/{s.d}',
                ]
                options, correct_index = self._make_options(correct, distractors)
                return MathQuestion(
                    id=f'L1-S{index}',
                    level=1,
                    topic='Simplification',
                    prompt=f'Simplify the fraction {n}/{d}.',
                    options=options,
                    correct_index=correct_index,
                    explanation=f'Divide numerator and denominator by their GCD {n // s.n} to get {correct}.',
                )
            else:
                n = self.rng.randint(1, 9)
                d = self.rng.randint(2, 9)
                f = Fraction(n, d)
                m = self.rng.randint(2, 5)
                eq = Fraction(n * m, d * m)
                correct = str(eq)
                distractors = [
                    f'{n}/{d}',
                    f'{n * m}/{d}',
                    f'{n}/{d * m}',
                    f'{n + m}/{d + m}',
                ]
                options, correct_index = self._make_options(correct, distractors)
                return MathQuestion(
                    id=f'L1-E{index}',
                    level=1,
                    topic='Equivalent Fractions',
                    prompt=f'Which fraction is equivalent to {n}/{d}?',
                    options=options,
                    correct_index=correct_index,
                    explanation=f'Multiply numerator and denominator by {m} to get {correct}.',
                )
        elif level == 2:
            if index % 2 == 0:
                d = self.rng.randint(2, 9)
                n1 = self.rng.randint(1, d - 1)
                n2 = self.rng.randint(1, d - 1)
                f1 = Fraction(n1, d)
                f2 = Fraction(n2, d)
                result = f1.add(f2)
                correct = str(result)
                distractors = [
                    f'{n1 + n2}/{d + d}',
                    f'{n1 + n2}/{d}',
                    f'{n1 * n2}/{d}',
                    f'{n1 + n2}/{d * d}',
                ]
                options, correct_index = self._make_options(correct, distractors)
                return MathQuestion(
                    id=f'L2-A{index}',
                    level=2,
                    topic='Like-Denominator Addition',
                    prompt=f'What is {f1} + {f2}?',
                    options=options,
                    correct_index=correct_index,
                    explanation=f'Add the numerators: ({n1} + {n2})/{d} = {correct}.',
                )
            else:
                n1 = self.rng.randint(1, 9)
                d1 = self.rng.randint(2, 9)
                n2 = self.rng.randint(1, 9)
                d2 = self.rng.randint(2, 9)
                f1 = Fraction(n1, d1)
                f2 = Fraction(n2, d2)
                c = f1.compare(f2)
                if c > 0:
                    correct = f'{f1} > {f2}'
                    opposite = f'{f1} < {f2}'
                elif c < 0:
                    correct = f'{f1} < {f2}'
                    opposite = f'{f1} > {f2}'
                else:
                    correct = f'{f1} = {f2}'
                    opposite = f'{f1} > {f2}'
                options, correct_index = self._make_options(
                    correct, [opposite, f'{f1} = {f2}', 'Cannot be determined']
                )
                return MathQuestion(
                    id=f'L2-C{index}',
                    level=2,
                    topic='Fraction Comparison',
                    prompt=f'Compare {f1} and {f2}.',
                    options=options,
                    correct_index=correct_index,
                    explanation=f'Cross-multiply: {f1} vs {f2} gives {correct}.',
                )
        else:
            if index % 2 != 0:
                n1 = self.rng.randint(1, 9)
                d1 = self.rng.randint(2, 9)
                n2 = self.rng.randint(1, 9)
                d2 = self.rng.randint(2, 9)
                f1 = Fraction(n1, d1)
                f2 = Fraction(n2, d2)
                result = f1.add(f2)
                correct = str(result)
                cd = Fraction.lcm(d1, d2)
                wrong_num = n1 * (cd // d1) + n2 * (cd // d2) + self.rng.randint(1, 3)
                distractors = [
                    f'{n1 + n2}/{d1 + d2}',
                    f'{n1 + n2}/{cd}',
                    f'{wrong_num}/{cd}',
                    f'{n1 * n2}/{d1 * d2}',
                ]
                options, correct_index = self._make_options(correct, distractors)
                return MathQuestion(
                    id=f'L3-A{index}',
                    level=3,
                    topic='Unlike-Denominator Addition',
                    prompt=f'What is {f1} + {f2}?',
                    options=options,
                    correct_index=correct_index,
                    explanation=f'Find a common denominator {cd}, then add to get {correct}.',
                )
            else:
                n1 = self.rng.randint(1, 9)
                d1 = self.rng.randint(2, 9)
                n2 = self.rng.randint(1, 9)
                d2 = self.rng.randint(2, 9)
                f1 = Fraction(n1, d1)
                f2 = Fraction(n2, d2)
                result = f1.subtract(f2)
                correct = str(result)
                cd = Fraction.lcm(d1, d2)
                wrong_num = n1 * (cd // d1) - n2 * (cd // d2) + self.rng.randint(1, 3)
                distractors = [
                    f'{n1 - n2}/{d1 - d2}',
                    f'{n1 - n2}/{cd}',
                    f'{wrong_num}/{cd}',
                    f'{n1 * n2}/{d1 * d2}',
                ]
                options, correct_index = self._make_options(correct, distractors)
                return MathQuestion(
                    id=f'L3-S{index}',
                    level=3,
                    topic='Unlike-Denominator Subtraction',
                    prompt=f'What is {f1} - {f2}?',
                    options=options,
                    correct_index=correct_index,
                    explanation=f'Find a common denominator {cd}, then subtract to get {correct}.',
                )
