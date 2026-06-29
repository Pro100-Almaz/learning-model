"""Integral questions must offer the intermediate step value as a distractor.

The pedagogical goal (the 3b1b / FTC case): a tempting wrong answer is the value
you get if you evaluate the antiderivative at the upper limit but forget to
subtract F(lower) -- e.g. the answer is 60 but F(upper) = 120, and BOTH must be
on the options list. This guards the blueprint's `lower >= 1` rule: at lower = 0,
F(0) = 0 so F(upper) would equal the answer and collapse out of the options.

Pure math -- no LLM, no DB.
"""

import random

from maiqe.math.engine import build_answer_options, generate_math_spec, load_blueprint
from maiqe.math.question_types import compute_answer_key


def _f_upper(spec: dict) -> int:
    """F(upper): the antiderivative at the upper limit (the intermediate step value)."""
    a, b, c, up = spec["a"], spec["b"], spec["c"], spec["upper"]
    return int(a / 3 * up**3 + b / 2 * up**2 + c * up)


def test_upper_limit_step_value_is_always_a_distinct_distractor():
    blueprint = load_blueprint("calculus_integrals")
    for seed in range(40):
        random.seed(seed)
        spec = generate_math_spec(blueprint, difficulty=2)
        answer = compute_answer_key(blueprint, spec)
        options = build_answer_options(answer, blueprint["distractors"], spec, n_options=4)

        texts = {o["text"] for o in options}
        f_upper = _f_upper(spec)

        # lower >= 1 keeps F(upper) != answer, so the step value stays a distractor.
        assert spec["lower"] >= 1, spec
        assert f_upper != answer, spec
        assert str(f_upper) in texts, (spec, texts)  # the "120" is offered
        assert str(answer) in texts                  # the "60" is offered

        # Bank invariants: exactly one correct, four distinct options.
        assert sum(o["is_correct"] for o in options) == 1
        assert len(texts) == 4
