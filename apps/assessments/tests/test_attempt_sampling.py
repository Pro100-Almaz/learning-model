"""Persisted question selection for bounded micro-test attempts."""

from __future__ import annotations

from unittest import mock

import pytest
from django.test import override_settings
from django.utils import translation
from rest_framework.exceptions import NotFound

from apps.assessments import services
from apps.assessments.models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestAttempt,
    TestQuestion,
)
from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag
from apps.users.models import CustomUser

pytestmark = pytest.mark.django_db


@pytest.fixture
def student():
    return CustomUser.objects.create_user(
        email="sampling@example.com",
        password="testpass123",
    )


@pytest.fixture
def lesson():
    subject = Subject.objects.create(name="Sampling math", slug="sampling-math")
    grade = ClassGrade.objects.create(grade=11, subject=subject)
    module = Module.objects.create(
        title="Sampling",
        slug="sampling",
        class_grade=grade,
    )
    tag = Tag.objects.create(name="Sampling", slug="sampling")
    return Lesson.objects.create(
        module=module,
        tag=tag,
        topic="",
        title="Sampling lesson",
        video_url="https://example.com/video",
    )


def _add_question(test: Test, *, language: str, index: int) -> Question:
    question = Question.objects.create(
        text=f"{language} question {index}",
        explanation="because",
        language=language,
        solution={
            "topic": "ubt_triangle_properties",
            "content_hash": f"item-{index}",
        },
    )
    AnswerOption.objects.create(
        question=question,
        text="correct",
        is_correct=True,
    )
    AnswerOption.objects.create(
        question=question,
        text="wrong",
        is_correct=False,
    )
    TestQuestion.objects.create(
        test=test,
        question=question,
        order=test.testquestion_set.count(),
    )
    return question


def _make_test(lesson: Lesson, *, test_type: str = "micro") -> Test:
    return Test.objects.create(type=test_type, title="Sample", lesson=lesson)


def _question_ids(payload: dict) -> list[int]:
    return [question.pk for question in payload["questions"]]


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_two_attempts_on_the_same_test_get_different_question_sets(student, lesson):
    test = _make_test(lesson)
    for index in range(6):
        _add_question(test, language="en", index=index)

    shuffle_calls = 0

    def alternate_shuffle(values):
        nonlocal shuffle_calls
        shuffle_calls += 1
        if shuffle_calls == 2:
            values.reverse()

    with (
        translation.override("en"),
        mock.patch.object(services.random, "shuffle", side_effect=alternate_shuffle),
    ):
        first = services.start_attempt(student, test)
        second = services.start_attempt(student, test)

    assert len(first.question_ids) == len(second.question_ids) == 3
    assert first.question_ids != second.question_ids


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_attempt_serves_exactly_the_configured_sample_size(student, lesson):
    test = _make_test(lesson)
    for index in range(7):
        _add_question(test, language="en", index=index)

    with translation.override("en"):
        attempt = services.start_attempt(student, test)
        payload = services.build_attempt_start_payload(attempt)

    assert len(attempt.question_ids) == 3
    assert _question_ids(payload) == attempt.question_ids


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_start_review_and_result_use_the_same_persisted_set(student, lesson):
    test = _make_test(lesson)
    for index in range(6):
        _add_question(test, language="en", index=index)

    with translation.override("en"):
        attempt = services.start_attempt(student, test)
        start = services.build_attempt_start_payload(attempt)
    with translation.override("ru"):
        review = services.build_attempt_review_payload(attempt)
        result = services.build_attempt_result_payload(attempt)

    start_ids = _question_ids(start)
    review_ids = [item["question_id"] for item in review["items"]]
    assert start_ids == review_ids == attempt.question_ids
    assert result["total_count"] == len(start_ids) == 3


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_score_denominator_is_sampled_items_not_all_language_rows(student, lesson):
    test = _make_test(lesson)
    for index in range(4):
        for language in ("kk", "ru", "en"):
            _add_question(test, language=language, index=index)

    with translation.override("en"):
        attempt = services.start_attempt(student, test)
        for position, question_id in enumerate(attempt.question_ids):
            option = Question.objects.get(pk=question_id).options.get(
                is_correct=position < 2
            )
            services.record_answer(attempt, question_id, option.pk)
        services.finish_attempt(attempt)
        result = services.build_attempt_result_payload(attempt)

    assert result["correct_count"] == 2
    assert result["total_count"] == 3
    assert result["score"] == 66.7


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_question_outside_sample_is_refused(student, lesson):
    test = _make_test(lesson)
    questions = [_add_question(test, language="en", index=index) for index in range(6)]

    with translation.override("en"):
        attempt = services.start_attempt(student, test)
        outside = next(q for q in questions if q.pk not in attempt.question_ids)
        option = outside.options.get(is_correct=True)
        with pytest.raises(NotFound) as error:
            services.record_answer(attempt, outside.pk, option.pk)

    assert str(error.value.detail["code"]) == "question_not_in_test"
    assert attempt.answers.count() == 0


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_unseen_questions_are_selected_before_answered_ones(student, lesson):
    test = _make_test(lesson)
    questions = [_add_question(test, language="en", index=index) for index in range(5)]
    previous = TestAttempt.objects.create(student=student, test=test)
    for question in questions[:2]:
        AttemptAnswer.objects.create(
            attempt=previous,
            question=question,
            selected_option=question.options.get(is_correct=True),
            is_correct=True,
        )

    with translation.override("en"):
        attempt = services.start_attempt(student, test)

    assert set(attempt.question_ids) == {question.pk for question in questions[2:]}


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_empty_question_ids_preserve_whole_test_locale_behavior(student, lesson):
    test = _make_test(lesson)
    en_questions = [_add_question(test, language="en", index=index) for index in range(2)]
    _add_question(test, language="ru", index=0)
    attempt = TestAttempt.objects.create(student=student, test=test, question_ids=[])

    with translation.override("en"):
        payload = services.build_attempt_start_payload(attempt)
        result = services.build_attempt_result_payload(attempt)

    assert _question_ids(payload) == [question.pk for question in en_questions]
    assert result["total_count"] == 2


@override_settings(MICRO_TEST_QUESTION_COUNT=3)
def test_mock_test_is_not_sampled(student, lesson):
    test = _make_test(lesson, test_type="mock")
    questions = [_add_question(test, language="en", index=index) for index in range(6)]

    with translation.override("en"):
        attempt = services.start_attempt(student, test)
        payload = services.build_attempt_start_payload(attempt)

    assert attempt.question_ids == []
    assert _question_ids(payload) == [question.pk for question in questions]
