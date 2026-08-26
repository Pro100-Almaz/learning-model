"""Filling the UBT bank off the request path.

The two properties that matter are the ones a synchronous publish gave away for
free and a fan-out can quietly lose:

    * an item's language rows are still written together, or not at all;
    * the whole batch is still reproducible from ONE seed, even though the work
      is split across workers that never talk to each other.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import translation

from apps.assessments import services as assessment_services
from apps.assessments.models import AnswerOption, Question, Test, TestQuestion
from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag
from apps.generation import ubt_tasks
from apps.users.models import CustomUser
from ubt_question_engine.testing import use_fake_translations, use_no_translations

pytestmark = pytest.mark.django_db

TOPIC = "ubt_triangle_properties"


def _run(topic=TOPIC, **kwargs):
    with use_no_translations():
        return ubt_tasks.generate_ubt_topic.run(topic, languages=["en"], **kwargs)


# --- one topic --------------------------------------------------------------


def test_a_topic_task_publishes_and_reports_what_it_did():
    summary = _run(count=3, seed=500)

    assert summary["topic"] == TOPIC
    assert summary["requested"] == 3
    assert summary["published"] == 3
    assert summary["duplicates"] == 0
    assert summary["failed"] == 0
    assert summary["seed"] == 500
    assert Question.objects.filter(solution__topic=TOPIC).count() == 3


def test_every_language_row_of_an_item_is_written():
    """The invariant publish() guarantees, asserted through the task."""
    with use_fake_translations():
        summary = ubt_tasks.generate_ubt_topic.run(
            TOPIC, count=1, seed=501, languages=["kk", "ru", "en"]
        )

    assert summary["published"] == 1
    rows = Question.objects.filter(solution__topic=TOPIC)
    assert rows.count() == 3
    assert {row.language for row in rows} == {"kk", "ru", "en"}
    # One mathematics, three dresses.
    assert len({row.solution["content_hash"] for row in rows}) == 1


def test_rerunning_the_same_seed_reports_duplicates_not_failures():
    """Re-rolling a seed already in the bank is ordinary, not an error."""
    first = _run(count=2, seed=502)
    second = _run(count=2, seed=502)

    assert first["published"] == 2 and first["duplicates"] == 0
    assert second["published"] == 0 and second["duplicates"] == 2
    assert second["failed"] == 0
    assert Question.objects.filter(solution__topic=TOPIC).count() == 2


def test_one_bad_item_does_not_lose_the_others():
    """A blueprint that raises on some rolls must not sink the whole task."""
    real = ubt_tasks.generate_question
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ubt_tasks.GenerationError("contrived bad roll")
        return real(*args, **kwargs)

    with mock.patch.object(ubt_tasks, "generate_question", side_effect=flaky):
        summary = _run(count=3, seed=503)

    assert summary["published"] == 2
    assert summary["failed"] == 1
    assert "contrived bad roll" in summary["failures"][0]
    assert Question.objects.filter(solution__topic=TOPIC).count() == 2


def test_seeds_come_from_the_base_seed_so_a_batch_is_reproducible():
    summary = _run(count=2, seed=504)
    seeds = {
        row.solution["seed"] for row in Question.objects.filter(solution__topic=TOPIC)
    }
    # build_plan: base + index*1000 + difficulty. Whatever the scheme, the point
    # is that the items are a function of the ONE number the caller was handed.
    assert summary["seed"] == 504
    assert seeds == {
        seed
        for _, _, seed in ubt_tasks.build_plan({"topic": TOPIC, "count": 2, "seed": 504})[
            0
        ]
    }


# --- the fan-out ------------------------------------------------------------


def test_the_batch_queues_one_task_per_topic():
    with (
        use_no_translations(),
        mock.patch.object(ubt_tasks.generate_ubt_topic, "delay") as delay,
    ):
        delay.return_value = mock.Mock(id="task-id")
        summary = ubt_tasks.generate_ubt_batch.run(count=2, languages=["en"])

    assert summary["queued"] == len(summary["topics"]) > 1
    assert delay.call_count == summary["queued"]
    assert Question.objects.count() == 0, "the batch queues, it does not publish"


def test_every_child_gets_the_same_seed_and_language_set():
    """Otherwise the base seed in the response reproduces nothing.

    A child drawing its own seed, or resolving languages while build_i18n
    finishes underneath it, would make the batch unreproducible and could give
    two topics different language sets in one run.
    """
    with (
        use_no_translations(),
        mock.patch.object(ubt_tasks.generate_ubt_topic, "delay") as delay,
    ):
        delay.return_value = mock.Mock(id="task-id")
        summary = ubt_tasks.generate_ubt_batch.run(seed=777, languages=["en"])

    seeds = {call.kwargs["seed"] for call in delay.call_args_list}
    languages = {tuple(call.kwargs["languages"]) for call in delay.call_args_list}
    assert seeds == {777} == {summary["seed"]}
    assert languages == {("en",)}


def test_the_batch_can_be_narrowed_to_one_topic():
    with (
        use_no_translations(),
        mock.patch.object(ubt_tasks.generate_ubt_topic, "delay") as delay,
    ):
        delay.return_value = mock.Mock(id="task-id")
        summary = ubt_tasks.generate_ubt_batch.run(topic=TOPIC, languages=["en"])

    assert summary["topics"] == [TOPIC]
    assert delay.call_count == 1


def test_an_unknown_topic_is_refused_before_anything_is_queued():
    from rest_framework.exceptions import NotFound

    with (
        use_no_translations(),
        mock.patch.object(ubt_tasks.generate_ubt_topic, "delay") as delay,
    ):
        with pytest.raises(NotFound):
            ubt_tasks.generate_ubt_batch.run(topic="ubt_no_such_topic", languages=["en"])

    assert delay.call_count == 0


# --- scheduled bank depth --------------------------------------------------


def _bank_row(topic: str, item_hash: str, language: str) -> Question:
    return Question.objects.create(
        text=f"{language} {item_hash}",
        explanation="because",
        language=language,
        solution={"topic": topic, "content_hash": item_hash},
    )


def test_top_up_does_not_queue_a_topic_already_at_target():
    for language in ("kk", "ru", "en"):
        _bank_row(TOPIC, "same-item", language)

    with (
        use_no_translations(),
        mock.patch.object(ubt_tasks, "resolve_topics", return_value=[TOPIC]),
        mock.patch.object(ubt_tasks.generate_ubt_topic, "delay") as delay,
    ):
        summary = ubt_tasks.top_up_ubt_bank.run(target=1, languages=["en"])

    assert summary["queued"] == 0
    assert summary["topics"] == []
    delay.assert_not_called()


def test_top_up_queues_exactly_the_item_shortfall():
    _bank_row(TOPIC, "item-one", "en")

    with (
        use_no_translations(),
        mock.patch.object(ubt_tasks, "resolve_topics", return_value=[TOPIC]),
        mock.patch.object(ubt_tasks.generate_ubt_topic, "delay") as delay,
    ):
        delay.return_value = mock.Mock(id="top-up-id")
        summary = ubt_tasks.top_up_ubt_bank.run(target=4, languages=["en"])

    delay.assert_called_once_with(
        TOPIC,
        count=3,
        languages=["en"],
        seed=summary["seed"],
    )
    assert summary["queued"] == 1


def test_top_up_depth_counts_items_not_language_rows():
    for language in ("kk", "ru", "en"):
        _bank_row(TOPIC, "same-item", language)

    with (
        use_no_translations(),
        mock.patch.object(ubt_tasks, "resolve_topics", return_value=[TOPIC]),
        mock.patch.object(ubt_tasks.generate_ubt_topic, "delay") as delay,
    ):
        delay.return_value = mock.Mock(id="top-up-id")
        ubt_tasks.top_up_ubt_bank.run(target=2, languages=["en"])

    assert delay.call_args.kwargs["count"] == 1


def test_top_up_children_share_one_base_seed_and_language_set():
    topics = [TOPIC, "ubt_roots_expressions"]
    with (
        use_no_translations(),
        mock.patch.object(ubt_tasks, "resolve_topics", return_value=topics),
        mock.patch.object(ubt_tasks.random, "randrange", return_value=888),
        mock.patch.object(ubt_tasks.generate_ubt_topic, "delay") as delay,
    ):
        delay.return_value = mock.Mock(id="top-up-id")
        summary = ubt_tasks.top_up_ubt_bank.run(target=1, languages=["en"])

    assert delay.call_count == 2
    assert {call.kwargs["seed"] for call in delay.call_args_list} == {888}
    assert {tuple(call.kwargs["languages"]) for call in delay.call_args_list} == {("en",)}
    assert summary["seed"] == 888
    assert summary["topics"] == topics


# --- request-path top-up ---------------------------------------------------


def _thin_micro_test(topic: str = TOPIC) -> tuple[CustomUser, Test]:
    subject = Subject.objects.create(name="Top-up math", slug="top-up-math")
    grade = ClassGrade.objects.create(grade=11, subject=subject)
    module = Module.objects.create(
        title="Top-up module",
        slug="top-up-module",
        class_grade=grade,
    )
    tag = Tag.objects.create(name="Top-up tag", slug="top-up-tag")
    lesson = Lesson.objects.create(
        module=module,
        tag=tag,
        topic=topic,
        title="Thin lesson",
        video_url="https://example.com/video",
    )
    test = Test.objects.create(type="micro", title="Thin micro", lesson=lesson)
    question = _bank_row(topic, "thin-item", "en")
    correct = AnswerOption.objects.create(
        question=question,
        text="correct",
        is_correct=True,
    )
    AnswerOption.objects.create(question=question, text="wrong", is_correct=False)
    TestQuestion.objects.create(test=test, question=question, order=0)
    user = CustomUser.objects.create_user(
        email="thin-bank@example.com",
        password="testpass123",
    )
    assert correct.pk
    return user, test


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_starting_thin_attempt_enqueues_once_and_debounces_the_second():
    user, test = _thin_micro_test()
    cache.clear()

    with (
        translation.override("en"),
        mock.patch.object(ubt_tasks.generate_ubt_topic, "delay") as delay,
    ):
        assessment_services.start_attempt(user, test)
        assessment_services.start_attempt(user, test)

    delay.assert_called_once_with(TOPIC, count=2)


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_broker_failure_does_not_fail_starting_an_attempt():
    user, test = _thin_micro_test()
    cache.clear()

    with (
        translation.override("en"),
        mock.patch.object(
            ubt_tasks.generate_ubt_topic,
            "delay",
            side_effect=RuntimeError("broker unavailable"),
        ),
    ):
        attempt = assessment_services.start_attempt(user, test)

    assert attempt.pk is not None
    assert test.attempts.filter(pk=attempt.pk).exists()
    assert cache.get(f"ubt-bank-top-up:{TOPIC}") is None
