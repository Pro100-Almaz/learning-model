"""The blueprint collection itself, and the loader that reads it.

The most valuable test in this directory is test_linter_reports_no_errors: it is
the gate that stops a newly authored blueprint from reaching production with an
unparseable expression, a distractor referencing an undeclared parameter, or a
pool too small to fill five options. Everything downstream assumes the collection
is well formed.
"""

from __future__ import annotations

import json

import pytest

from ubt_question_engine.lint_blueprints import lint_all
from ubt_question_engine.loader import (BLUEPRINT_ROOT, BlueprintError,
                                        list_topics, load_blueprint,
                                        modes_for_difficulty, topic_meta)


def test_collection_is_not_empty(topics):
    assert len(topics) >= 58


def test_linter_reports_no_errors():
    """A blueprint that fails this must never be published."""
    findings = lint_all()
    errors = [f for f in findings if f.level == "error"]
    assert errors == [], "\n".join(f"{f.topic}: {f.where}: {f.message}" for f in errors)


def test_every_topic_loads_and_has_usable_metadata(topics):
    for topic in topics:
        meta = topic_meta(topic)
        assert meta["topic"] == topic
        assert meta["display_name"]
        assert meta["tag_slug"], f"{topic} has no tag; publishing requires one"
        assert meta["supported_difficulties"], topic
        assert set(meta["supported_difficulties"]) <= {1, 2, 3}, topic
        assert meta["default_difficulty"] in meta["supported_difficulties"], topic
        assert meta["modes"], topic


def test_every_supported_difficulty_offers_at_least_one_mode(topics):
    for topic in topics:
        for difficulty in topic_meta(topic)["supported_difficulties"]:
            modes = modes_for_difficulty(topic, difficulty)
            assert modes, f"{topic} d{difficulty} allows no modes"
            assert set(modes) <= set(topic_meta(topic)["modes"]), topic


def test_load_blueprint_hands_out_independent_copies():
    """Callers merge difficulty overrides into the schema they are given.

    Sharing the cached dict would let the first caller of a topic corrupt every
    later one in the same process -- a bug that would only ever appear under a
    long-lived worker, which is the worst place to find it.
    """
    first = load_blueprint("ubt_roots_expressions")
    first["parameters"]["__probe__"] = "mutated"
    second = load_blueprint("ubt_roots_expressions")
    assert "__probe__" not in second["parameters"]


def test_unknown_topic_suggests_the_right_one():
    with pytest.raises(BlueprintError) as excinfo:
        load_blueprint("ubt_roots_expresions")   # one letter short
    assert "ubt_roots_expressions" in str(excinfo.value)


def test_topics_are_addressed_by_stem_not_by_path(topics):
    """One folder on disk is misspelled ("inequalitites").

    Addressing topics by filename stem is what keeps that typo out of every
    caller; this test fails if anyone reintroduces path-based lookup.
    """
    assert "ubt_quadratic_inequalities" in topics
    assert not any("/" in topic or "\\" in topic for topic in topics)


def test_blueprints_are_stored_canonically(topics):
    """Every file round-trips through json.dumps(indent=2, ensure_ascii=False).

    Keeps diffs of blueprint edits readable and keeps the Kazakh tag names as
    Kazakh rather than \\uXXXX escapes.
    """
    offenders = []
    for path in sorted(BLUEPRINT_ROOT.rglob("*.json")):
        raw = path.read_text(encoding="utf-8")
        canonical = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
        if raw.rstrip("\n") != canonical:
            offenders.append(path.name)
    assert offenders == [], f"not canonically formatted: {offenders}"
