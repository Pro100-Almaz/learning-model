"""The translation cache, and dressing one question in three languages.

The invariant under test is the one the whole design exists to guarantee:

    The Kazakh, Russian and English versions of an item are the SAME question.
    Same mode, same numbers, same answer, same five options, same order.

It holds because generation happens once and knows nothing about language, so
these tests check that localization only ever replaces words.
"""

from __future__ import annotations

import pytest

from ubt_question_engine import i18n
from ubt_question_engine.generator import generate_question
from ubt_question_engine.loader import list_topics, topic_meta
from ubt_question_engine.localize import (ENGINE_LANGUAGES, build_all_languages,
                                          build_question, localize, missing_for)

MATHEMATICAL_FIELDS = (
    "content_hash", "mode", "seed", "difficulty", "parameters", "derived",
    "answer_expression", "latex", "solution",
)


# --- the cache --------------------------------------------------------------


def test_the_translatable_surface_is_small_and_bounded():
    """179 instructions + 19 answer labels.

    This number is the entire justification for a cache instead of per-question
    translation. If it ever grows unbounded, the design has to change, so it is
    asserted rather than assumed.
    """
    surface = i18n.translatable()
    kinds = [meta["kind"] for meta in surface.values()]
    assert len(surface) == 198
    assert kinds.count(i18n.KIND_INSTRUCTION) == 179
    assert kinds.count(i18n.KIND_ANSWER_LABEL) == 19


def test_keys_are_stable_and_language_scoped():
    assert i18n.source_key("Simplify", "kk") == i18n.source_key("Simplify", "kk")
    assert i18n.source_key("Simplify", "kk") != i18n.source_key("Simplify", "ru")
    assert i18n.source_key("Simplify", "kk") != i18n.source_key("Factor", "kk")


def test_editing_a_source_string_invalidates_its_entry():
    """Keyed on a hash of the English, so reworded instructions cannot go stale."""
    assert i18n.source_key("Simplify", "kk") != i18n.source_key("Simplify.", "kk")


def test_english_needs_no_translations(empty_i18n):
    """Blueprints are authored in English, so it is the source language."""
    assert i18n.lookup("Simplify", "en") == "Simplify"
    assert i18n.translate("Simplify", "en") == "Simplify"
    assert i18n.missing("en") == {}
    done, total = i18n.coverage()["en"]
    assert done == total == 198


def test_a_missing_translation_raises_instead_of_falling_back(empty_i18n):
    """A silent English fallback looks deliberate, so nobody ever reports it."""
    with pytest.raises(i18n.MissingTranslation) as excinfo:
        i18n.translate("Simplify", "kk")
    assert "build_i18n" in str(excinfo.value)


def test_coverage_counts_what_is_actually_missing(empty_i18n):
    assert i18n.coverage()["kk"] == (0, 198)
    assert len(i18n.missing("kk")) == 198


# --- localization -----------------------------------------------------------


def test_english_works_with_an_empty_cache(empty_i18n):
    built = 0
    for topic in list_topics():
        for difficulty in topic_meta(topic)["supported_difficulties"]:
            state = build_question(topic, "en", difficulty=difficulty, seed=7)
            assert state["language"] == "en"
            assert state["text"].strip()
            built += 1
    assert built >= 100


def test_all_languages_pose_the_same_question(fake_i18n):
    """The invariant. Words differ; mathematics does not."""
    for topic in list_topics():
        for difficulty in topic_meta(topic)["supported_difficulties"]:
            versions = build_all_languages(topic, difficulty=difficulty, seed=13)
            assert set(versions) == set(ENGINE_LANGUAGES)

            reference = versions["en"]
            for language, state in versions.items():
                for field in MATHEMATICAL_FIELDS:
                    assert state[field] == reference[field], f"{topic}/{language}/{field}"

                # Option ORDER, not merely the set: a student comparing answers
                # with a classmate in another language must see B mean B.
                assert [c["is_correct"] for c in state["answer_options"]] == [
                    c["is_correct"] for c in reference["answer_options"]
                ], f"{topic}/{language}"
                assert [c["distractor_id"] for c in state["answer_options"]] == [
                    c["distractor_id"] for c in reference["answer_options"]
                ], f"{topic}/{language}"


def test_the_words_actually_change(fake_i18n):
    for topic in list_topics():
        versions = build_all_languages(topic, seed=21)
        assert versions["kk"]["text"] != versions["en"]["text"], topic
        assert versions["kk"]["instruction"] != versions["en"]["instruction"], topic


def test_answer_labels_are_translated_because_they_are_the_answer(fake_i18n):
    """On 3 topics the option text IS the answer ("parallel lines").

    Untranslated, a Kazakh student picks between five English phrases.
    """
    checked = 0
    for topic in ("ubt_spatial_line_plane_relations", "ubt_polyhedron_sections",
                  "ubt_sphere_plane_positions"):
        for difficulty in topic_meta(topic)["supported_difficulties"]:
            versions = build_all_languages(topic, difficulty=difficulty, seed=13)
            english = versions["en"]
            labels = set((english.get("choice_labels") or {}).values())
            options = {c["latex"] for c in english["answer_options"]}
            if not labels & options:
                # A mixed_by_mode topic also has numeric modes, whose options are
                # numbers and correctly stay identical across languages.
                continue
            checked += 1
            assert {c["latex"] for c in versions["kk"]["answer_options"]} != options
    assert checked >= 3


def test_localize_does_not_mutate_its_input(fake_i18n):
    """Callers render one question in three languages, in a loop."""
    base = generate_question("ubt_roots_expressions", difficulty=1, seed=41)
    before = dict(base)
    localize(base, "kk")
    localize(base, "ru")
    assert base["instruction"] == before["instruction"]
    assert "language" not in base or base.get("language") == before.get("language")


def test_the_solution_record_stays_canonical(fake_i18n):
    """It is the reproduction record; regenerate() compares against it.

    Translating it would make every stored question look like it had drifted.
    """
    base = generate_question("ubt_roots_expressions", difficulty=1, seed=41)
    kazakh = localize(base, "kk")
    assert kazakh["solution"] == base["solution"]
    assert kazakh["solution"]["answer_latex"] == base["answer_latex"]


def test_missing_for_reports_before_any_work_is_done(empty_i18n):
    base = generate_question("ubt_roots_expressions", difficulty=1, seed=41)
    assert missing_for(base, "kk") == [base["instruction"]]
    assert missing_for(base, "en") == []


def test_an_unsupported_language_is_refused(fake_i18n):
    base = generate_question("ubt_roots_expressions", seed=1)
    with pytest.raises(ValueError, match="Unsupported language"):
        localize(base, "de")


def test_rendered_text_contains_the_statement_and_every_option(fake_i18n):
    state = build_question("ubt_roots_expressions", "en", difficulty=1, seed=41)
    text = state["text"]
    assert state["instruction"] in text
    if state["latex"]:
        assert state["latex"] in text
    for choice in state["answer_options"]:
        assert choice["latex"] in text
    # The public statement must never mark which option is right.
    assert "correct" not in text.lower()
