"""Cross-engine consistency tests verifying parity between fraction_engine.js and fraction_math.py.

Runs offline using the host's existing node.exe runtime without third-party dependencies.
"""

import json
import subprocess
import pytest

pytestmark = pytest.mark.host_integration


def _get_py_fraction():
    """Lazy import of target Python fraction module, executed only when authorized."""
    from space_fractions.fraction_math import Fraction, MathQuestionGenerator
    return Fraction, MathQuestionGenerator


def run_node_eval(js_code: str):
    """Execute small JavaScript snippet in node and return stdout."""
    res = subprocess.run(
        ["node", "-e", js_code],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return res.stdout.strip()


def test_js_py_simplification_parity():
    PyFraction, _ = _get_py_fraction()
    test_cases = [(6, 8), (15, 25), (42, 56), (7, 7), (10, 2), (-4, 8), (3, -9)]
    js_snippet = """
    const { Fraction } = require('./space_fractions/fraction_engine.js');
    const cases = %s;
    const results = cases.map(([n, d]) => {
        const f = new Fraction(n, d).simplify();
        return `${f.n}/${f.d}`;
    });
    console.log(JSON.stringify(results));
    """ % json.dumps(test_cases)

    js_output = json.loads(run_node_eval(js_snippet))

    for idx, (n, d) in enumerate(test_cases):
        py_f = PyFraction(n, d).simplify()
        py_str = f"{py_f.n}/{py_f.d}"
        assert js_output[idx] == py_str


def test_js_py_addition_parity():
    PyFraction, _ = _get_py_fraction()
    pairs = [
        ((1, 4), (2, 4)),
        ((1, 3), (1, 6)),
        ((2, 5), (3, 7)),
        ((5, 12), (7, 18)),
    ]
    js_snippet = """
    const { Fraction } = require('./space_fractions/fraction_engine.js');
    const pairs = %s;
    const results = pairs.map(([f1, f2]) => {
        const res = new Fraction(f1[0], f1[1]).add(new Fraction(f2[0], f2[1]));
        return res.toString();
    });
    console.log(JSON.stringify(results));
    """ % json.dumps(pairs)

    js_output = json.loads(run_node_eval(js_snippet))

    for idx, (f1, f2) in enumerate(pairs):
        py_res = PyFraction(f1[0], f1[1]).add(PyFraction(f2[0], f2[1]))
        assert js_output[idx] == str(py_res)


def test_js_py_subtraction_parity():
    PyFraction, _ = _get_py_fraction()
    pairs = [
        ((3, 4), (1, 4)),
        ((5, 6), (1, 3)),
        ((1, 3), (1, 2)),
        ((7, 10), (2, 5)),
    ]
    js_snippet = """
    const { Fraction } = require('./space_fractions/fraction_engine.js');
    const pairs = %s;
    const results = pairs.map(([f1, f2]) => {
        const res = new Fraction(f1[0], f1[1]).subtract(new Fraction(f2[0], f2[1]));
        return res.toString();
    });
    console.log(JSON.stringify(results));
    """ % json.dumps(pairs)

    js_output = json.loads(run_node_eval(js_snippet))

    for idx, (f1, f2) in enumerate(pairs):
        py_res = PyFraction(f1[0], f1[1]).subtract(PyFraction(f2[0], f2[1]))
        assert js_output[idx] == str(py_res)


def test_js_py_comparison_parity():
    PyFraction, _ = _get_py_fraction()
    pairs = [
        ((2, 3), (3, 5)),
        ((1, 4), (2, 8)),
        ((3, 7), (4, 7)),
        ((-1, 2), (1, 4)),
    ]
    js_snippet = """
    const { Fraction } = require('./space_fractions/fraction_engine.js');
    const pairs = %s;
    const results = pairs.map(([f1, f2]) => {
        return new Fraction(f1[0], f1[1]).compare(new Fraction(f2[0], f2[1]));
    });
    console.log(JSON.stringify(results));
    """ % json.dumps(pairs)

    js_output = json.loads(run_node_eval(js_snippet))

    for idx, (f1, f2) in enumerate(pairs):
        py_cmp = PyFraction(f1[0], f1[1]).compare(PyFraction(f2[0], f2[1]))
        assert js_output[idx] == py_cmp


def test_js_zero_denominator_rejection():
    js_snippet = """
    const { Fraction } = require('./space_fractions/fraction_engine.js');
    try {
        new Fraction(5, 0);
        console.log("NO_ERROR");
    } catch (e) {
        console.log("THREW_ERROR:" + e.message);
    }
    """
    out = run_node_eval(js_snippet)
    assert "THREW_ERROR:Denominator cannot be zero." in out


def test_js_question_generation_options():
    js_snippet = """
    const { QuestionGenerator } = require('./space_fractions/fraction_engine.js');
    const gen = new QuestionGenerator(42);
    const results = [];
    for (let lvl = 1; lvl <= 3; lvl++) {
        for (let idx = 1; idx <= 5; idx++) {
            const q = gen.generate(lvl, idx);
            results.push({
                id: q.id,
                level: q.level,
                optionsCount: q.options.length,
                uniqueOptionsCount: (new Set(q.options)).size,
                correctIndex: q.correctIndex,
                correctInOptions: q.options[q.correctIndex] !== undefined
            });
        }
    }
    console.log(JSON.stringify(results));
    """
    res = json.loads(run_node_eval(js_snippet))
    assert len(res) == 15
    for item in res:
        assert item["optionsCount"] == 4
        assert item["uniqueOptionsCount"] == 4
        assert 0 <= item["correctIndex"] < 4
        assert item["correctInOptions"] is True
