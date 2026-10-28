import math
import random
from dataclasses import dataclass
from typing import List, Tuple

class Fraction:
    def __init__(self, numerator: int, denominator: int = 1):
        if not isinstance(numerator, int) or not isinstance(denominator, int):
            raise TypeError("Numerator and denominator must be integers.")
        if denominator == 0:
            raise ValueError("Denominator cannot be zero.")
        if denominator < 0:
            numerator, denominator = -numerator, -denominator
        self.n = numerator
        self.d = denominator

    @staticmethod
    def gcd(a: int, b: int) -> int: return math.gcd(a, b)
    @staticmethod
    def lcm(a: int, b: int) -> int: return 0 if a == 0 or b == 0 else abs(a * b) // math.gcd(a, b)

    def simplify(self) -> 'Fraction':
        g = math.gcd(self.n, self.d)
        return Fraction(self.n // g, self.d // g)

    def add(self, o: 'Fraction') -> 'Fraction':
        cd = self.lcm(self.d, o.d)
        return Fraction(self.n * (cd // self.d) + o.n * (cd // o.d), cd).simplify()

    def subtract(self, o: 'Fraction') -> 'Fraction':
        cd = self.lcm(self.d, o.d)
        return Fraction(self.n * (cd // self.d) - o.n * (cd // o.d), cd).simplify()

    def equals(self, o: 'Fraction') -> bool:
        sa, sb = self.simplify(), o.simplify()
        return sa.n == sb.n and sa.d == sb.d

    def compare(self, o: 'Fraction') -> int:
        diff = (self.n * o.d) - (o.n * self.d)
        return 1 if diff > 0 else (-1 if diff < 0 else 0)

    @classmethod
    def from_string(cls, s: str) -> 'Fraction':
        if not isinstance(s, str): raise TypeError("Must be string.")
        s = s.strip()
        if not s: raise ValueError("Empty string.")
        if "/" in s:
            p = s.split("/")
            if len(p) != 2: raise ValueError(f"Invalid fraction: {s}")
            return cls(int(p[0].strip()), int(p[1].strip()))
        return cls(int(s), 1)

    def __str__(self) -> str:
        reduced = self.simplify()
        return str(reduced.n) if reduced.d == 1 else f"{reduced.n}/{reduced.d}"
    def __repr__(self) -> str: return f"Fraction({self.n}, {self.d})"
    def __eq__(self, o: object) -> bool: return self.equals(o) if isinstance(o, Fraction) else False

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
    def __init__(self, seed: int = 42): self.rng = random.Random(seed)
    def _make_options(self, correct: str, distractors: List[str]) -> Tuple[List[str], int]:
        opts = [correct]
        for d in distractors:
            if d not in opts: opts.append(d)
        while len(opts) < 4: opts.append(f"{correct}?")
        opts = opts[:4]
        self.rng.shuffle(opts)
        return opts, opts.index(correct)

    def generate(self, level: int = 1, index: int = 1) -> MathQuestion:
        if level == 1:
            n = self.rng.randint(2, 6); d = n * self.rng.randint(2, 4); f = Fraction(n, d)
            c = str(f.simplify()); dis = [f"{n}/{d}", f"{f.simplify().n}/{d}", f"{n}/{f.simplify().d}"]
            opts, c_idx = self._make_options(c, dis)
            return MathQuestion(f"L1-S{index}", 1, "Simplification", f"Simplify {n}/{d}.", opts, c_idx, f"{n}/{d} = {c}.")
        elif level == 2:
            d = self.rng.randint(3, 8); n1, n2 = self.rng.randint(1, d - 1), self.rng.randint(1, d - 1)
            f1, f2 = Fraction(n1, d), Fraction(n2, d); c = str(f1.add(f2))
            opts, c_idx = self._make_options(c, [f"{n1+n2}/{d*2}", f"{n1+n2+1}/{d}", f"{n1*n2}/{d}"])
            return MathQuestion(f"L2-A{index}", 2, "Addition", f"What is {f1} + {f2}?", opts, c_idx, f"Sum is {c}.")
        else:
            f1, f2 = Fraction(self.rng.randint(1, 3), self.rng.randint(2, 4)), Fraction(self.rng.randint(1, 3), self.rng.randint(2, 5))
            res = f1.add(f2); c = str(res); cd = Fraction.lcm(f1.d, f2.d)
            opts, c_idx = self._make_options(c, [f"{f1.n+f2.n}/{f1.d+f2.d}", f"{f1.n+f2.n}/{cd}", f"{f1.n*f2.n}/{f1.d*f2.d}"])
            return MathQuestion(f"L3-A{index}", 3, "Addition", f"What is {f1} + {f2}?", opts, c_idx, f"Sum is {c}.")
