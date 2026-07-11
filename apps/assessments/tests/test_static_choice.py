"""Declarative (static_choice) topics must generate end-to-end without an LLM.

These topics spell their answer out under `answer.correct` as a set of named
properties rather than a computed number. trig_sin (y = sin(kx)) is the
reference blueprint: its one parameter `freq` (k) is the numeric difficulty
lever, and the `период` property is a Jinja template that reduces 2*pi/k so a
harder roll (larger k) genuinely changes the answer. This exercises the whole
pure pipeline: roll spec -> compute answer -> render constraints -> build
solution -> build options.

Pure math -- no LLM, no DB.
"""

import random

from agents_and_engine.math_engine import (
    build_answer_options,
    build_solution,
    generate_math_spec,
    load_blueprint,
    render_constraints,
    render_value,
)
from agents_and_engine.math_ques_types import compute_answer_key


def test_static_choice_pipeline_runs_and_options_are_well_formed():
    blueprint = load_blueprint("trig_sin")

    for seed in range(40):
        random.seed(seed)

        # 1) the numeric freq (k) lever rolls inside its range; difficulty 1
        #    clamps it to k=1 via difficulty_overrides.
        spec = generate_math_spec(blueprint, difficulty=1)
        assert spec["freq"] == 1

        # 2) the declarative answer renders vs the rolled k (period = 2*pi/k,
        #    reduced; at k=1 that is 2*pi).
        answer = compute_answer_key(blueprint, spec)
        assert answer == {
            "область_определения": "(-inf, +inf)",
            "область_значений": "[-1, 1]",
            "четность": "нечетная",
            "период": "2*pi",
        }

        # 3) constraints render, and the sine function appears in the text.
        text = render_constraints(blueprint, spec)
        assert "\\sin" in text

        # 4) the worked solution lists one step per property.
        solution = build_solution(blueprint, spec, answer)
        assert len(solution["steps"]) == len(answer)

        # 5) options: literal transforms, exactly one correct, all distinct.
        options = build_answer_options(
            answer, blueprint["distractors"], spec, n_options=4, literal=True
        )
        texts = [o["text"] for o in options]
        assert sum(o["is_correct"] for o in options) == 1
        assert len(set(texts)) == len(texts)              # no duplicates
        # one option per declared distractor + the correct one.
        assert len(options) == len(blueprint["distractors"]) + 1

        # the correct option renders every property, in answer-key order.
        correct_text = next(o["text"] for o in options if o["is_correct"])
        assert correct_text == ", ".join(f"{k} = {v}" for k, v in answer.items())

        # each wrong option flips exactly one property and is tagged with its id.
        for opt in options:
            if opt["is_correct"]:
                continue
            assert opt["misconception"] in {d["id"] for d in blueprint["distractors"]}


def test_wrong_options_differ_from_correct_in_one_field():
    """A static distractor overrides only its targeted property; the rest stay right."""
    blueprint = load_blueprint("trig_sin")
    random.seed(0)
    spec = generate_math_spec(blueprint, difficulty=1)   # k=1 -> период = 2*pi
    answer = compute_answer_key(blueprint, spec)

    options = build_answer_options(
        answer, blueprint["distractors"], spec, n_options=4, literal=True
    )
    by_tag = {o["misconception"]: o["text"] for o in options if not o["is_correct"]}

    # wrong_parity flips only 'четность' to 'четная'; range/period/domain unchanged.
    expected_parity = dict(answer)
    expected_parity["четность"] = "четная"
    assert by_tag["wrong_parity"] == ", ".join(
        f"{k} = {v}" for k, v in expected_parity.items()
    )

    # wrong_period flips only 'период'; at k=1 the student who forgot to divide
    # by k (or confused it with tangent) lands on pi.
    expected_period = dict(answer)
    expected_period["период"] = "pi"
    assert by_tag["wrong_period"] == ", ".join(
        f"{k} = {v}" for k, v in expected_period.items()
    )


def test_period_scales_with_freq():
    """The период template reduces 2*pi/k, so the freq lever changes the answer."""
    blueprint = load_blueprint("trig_sin")
    template = blueprint["answer"]["correct"]["период"]

    # k -> reduced period 2*pi/k.
    expected = {1: "2*pi", 2: "pi", 3: "2*pi/3", 4: "pi/2"}
    for k, period in expected.items():
        assert render_value(template, {"freq": k}) == period
