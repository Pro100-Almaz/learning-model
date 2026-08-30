"""Generation: the seed contract and the invariants every item must satisfy.

The promise these tests defend, from architecture doc sections 14-15:

    (topic, difficulty, seed) fully determine the question, its answer, its five
    options, and the order they appear in.

Not "usually". Always -- which is what makes a stored question auditable, a bug
report reproducible, and regenerate() meaningful.
"""

from __future__ import annotations

import json
import re

import pytest

from ubt_question_engine.generator import (SEED_SPACE, content_hash,
                                           generate_question, regenerate)
from ubt_question_engine.loader import list_topics, topic_meta
from ubt_question_engine.math_functions import CHOICE_COUNT
from ubt_question_engine.params import GenerationError

from .conftest import SEEDS

# `zoo` and `nan` are SymPy's quiet answers to division by zero and friends. They
# do not raise; they render, and would reach a student as \tilde{\infty}.
UNDEFINED = re.compile(r"zoo|\bnan\b|tilde\\\{\\infty")
# A coefficient of 0 or 1 spliced into a template, which tidy() should have
# removed. 0 events across 4800 questions when this was written.
COSMETIC = re.compile(r"(?<![0-9a-zA-Z])1x|[+-]0(?![0-9])")


def _every_item():
    """Every topic, every supported difficulty, a few seeds each."""
    for topic in list_topics():
        for difficulty in topic_meta(topic)["supported_difficulties"]:
            for seed in SEEDS:
                yield topic, difficulty, seed


def test_every_topic_generates_at_every_supported_difficulty(topics):
    failures = []
    built = 0
    for topic, difficulty, seed in _every_item():
        try:
            generate_question(topic, difficulty=difficulty, seed=seed)
            built += 1
        except GenerationError as error:
            failures.append(f"{topic} d{difficulty} seed {seed}: {error}")
    assert failures == [], "\n".join(failures[:10])
    assert built == sum(
        len(topic_meta(t)["supported_difficulties"]) * len(SEEDS) for t in topics
    )


def test_structural_invariants_hold_for_every_item():
    """Five options, exactly one correct, all distinct, none undefined."""
    problems = []
    for topic, difficulty, seed in _every_item():
        state = generate_question(topic, difficulty=difficulty, seed=seed)
        where = f"{topic}/{state['mode']}/d{difficulty}/seed{seed}"
        choices = state["answer_options"]

        if len(choices) != CHOICE_COUNT:
            problems.append(f"{where}: {len(choices)} options")
        if sum(c["is_correct"] for c in choices) != 1:
            problems.append(f"{where}: not exactly one correct")
        rendered = [c["latex"] for c in choices]
        if len(set(rendered)) != len(rendered):
            problems.append(f"{where}: duplicate options {rendered}")
        if any(not c["latex"].strip() for c in choices):
            problems.append(f"{where}: an option rendered to nothing")
        for choice in choices:
            if not choice["is_correct"] and not choice["distractor_id"]:
                problems.append(f"{where}: wrong option carries no misconception")
        if not state["instruction"].strip():
            problems.append(f"{where}: no instruction")
        if not state["answer_latex"].strip():
            problems.append(f"{where}: no answer")
        if UNDEFINED.search(state["latex"]) or any(
            UNDEFINED.search(c["latex"]) for c in choices
        ):
            problems.append(f"{where}: undefined value reached the output")
        if COSMETIC.search(state["latex"]):
            problems.append(f"{where}: untidied latex {state['latex']!r}")

    assert problems == [], "\n".join(problems[:10])


def test_the_same_seed_always_gives_the_same_question(topics):
    """The core promise. Two calls, byte-identical results."""
    for topic in topics:
        difficulty = topic_meta(topic)["default_difficulty"]
        first = generate_question(topic, difficulty=difficulty, seed=4242)
        second = generate_question(topic, difficulty=difficulty, seed=4242)
        assert first == second, topic


def test_option_order_is_part_of_the_seed_contract(topics):
    """Shuffle order is reproducible too, not merely the set of options."""
    for topic in topics[:15]:
        difficulty = topic_meta(topic)["default_difficulty"]
        a = generate_question(topic, difficulty=difficulty, seed=8)
        b = generate_question(topic, difficulty=difficulty, seed=8)
        assert [c["latex"] for c in a["answer_options"]] == [
            c["latex"] for c in b["answer_options"]
        ], topic


def test_different_seeds_give_different_questions():
    """Otherwise the seed is decoration and the bank is one question deep."""
    hashes = {
        generate_question("ubt_roots_expressions", difficulty=1, seed=s)["content_hash"]
        for s in range(25)
    }
    assert len(hashes) > 15


def test_an_omitted_seed_is_drawn_and_recorded(topics):
    """A question generated from an unrecorded seed can never be reproduced."""
    state = generate_question("ubt_roots_expressions")
    assert isinstance(state["seed"], int)
    assert 0 <= state["seed"] < SEED_SPACE
    assert state["solution"]["seed"] == state["seed"]
    # ...and replaying the recorded seed reproduces the item exactly.
    assert generate_question(
        "ubt_roots_expressions",
        difficulty=state["difficulty"],
        seed=state["seed"],
    ) == state


def test_an_omitted_difficulty_uses_the_topic_default(topics):
    """Only 23 of 58 topics support all three levels; there is no global default."""
    for topic in topics:
        expected = topic_meta(topic)["default_difficulty"]
        assert generate_question(topic, seed=3)["difficulty"] == expected, topic


def test_an_unsupported_difficulty_is_refused_at_the_boundary(topics):
    """23 topics reject difficulty 1 and 15 reject difficulty 3.

    The error must name the topic and its legal values, not surface later as a
    complaint about the parameter schema.
    """
    checked = 0
    for topic in topics:
        supported = topic_meta(topic)["supported_difficulties"]
        for difficulty in (1, 2, 3):
            if difficulty in supported:
                continue
            checked += 1
            with pytest.raises(GenerationError) as excinfo:
                generate_question(topic, difficulty=difficulty, seed=1)
            assert "does not support difficulty" in str(excinfo.value)
            assert str(supported) in str(excinfo.value)
    assert checked > 30, "expected many topics to reject at least one difficulty"


def test_regenerate_rebuilds_a_stored_question(topics):
    """The point of recording the seed (doc section 15)."""
    for topic in topics:
        original = generate_question(topic, seed=99)
        assert regenerate(original["solution"]) == original, topic


def test_regenerate_detects_a_blueprint_that_changed_underneath():
    """A published question whose blueprint moved must not silently differ."""
    state = generate_question("ubt_roots_expressions", difficulty=1, seed=41)
    tampered = dict(state["solution"])
    tampered["answer_latex"] = "999999"
    with pytest.raises(GenerationError, match="changed after this question"):
        regenerate(tampered)


def test_content_hash_identifies_the_mathematics_not_the_presentation():
    a = generate_question("ubt_roots_expressions", difficulty=1, seed=41)
    b = generate_question("ubt_roots_expressions", difficulty=1, seed=41)
    assert a["content_hash"] == b["content_hash"]
    # Stable across processes: sha256 of the inputs, never Python's hash(),
    # which PYTHONHASHSEED randomises per process.
    assert a["content_hash"] == content_hash(
        a["topic"], a["mode"], a["difficulty"], a["parameters"]
    )
    assert len(a["content_hash"]) == 64


def test_solution_record_is_json_serialisable(topics):
    """It becomes Question.solution, a JSONField. No SymPy value survives that."""
    for topic in topics:
        state = generate_question(topic, seed=5)
        json.dumps(state["solution"], ensure_ascii=False)


def test_word_problem_modes_expose_their_values_as_text():
    """The 2 text_context modes must not make a reader dig values out of LaTeX."""
    found = False
    for seed in range(30):
        state = generate_question("ubt_progression_word_problems", seed=seed)
        if state.get("text_context"):
            found = True
            assert all(isinstance(v, str) for v in state["text_context"].values())
    assert found, "no text_context mode was drawn in 30 seeds"
