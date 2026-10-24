"""Unit tests for Space Fractions Mathematical Engine.

Covers:
1. Equivalent fractions and simplification.
2. Fraction comparisons (greater than, less than, equality).
3. Addition and subtraction with like and unlike denominators.
4. Negative results and zero-denominator rejection.
5. Reproducibility under a fixed random seed.
6. Correct answer option presence and distractor uniqueness.

NOTE: In accordance with Stage F3A safety constraints, this test suite is authored
for verification but its execution is withheld pending technical-lead review.
"""

import pytest

pytestmark = pytest.mark.host_integration


@pytest.fixture(autouse=True, scope="module")
def _load_module_globals():
    """Lazy import of target module at test execution time, not at collection."""
    import space_fractions.fraction_math as fm
    globals()["Fraction"] = fm.Fraction
    globals()["MathQuestionGenerator"] = fm.MathQuestionGenerator


def test_fraction_simplification():
    f = Fraction(8, 12).simplify()
    assert f.n == 2
    assert f.d == 3
    assert str(f) == "2/3"

    f2 = Fraction(15, 25).simplify()
    assert f2.n == 3
    assert f2.d == 5

    f3 = Fraction(7, 7).simplify()
    assert f3.n == 1
    assert f3.d == 1
    assert str(f3) == "1"


def test_fraction_equivalence():
    f1 = Fraction(1, 2)
    f2 = Fraction(4, 8)
    f3 = Fraction(3, 6)
    f4 = Fraction(2, 3)

    assert f1.equals(f2)
    assert f1 == f3
    assert not f1.equals(f4)


def test_fraction_comparisons():
    f1 = Fraction(3, 4)
    f2 = Fraction(2, 3)
    f3 = Fraction(6, 8)

    assert f1.compare(f2) == 1   # 3/4 > 2/3
    assert f2.compare(f1) == -1  # 2/3 < 3/4
    assert f1.compare(f3) == 0   # 3/4 == 6/8


def test_fraction_addition_like_denominators():
    f1 = Fraction(1, 5)
    f2 = Fraction(2, 5)
    res = f1.add(f2)
    assert res.n == 3
    assert res.d == 5


def test_fraction_addition_unlike_denominators():
    f1 = Fraction(1, 3)
    f2 = Fraction(1, 6)
    res = f1.add(f2)
    assert res.n == 1
    assert res.d == 2  # 3/6 simplifies to 1/2


def test_fraction_subtraction_like_denominators():
    f1 = Fraction(4, 7)
    f2 = Fraction(1, 7)
    res = f1.subtract(f2)
    assert res.n == 3
    assert res.d == 7


def test_fraction_subtraction_unlike_denominators():
    f1 = Fraction(3, 4)
    f2 = Fraction(1, 2)
    res = f1.subtract(f2)
    assert res.n == 1
    assert res.d == 4


def test_zero_denominator_rejection():
    with pytest.raises(ValueError, match="Denominator cannot be zero"):
        Fraction(1, 0)


def test_negative_fraction_normalization():
    f1 = Fraction(3, -4)
    assert f1.n == -3
    assert f1.d == 4

    f2 = Fraction(-3, -4)
    assert f2.n == 3
    assert f2.d == 4


def test_negative_subtraction_result():
    f1 = Fraction(1, 4)
    f2 = Fraction(3, 4)
    res = f1.subtract(f2)
    assert res.n == -1
    assert res.d == 2  # -2/4 simplifies to -1/2


def test_question_generator_reproducibility():
    gen1 = MathQuestionGenerator(seed=12345)
    gen2 = MathQuestionGenerator(seed=12345)

    q1 = gen1.generate(level=1, index=1)
    q2 = gen2.generate(level=1, index=1)

    assert q1.id == q2.id
    assert q1.prompt == q2.prompt
    assert q1.options == q2.options
    assert q1.correct_index == q2.correct_index
    assert q1.explanation == q2.explanation


def test_question_options_validity():
    gen = MathQuestionGenerator(seed=999)
    for lvl in [1, 2, 3]:
        for idx in range(1, 6):
            q = gen.generate(level=lvl, index=idx)
            assert len(q.options) == 4
            assert 0 <= q.correct_index < 4
            correct_opt = q.options[q.correct_index]
            assert correct_opt in q.options
            # Plausible distractors should be distinct from correct answer
            assert len(set(q.options)) == 4
