"""
Exact symbolic engine for the inverse-trigonometry blueprint family.

Unlike the integer/`Fraction` answers the rest of `math_engine` computes, these
topics produce *symbolic* quantities — radian angles that are rational multiples
of pi (pi/3, 5pi/6, ...) and quadratic surds (sqrt(3)/2, sqrt(2)/2). Everything
here is exact, pure-Python arithmetic (stdlib `Fraction` + a tiny `Surd` helper),
so it carries the same math-integrity guarantee as `compute_answer_key`: the
correct answer is never delegated to an LLM and never drifts through float.

The input set is a small, closed table of standard angles, so we use lookup
tables rather than a general CAS. A value outside the table raises `KeyError`
loudly at generation time — a missing entry can never silently corrupt an answer.

Public API (consumed by math_ques_types.compute_answer_key, math_engine.build_solution,
and nodes_self.architect_node):

    ANSWER_TYPES                              -> frozenset of the 4 type strings
    compute_answer(answer_type, spec)         -> str  (LaTeX answer key)
    solution_steps(answer_type, spec)         -> list[{"label", "detail"}]
    build_options(answer_type, spec, n)       -> list[{"text","is_correct","misconception"}]
"""
from __future__ import annotations

import random
from fractions import Fraction
from typing import Any

# ---------------------------------------------------------------------------
# Standard-angle table: arc-function + table value -> angle, as a Fraction that
# is the MULTIPLE of pi (so pi/3 is stored as Fraction(1, 3), pi as Fraction(1)).
# Covers every (func, value) pair the four blueprints can roll once their
# constraints have excluded the non-table combinations (see the constraint fixes
# in blueprints/inv_trig_*.json). A KeyError here means a blueprint let through a
# value with no standard angle.
# ---------------------------------------------------------------------------
PI_ANGLE: dict[str, dict[str, Fraction]] = {
    "arcsin": {
        "-1": Fraction(-1, 2), "-sqrt(3)/2": Fraction(-1, 3),
        "-sqrt(2)/2": Fraction(-1, 4), "-1/2": Fraction(-1, 6), "0": Fraction(0),
        "1/2": Fraction(1, 6), "sqrt(2)/2": Fraction(1, 4),
        "sqrt(3)/2": Fraction(1, 3), "1": Fraction(1, 2),
    },
    "arccos": {
        "-1": Fraction(1), "-sqrt(3)/2": Fraction(5, 6),
        "-sqrt(2)/2": Fraction(3, 4), "-1/2": Fraction(2, 3), "0": Fraction(1, 2),
        "1/2": Fraction(1, 3), "sqrt(2)/2": Fraction(1, 4),
        "sqrt(3)/2": Fraction(1, 6), "1": Fraction(0),
    },
    "arctg": {
        "-1": Fraction(-1, 4), "0": Fraction(0), "1": Fraction(1, 4),
        "sqrt(3)": Fraction(1, 3),
    },
    "arcctg": {
        "-1": Fraction(3, 4), "0": Fraction(1, 2), "1": Fraction(1, 4),
        "sqrt(3)": Fraction(1, 6),
    },
}


# ---------------------------------------------------------------------------
# Surd: an exact real of the form  coeff * sqrt(rad), with rad a square-free
# positive int (rad == 1 means a plain rational). Just enough algebra for the
# double-angle / composition values, which all live in {0, +-1/2, +-sqrt(2)/2,
# +-sqrt(3)/2, +-1, ...}. We never need addition, so this stays tiny.
# ---------------------------------------------------------------------------
class Surd:
    __slots__ = ("coeff", "rad")

    def __init__(self, coeff: Fraction, rad: int = 1) -> None:
        if coeff == 0:
            self.coeff, self.rad = Fraction(0), 1
        else:
            self.coeff, self.rad = coeff, rad

    @staticmethod
    def from_value(s: str) -> "Surd":
        """Parse a blueprint table value string ('sqrt(3)/2', '-1/2', '1', '0')."""
        neg = s.startswith("-")
        body = s[1:] if neg else s
        if body == "0":
            return Surd(Fraction(0))
        if body == "sqrt(3)":
            coeff, rad = Fraction(1), 3
        elif body == "sqrt(2)/2":
            coeff, rad = Fraction(1, 2), 2
        elif body == "sqrt(3)/2":
            coeff, rad = Fraction(1, 2), 3
        else:  # a plain rational like '1', '1/2'
            coeff, rad = Fraction(body), 1
        return Surd(-coeff if neg else coeff, rad)

    def square(self) -> Fraction:
        """coeff^2 * rad  — always rational."""
        return self.coeff * self.coeff * self.rad

    def __mul__(self, other: "Surd") -> "Surd":
        k, m = _square_free(self.rad * other.rad)
        return Surd(self.coeff * other.coeff * k, m)

    def latex(self) -> str:
        return _render_surd(self.coeff, self.rad)


def _square_free(n: int) -> tuple[int, int]:
    """Factor n = k^2 * m with m square-free; return (k, m). (n > 0.)"""
    k, m, d = 1, n, 2
    while d * d <= m:
        while m % (d * d) == 0:
            m //= d * d
            k *= d
        d += 1
    return k, m


def _sqrt_fraction(fr: Fraction) -> Surd:
    """Exact sqrt of a non-negative rational, as a Surd.  sqrt(a/b)=sqrt(a*b)/b."""
    if fr < 0:
        raise ValueError(f"sqrt of negative rational {fr}")
    if fr == 0:
        return Surd(Fraction(0))
    k, m = _square_free(fr.numerator * fr.denominator)
    return Surd(Fraction(k, fr.denominator), m)


def _exact_sqrt(fr: Fraction) -> Fraction:
    """sqrt of a rational that MUST be a perfect square; raise otherwise.

    Used by the identity-logic topic, whose Pythagorean fractions are chosen so
    every result is rational. A non-square here means a bad blueprint value, so
    we fail loudly rather than emit an irrational 'rational' answer.
    """
    s = _sqrt_fraction(fr)
    if s.rad != 1:
        raise ValueError(f"{fr} is not a perfect square (got {s.latex()})")
    return s.coeff


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
def _render_surd(coeff: Fraction, rad: int) -> str:
    if coeff == 0:
        return "0"
    sign = "-" if coeff < 0 else ""
    c = abs(coeff)
    num, den = c.numerator, c.denominator
    if rad == 1:
        body = str(num) if den == 1 else f"\\frac{{{num}}}{{{den}}}"
        return sign + body
    root = f"\\sqrt{{{rad}}}"
    top = root if num == 1 else f"{num}{root}"
    body = top if den == 1 else f"\\frac{{{top}}}{{{den}}}"
    return sign + body


def render_angle(k: Fraction) -> str:
    """Render an angle given as the multiple k of pi:  Fraction(5,6) -> 5pi/6."""
    if k == 0:
        return "0"
    sign = "-" if k < 0 else ""
    k = abs(k)
    num, den = k.numerator, k.denominator
    top = "\\pi" if num == 1 else f"{num}\\pi"
    if den == 1:
        return f"{sign}{top}"
    return f"{sign}\\frac{{{top}}}{{{den}}}"


def render_rational(r: Fraction) -> str:
    return _render_surd(r, 1)


# ---------------------------------------------------------------------------
# Per-type computation. Each returns (answer_latex, kind, tagged_distractors):
#   kind                -> 'angle' | 'value' | 'rational' (picks the top-up pool)
#   tagged_distractors  -> list[(misconception_id, latex)] mirroring the
#                          blueprint's named misconceptions where computable.
# ---------------------------------------------------------------------------
def _angle(func: str, val: str) -> Fraction:
    return PI_ANGLE[func][val]


def _eval_arithmetic(spec: dict) -> tuple[str, str, list[tuple[str, str]]]:
    etype = spec["expression_type"]

    if etype == "linear_combination":
        a1, a2 = _angle(spec["func1"], spec["val1"]), _angle(spec["func2"], spec["val2"])
        c1, c2 = spec["coef1"], spec["coef2"]
        op = 1 if spec["op"] == "+" else -1
        correct = c1 * a1 + op * c2 * a2
        # Misconception: the minus between the terms is dropped (always added).
        wrong_sign = c1 * a1 + c2 * a2
        return (
            render_angle(correct), "angle",
            [("wrong_sign_in_sum", render_angle(wrong_sign))],
        )

    if etype == "direct_composition":
        # outer(arc(val1)) = val1 — the functions cancel, no angle is computed.
        val = Surd.from_value(spec["val1"])
        # Misconception: the student computes the inner angle instead of cancelling.
        angle = _angle(spec["func1"], spec["val1"])
        return (
            val.latex(), "value",
            [("unnecessary_calculation", render_angle(angle))],
        )

    # double_angle: outer matches func1 (sin/arcsin or cos/arccos; arctg excluded
    # by constraint to avoid the tg(pi/2) division by zero).
    v = Surd.from_value(spec["val1"])
    func1 = spec["func1"]
    if func1 == "arcsin":          # sin(2 arcsin v) = 2 v sqrt(1 - v^2)
        result = Surd(Fraction(2)) * v * _sqrt_fraction(Fraction(1) - v.square())
    else:                          # cos(2 arccos v) = 2 v^2 - 1
        result = Surd(Fraction(2) * v.square() - 1)
    # Misconception: student cancels as if it were a direct composition (ignores
    # the factor 2), answering val1.
    return (
        result.latex(), "value",
        [("mixed_functions_error", v.latex())],
    )


def _eval_table_lookup(spec: dict) -> tuple[str, str, list[tuple[str, str]]]:
    func, val = spec["func_type"], spec["value"]
    angle = _angle(func, val)
    distractors: list[tuple[str, str]] = [
        # Student writes the angle in degrees instead of radians (pi/6 -> 30).
        ("degrees_format", str(int(angle * 180))),
    ]
    if func in ("arcsin", "arccos"):
        # Sin/cos confusion swaps to the co-function: arcsin <-> arccos give
        # complementary angles, pi/2 - theta.
        distractors.append(("diagonal_confusion", render_angle(Fraction(1, 2) - angle)))
    return render_angle(angle), "angle", distractors


def _eval_neg_logic(spec: dict) -> tuple[str, str, list[tuple[str, str]]]:
    func, val = spec["func_type"], spec["value"]
    angle = _angle(func, val)
    pos_val = val[1:]                      # strip the leading minus
    pos_angle = _angle(func, pos_val)
    distractors: list[tuple[str, str]] = []
    if func in ("arccos", "arcctg"):
        # Treats the function as even, ignoring the minus: answers f(|x|).
        distractors.append(("ignore_minus_arccos", render_angle(pos_angle)))
        # Pulls the minus out front, forgetting the pi: answers -f(|x|).
        distractors.append(("extract_minus_arccos", render_angle(-pos_angle)))
    else:
        # Odd function: the natural slip is dropping the minus -> +f(|x|).
        distractors.append(("", render_angle(pos_angle)))
    return render_angle(angle), "angle", distractors


# (outer_func, inner_func) -> how to build the rational result from `value`.
def _eval_identity_logic(spec: dict) -> tuple[str, str, list[tuple[str, str]]]:
    outer, inner = spec["outer_func"], spec["inner_func"]
    v = Fraction(spec["value"])
    sin = _exact_sqrt(Fraction(1) - v * v)   # cos = value, so sin = sqrt(1 - v^2)

    if inner == "1/2 * arccos":
        # Half-angle: tg(a/2) = sqrt((1 - cos a)/(1 + cos a)), cos a = value.
        half = _exact_sqrt((1 - v) / (1 + v))
        result = half if outer == "tg" else 1 / half
        wrong_id_id = "wrong_identity"
        # Ignoring the 1/2 and applying the full-angle (arccos) formula instead.
        full = sin / v if outer == "tg" else v / sin
        tagged = [(wrong_id_id, render_rational(full))]
    elif inner == "arccos":
        result = sin / v if outer == "tg" else v / sin
        tagged = []
    else:  # arcsin: value = sin, so cos = sqrt(1 - v^2)
        result = v / sin if outer == "tg" else sin / v
        tagged = []

    # Common to all: sign slip on the root, and numerator/denominator swap.
    tagged.append(("wrong_root_sign", render_rational(-result)))
    if result != 0:
        tagged.append(("fraction_division_error", render_rational(1 / result)))
    return render_rational(result), "rational", tagged


_DISPATCH = {
    "inv_trig_eval": _eval_arithmetic,
    "inv_trig_table_lookup": _eval_table_lookup,
    "inv_trig_neg_logic": _eval_neg_logic,
    "inv_trig_identity_logic": _eval_identity_logic,
}
ANSWER_TYPES = frozenset(_DISPATCH)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_answer(answer_type: str, spec: dict) -> str:
    """The exact correct answer, as a LaTeX fragment (angle, surd, or rational)."""
    return _DISPATCH[answer_type](spec)[0]


def solution_steps(answer_type: str, spec: dict) -> list[dict[str, str]]:
    """A short, human-readable derivation for the Tutor's ground truth."""
    answer, _, _ = _DISPATCH[answer_type](spec)
    return [{"label": "Answer", "detail": f"${answer}$"}]


# Top-up pools per answer kind, used only to fill option slots that the named
# misconceptions did not (each spec triggers just one or two of them). These
# carry an empty misconception tag — the Tutor infers them.
_POOL_ANGLE = [Fraction(1, 6), Fraction(1, 4), Fraction(1, 3), Fraction(1, 2),
               Fraction(2, 3), Fraction(3, 4), Fraction(5, 6), Fraction(1),
               Fraction(-1, 6), Fraction(-1, 4), Fraction(-1, 3), Fraction(-1, 2)]
_POOL_VALUE = ["\\frac{1}{2}", "-\\frac{1}{2}", "\\frac{\\sqrt{2}}{2}",
               "\\frac{\\sqrt{3}}{2}", "-\\frac{\\sqrt{3}}{2}", "1", "-1", "0"]
_POOL_RATIONAL = ["\\frac{1}{2}", "\\frac{1}{3}", "\\frac{2}{3}", "\\frac{3}{4}",
                  "\\frac{4}{3}", "\\frac{3}{2}", "2", "3"]


def build_options(answer_type: str, spec: dict, n_options: int = 4) -> list[dict[str, Any]]:
    """One correct option plus distractors, each tagged with its misconception.

    Mirrors math_engine.build_answer_options' contract (same dict shape, dedup by
    rendered text, shuffled) but for symbolic answers: named misconceptions are
    computed exactly, then any remaining slots are topped up from a per-kind pool
    of plausible wrong answers (tagged with an empty misconception).
    """
    correct, kind, tagged = _DISPATCH[answer_type](spec)
    options = [{"text": correct, "is_correct": True, "misconception": ""}]
    seen = {correct}

    for mid, text in tagged:
        if len(options) >= n_options:
            break
        if text in seen:
            continue
        seen.add(text)
        options.append({"text": text, "is_correct": False, "misconception": mid})

    pool = {"angle": [render_angle(k) for k in _POOL_ANGLE],
            "value": _POOL_VALUE,
            "rational": _POOL_RATIONAL}[kind]
    for text in pool:
        if len(options) >= n_options:
            break
        if text in seen:
            continue
        seen.add(text)
        options.append({"text": text, "is_correct": False, "misconception": ""})

    random.shuffle(options)
    return options
