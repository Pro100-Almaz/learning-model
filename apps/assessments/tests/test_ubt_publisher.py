"""Publishing UBT items into the question bank.

The unit of publication is an ITEM, not a row: one generation becomes one row per
language, written in a single transaction. These tests defend the two things that
makes true --

    * the three rows carry the SAME mathematics, joined by a language-free hash;
    * either all of them are written or none of them is.
"""

from __future__ import annotations

import pytest

from apps.assessments.models import AnswerOption, Question
from ubt_question_engine import i18n
from ubt_question_engine.generator import generate_question
from ubt_question_engine.localize import build_all_languages, localize
from ubt_question_engine.publisher import (PublishError, publish, publish_item,
                                           publishable_languages, row_content_hash,
                                           to_options)
from ubt_question_engine.testing import use_fake_translations, use_no_translations

pytestmark = pytest.mark.django_db

TOPIC = "ubt_roots_expressions"


@pytest.fixture
def fake_i18n():
    with use_fake_translations() as store:
        yield store


# --- hashes -----------------------------------------------------------------


def test_row_hash_differs_per_language_but_derives_from_one_math_hash():
    """Question.content_hash is UNIQUE, so three languages need three values."""
    math_hash = "a" * 64
    hashes = {row_content_hash(math_hash, lang) for lang in ("kk", "ru", "en")}
    assert len(hashes) == 3
    assert all(len(h) == 64 for h in hashes)
    # Stable across processes and runs.
    assert row_content_hash(math_hash, "kk") == row_content_hash(math_hash, "kk")


def test_options_carry_the_misconception_the_tutor_reads():
    state = generate_question(TOPIC, difficulty=1, seed=41)
    options = to_options(state["answer_options"])
    assert len(options) == 5
    assert sum(o["is_correct"] for o in options) == 1
    wrong = [o for o in options if not o["is_correct"]]
    assert all(o["misconception"] for o in wrong)
    assert all(o["text"] for o in options)


# --- publishing -------------------------------------------------------------


def test_publishing_english_needs_no_translations():
    with use_no_translations():
        assert publishable_languages() == ("en",)
        result = publish_item(TOPIC, difficulty=1, seed=41, languages=("en",))

    question = Question.objects.get(pk=result["en"]["question_id"])
    assert question.language == "en"
    assert question.text.strip()
    assert question.tags.exists(), "a question with no tag can never be reached"
    assert AnswerOption.objects.filter(question=question).count() == 5
    assert AnswerOption.objects.filter(question=question, is_correct=True).count() == 1


def test_five_options_are_accepted_even_though_the_bank_defaults_to_four():
    """A real ҰБТ paper offers A-E; MAIQE's four is only the default."""
    with use_no_translations():
        result = publish_item(TOPIC, difficulty=1, seed=7, languages=("en",))
    question_id = result["en"]["question_id"]
    assert AnswerOption.objects.filter(question_id=question_id).count() == 5


def test_all_languages_become_one_item(fake_i18n):
    states = build_all_languages(TOPIC, difficulty=1, seed=41)
    publish(states)

    rows = list(Question.objects.all())
    assert len(rows) == 3
    assert sorted(r.language for r in rows) == ["en", "kk", "ru"]

    # One mathematics, three rows: the language-free hash is the join key.
    assert len({r.solution["content_hash"] for r in rows}) == 1
    assert len({r.content_hash for r in rows}) == 3
    assert AnswerOption.objects.count() == 15


def test_the_join_query_a_serving_layer_would_run(fake_i18n):
    publish(build_all_languages(TOPIC, difficulty=1, seed=41))
    math_hash = Question.objects.first().solution["content_hash"]
    assert Question.objects.filter(solution__content_hash=math_hash).count() == 3


def test_the_solution_record_keeps_everything_needed_to_reproduce(fake_i18n):
    publish(build_all_languages(TOPIC, difficulty=1, seed=41))
    solution = Question.objects.filter(language="kk").first().solution
    for key in ("topic", "mode", "difficulty", "seed", "parameters", "derived",
                "answer_expression", "answer_latex", "content_hash"):
        assert key in solution, key
    assert solution["seed"] == 41


def test_republishing_the_same_seed_deduplicates():
    """A batch re-rolling a seed it already used is ordinary, not an error."""
    with use_no_translations():
        first = publish_item(TOPIC, difficulty=1, seed=41, languages=("en",))
        assert not first["en"]["was_duplicate"]
        before = Question.objects.count()

        second = publish_item(TOPIC, difficulty=1, seed=41, languages=("en",))
        assert second["en"]["was_duplicate"]
        assert second["en"]["question_id"] == first["en"]["question_id"]
    assert Question.objects.count() == before


# --- refusals ---------------------------------------------------------------


def test_a_failure_on_one_language_writes_nothing(fake_i18n):
    """All rows or none.

    A half-published item is worse than an unpublished one: the bank looks
    fuller than it is, and only a student sitting the exam in the missing
    language ever finds out.
    """
    states = build_all_languages(TOPIC, difficulty=1, seed=555)
    ordered = {lang: states[lang] for lang in ("kk", "ru", "en")}
    # Break the LAST language, so two rows are already written when it fails.
    ordered["en"]["answer_options"][1]["latex"] = ordered["en"]["answer_options"][0]["latex"]

    with pytest.raises(ValueError):
        publish(ordered)

    assert Question.objects.count() == 0
    assert AnswerOption.objects.count() == 0


def test_an_untranslated_item_never_reaches_the_database():
    """localize() raises before any DB call, so preflight never sees it."""
    with use_no_translations():
        with pytest.raises(i18n.MissingTranslation):
            build_all_languages(TOPIC, difficulty=1, seed=7)
    assert Question.objects.count() == 0


def test_versions_from_different_generations_are_refused(fake_i18n):
    """Publishing them together would file two questions as one item."""
    first = build_all_languages(TOPIC, difficulty=1, seed=1)
    second = build_all_languages(TOPIC, difficulty=1, seed=2)
    with pytest.raises(PublishError, match="disagree about the mathematics"):
        publish({"en": first["en"], "kk": second["kk"]})
    assert Question.objects.count() == 0


def test_a_state_filed_under_the_wrong_language_is_refused(fake_i18n):
    states = build_all_languages(TOPIC, difficulty=1, seed=1, languages=("en",))
    states["kk"] = dict(states["en"])       # English content, filed as Kazakh
    with pytest.raises(PublishError, match="says it is"):
        publish(states)
    assert Question.objects.count() == 0


def test_publishing_an_ungeneralized_state_is_refused(fake_i18n):
    """generate_question output has no `text`; only localize() adds it."""
    base = generate_question(TOPIC, difficulty=1, seed=1)
    with pytest.raises(PublishError, match="no text"):
        publish({"en": {**base, "language": "en"}})


def test_nothing_to_publish_is_refused():
    with pytest.raises(PublishError):
        publish({})


# --- the whole collection ---------------------------------------------------


def test_every_topic_publishes_in_every_language(fake_i18n):
    from ubt_question_engine.loader import list_topics, topic_meta

    published = 0
    for topic in list_topics():
        difficulty = topic_meta(topic)["default_difficulty"]
        publish_item(topic, difficulty=difficulty, seed=2026)
        published += 1

    assert Question.objects.count() == published * 3
    assert AnswerOption.objects.count() == published * 15
    assert Question.objects.filter(tags__isnull=True).count() == 0
    for language in ("kk", "ru", "en"):
        assert Question.objects.filter(language=language).count() == published
