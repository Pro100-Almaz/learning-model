"""Every converted static_choice blueprint must generate end-to-end.

Smoke-tests the whole pure pipeline (roll -> answer -> render -> solution ->
options) over many seeds for each declarative topic. Catches: sampler crashes,
unrendered Jinja left in an answer ("{{" leaking into option text), a transform
that collapses onto the correct answer, and the basic bank invariants.

Pure math -- no LLM, no DB.
"""

import random

import pytest

from maiqe.math.engine import build_answer_options, build_solution, generate_math_spec, load_blueprint
from maiqe.math.question_types import compute_answer_key

STATIC_TOPICS = [
    "trig_sin",
    "trig_cos",
    "trig_tg_ctg",
    "symmetry_periodicity",
    "shifts_xy",
    "deformations_xy",
    "function_analysis",
    "quadratic_analysis",
    "domain_extremums",
    "fractional_linear",
    "trig_eq_sin",
    "trig_eq_cos",
    "trig_eq_deg_red",
    "trig_eq_deg_sum",
    "trig_sys_sub",
    "trig_sys_add",
    "trig_eq_homog",
    "trig_eq_aux_angle",
    "inverse_fractional",
]

N_OPTIONS = 4


@pytest.mark.parametrize("topic", STATIC_TOPICS)
def test_static_choice_blueprint_generates(topic):
    blueprint = load_blueprint(topic)
    assert blueprint["answer"]["type"] == "static_choice", topic
    assert blueprint["topic"] == topic  # filename == topic (the #1 alignment)

    # A topic that declares >= N_OPTIONS-1 distractors must always fill the slate;
    # one with fewer yields (distractors + correct). Either way: never below 2.
    expected = min(N_OPTIONS, len(blueprint["distractors"]) + 1)

    for seed in range(60):
        random.seed(seed)
        spec = generate_math_spec(blueprint, blueprint.get("default_difficulty", 1))
        answer = compute_answer_key(blueprint, spec)

        # Answer (dict of properties OR a scalar solution string) — no Jinja markers
        # and no leaked skip-sentinel must survive into the rendered answer.
        values = list(answer.values()) if isinstance(answer, dict) else [answer]
        for value in values:
            assert "{{" not in str(value) and "{%" not in str(value), (topic, spec, answer)

        # Worked solution: one step per property (dict) or a single step (scalar).
        solution = build_solution(blueprint, spec, answer)
        assert len(solution["steps"]) == (len(answer) if isinstance(answer, dict) else 1)

        options = build_answer_options(
            answer, blueprint["distractors"], spec, n_options=N_OPTIONS, literal=True
        )
        texts = [o["text"] for o in options]

        # Bank invariants: exactly one correct; all options distinct; no markers or
        # skip-sentinel ("1/0") leaked; the slate is filled to the expected size.
        assert sum(o["is_correct"] for o in options) == 1, (topic, seed)
        assert len(set(texts)) == len(texts), (topic, seed, texts)
        for t in texts:
            assert "{{" not in t and "{%" not in t and "1/0" not in t, (topic, seed, t)
        assert len(options) == expected, (topic, seed, texts)
