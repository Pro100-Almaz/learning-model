"""Declarative (static_choice) topics must generate end-to-end without an LLM.

These topics have `options`-based parameters (rolled by random.choice, not
randint) and a conceptual answer spelled out under `answer.correct` — a set of
named properties rather than a computed number. This exercises the whole pure
pipeline: roll spec -> compute answer -> render constraints -> build solution ->
build options. trig_sin_properties is the reference blueprint.

Pure math -- no LLM, no DB.
"""

import random

from math_engine import (
    build_answer_options,
    build_solution,
    generate_math_spec,
    load_blueprint,
    render_constraints,
)
from math_ques_types import compute_answer_key


def test_static_choice_pipeline_runs_and_options_are_well_formed():
    blueprint = load_blueprint("trig_sin")
    correct = blueprint["answer"]["correct"]

    for seed in range(40):
        random.seed(seed)

        # 1) options-based params roll without KeyError (the sampler fix).
        spec = generate_math_spec(blueprint, difficulty=1)
        assert spec["point_x"] in blueprint["parameters"]["point_x"]["options"]

        # 2) the declarative answer comes back verbatim.
        answer = compute_answer_key(blueprint, spec)
        assert answer == correct

        # 3) constraints render, and the rolled control point appears in the text.
        text = render_constraints(blueprint, spec)
        assert "\\sin" in text

        # 4) the worked solution lists one step per property.
        solution = build_solution(blueprint, spec, answer)
        assert len(solution["steps"]) == len(correct)

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
        assert correct_text == ", ".join(f"{k} = {v}" for k, v in correct.items())

        # each wrong option flips exactly one property and is tagged with its id.
        for opt in options:
            if opt["is_correct"]:
                continue
            assert opt["misconception"] in {d["id"] for d in blueprint["distractors"]}


def test_wrong_options_differ_from_correct_in_one_field():
    """A static distractor overrides only its targeted property; the rest stay right."""
    blueprint = load_blueprint("trig_sin")
    correct = blueprint["answer"]["correct"]
    random.seed(0)
    spec = generate_math_spec(blueprint, difficulty=1)
    answer = compute_answer_key(blueprint, spec)

    options = build_answer_options(
        answer, blueprint["distractors"], spec, n_options=4, literal=True
    )
    by_tag = {o["misconception"]: o["text"] for o in options if not o["is_correct"]}

    # wrong_parity flips only 'четность' to 'четная'; range/period/domain unchanged.
    expected = dict(correct)
    expected["четность"] = "четная"
    assert by_tag["wrong_parity"] == ", ".join(f"{k} = {v}" for k, v in expected.items())
