"""Every Qadam blueprint must satisfy the complete deterministic Architect contract.

These tests deliberately stop before the Storyteller: an LLM can rephrase a valid
problem, but it cannot repair a malformed answer, option slate, or Jinja template.
"""

import json
import random
from collections import defaultdict

import pytest

from agents_and_engine import answer_modules
from agents_and_engine.math_engine import (
    BLUEPRINT_DIR,
    QADAM_BLUEPRINT_DIR,
    BlueprintSchemaError,
    available_topics,
    build_answer_options,
    format_answer,
    load_blueprint,
)
from agents_and_engine.nodes_self import architect_node

QADAM_TOPICS = sorted(path.stem for path in QADAM_BLUEPRINT_DIR.glob("*.json"))
TARGET_SCORE_BY_DIFFICULTY = {1: 0, 2: 18, 3: 28}
BUILTIN_ANSWER_TYPES = {
    "roots",
    "progression",
    "integral_definite",
    "static_choice",
}


def test_qadam_topics_are_unique_and_discoverable():
    original_topics = {path.stem for path in BLUEPRINT_DIR.glob("*.json")}

    assert QADAM_TOPICS
    assert not (original_topics & set(QADAM_TOPICS))
    assert set(QADAM_TOPICS) <= set(available_topics())


def test_blueprint_tag_names_and_slugs_have_one_canonical_mapping():
    """A unique Tag name and slug must never identify two different records."""
    slugs_by_name = defaultdict(set)
    names_by_slug = defaultdict(set)

    for directory in (BLUEPRINT_DIR, QADAM_BLUEPRINT_DIR):
        for path in directory.glob("*.json"):
            tag = json.loads(path.read_text("utf-8"))["tag"]
            slugs_by_name[tag["name"]].add(tag["slug"])
            names_by_slug[tag["slug"]].add(tag["name"])

    conflicting_names = {
        name: slugs for name, slugs in slugs_by_name.items() if len(slugs) > 1
    }
    conflicting_slugs = {
        slug: names for slug, names in names_by_slug.items() if len(names) > 1
    }
    assert not conflicting_names
    assert not conflicting_slugs


@pytest.mark.parametrize("topic", QADAM_TOPICS)
def test_qadam_blueprint_metadata_and_files_are_wired(topic):
    blueprint = load_blueprint(topic)

    assert blueprint["topic"] == topic
    assert blueprint["constraints_template"] == f"{topic}.j2"
    assert (QADAM_BLUEPRINT_DIR / f"{topic}.j2").is_file()
    assert blueprint["answer"]["type"] in (
        BUILTIN_ANSWER_TYPES | answer_modules.ANSWER_TYPES
    )
    assert len(blueprint.get("distractors", [])) >= 3


@pytest.mark.parametrize("topic", QADAM_TOPICS)
@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_qadam_architect_generates_publishable_payloads(topic, difficulty):
    blueprint = load_blueprint(topic)
    declared_misconceptions = {
        distractor["id"] for distractor in blueprint.get("distractors", [])
    }

    for seed in range(20):
        random.seed(f"{topic}:{difficulty}:{seed}")
        result = architect_node(
            {
                "topic": topic,
                "student_profile": {
                    "target_score": TARGET_SCORE_BY_DIFFICULTY[difficulty]
                },
                "language": "kk",
            }
        )

        assert result["difficulty"] == difficulty
        assert result["solution"]["answer_key"] == result["answer_key"]
        assert result["solution"]["steps"]
        assert result["content_hash"] and len(result["content_hash"]) == 64
        assert "{{" not in result["constraints_payload"]
        assert "{%" not in result["constraints_payload"]

        options = result["answer_options"]
        texts = [option["text"] for option in options]
        assert len(options) == 4
        assert len(set(texts)) == 4
        assert sum(option["is_correct"] for option in options) == 1
        assert all(isinstance(text, str) and text.strip() for text in texts)
        assert all("{{" not in text and "{%" not in text for text in texts)

        correct = next(option for option in options if option["is_correct"])
        assert correct["text"] == format_answer(result["answer_key"])
        assert {
            option["misconception"]
            for option in options
            if option["misconception"]
        } <= declared_misconceptions


def test_scalar_static_choice_qadam_wrapper_is_rendered():
    options = build_answer_options(
        "$3$",
        [
            {
                "id": "wrong_value",
                "transform": {"correct": "${{ value + 1 }}$"},
            }
        ],
        {"value": 3},
        n_options=2,
        literal=True,
    )

    by_correctness = {option["is_correct"]: option["text"] for option in options}
    assert by_correctness == {True: "$3$", False: "$4$"}


def test_invalid_scalar_static_choice_wrapper_fails_loudly():
    with pytest.raises(BlueprintSchemaError, match="must contain only"):
        build_answer_options(
            "$3$",
            [
                {
                    "id": "malformed",
                    "transform": {"unexpected": "$4$"},
                }
            ],
            n_options=2,
            literal=True,
        )
