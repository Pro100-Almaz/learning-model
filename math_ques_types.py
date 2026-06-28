from __future__ import annotations
from fractions import Fraction
from typing import Any
from math_engine import _eval, render_value

def compute_answer_key(blueprint: dict, spec: dict) -> Any:
    """Compute the absolute correct answer, in pure Python, from the spec.

    Never delegated to an LLM — this is the math-integrity guarantee.
    """
    answer = blueprint["answer"]
    kind = answer["type"]

    if kind == "roots":
        # Quadratic: the roots are parameters we rolled directly.
        return sorted(spec[name] for name in answer["values"])

    if kind == "progression":
        # Arithmetic progression: evaluate the nth-term and sum formulas.
        return {
            "a_n": int(_eval(answer["nth_term"], spec)),
            "S_n": int(_eval(answer["sum_n"], spec)),
        }

    if kind == "integral_definite":
        # ∫(a x^2 + b x + c) dx = a/3 x^3 + b/2 x^2 + c x  ->  F(upper) - F(lower).
        # Computed with exact rationals (Fraction), NOT float + round(): float
        # arithmetic can drift on large x^3 and round() uses banker's rounding,
        # either of which would silently emit a wrong "correct" answer. We then
        # require an integer result, so a blueprint that forgets the a%3==0 /
        # b%2==0 constraints fails loudly here instead of corrupting the answer.
        a, b, c = spec["a"], spec["b"], spec["c"]
        f = lambda x: Fraction(a, 3) * x**3 + Fraction(b, 2) * x**2 + Fraction(c) * x
        result = f(spec["upper"]) - f(spec["lower"])
        if result.denominator != 1:
            raise ValueError(
                f"Definite integral is not an integer ({result}) for spec {spec}. "
                "Constrain the blueprint so the result stays whole "
                "(e.g. a % 3 == 0 and b % 2 == 0)."
            )
        return int(result)

    if kind == "static_choice":
        # Declarative/conceptual answer: the correct value(s) are spelled out
        # literally in the blueprint under `correct` (no computation). Used for
        # topics whose answer is a classification or a set of named properties
        # (e.g. parity / domain / range / period), not a single computed number.
        # May be a dict of named fields or a single string. Each value is
        # Jinja-rendered against the spec (so a parameter-dependent answer like
        # "{{ -denom_d }}/{{ denom_c }}" resolves), then the option machinery
        # varies it without int-coercion.
        correct = answer["correct"]
        if isinstance(correct, dict):
            return {key: render_value(val, spec) for key, val in correct.items()}
        return render_value(correct, spec)

    raise ValueError(f"Unknown answer type: {kind}")