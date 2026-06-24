"""publish_generated_question must link questions into the content graph.

A generated question is useless if it just sits in the bank: students reach it
only through a Test, and the roadmap finds it only through its Lesson. These
tests check that publishing sets Question.lesson and joins the lesson's micro
Test (creating it if needed), and degrades gracefully when no lesson teaches the
tag yet.
"""

import pytest

from apps.assessments.models import Question, Test, TestQuestion
from apps.assessments.services import publish_generated_question
from apps.content.models import Lesson, Module, Tag

pytestmark = pytest.mark.django_db


def _options():
    return [
        {"text": "60", "is_correct": True, "misconception": ""},
        {"text": "120", "is_correct": False, "misconception": "upper_limit_only"},
        {"text": "55", "is_correct": False, "misconception": "dropped_constant"},
        {"text": "10", "is_correct": False, "misconception": ""},
    ]


def _publish(content_hash, *, tag_slug="integraly"):
    return publish_generated_question(
        text="Найдите площадь под кривой $f(x)=...$.",
        explanation="",
        difficulty=2,
        solution={"answer_key": 60},
        options=_options(),
        tag_slug=tag_slug,
        tag_name="Интегралы",
        content_hash=content_hash,
    )


def _integrals_lesson():
    module = Module.objects.create(title="Calculus", slug="calc", subject="profile_math")
    tag = Tag.objects.create(name="Интегралы", slug="integraly")
    lesson = Lesson.objects.create(
        module=module, title="Интегралы: основы", video_url="https://x", tag=tag, order=1
    )
    return lesson


def test_question_is_linked_to_lesson_and_micro_test():
    lesson = _integrals_lesson()

    res = _publish("a" * 64)
    question = Question.objects.get(pk=res["question_id"])

    assert question.lesson_id == lesson.pk
    micro = Test.objects.get(lesson=lesson, type="micro")
    assert res["test_id"] == micro.pk
    assert TestQuestion.objects.filter(test=micro, question=question).exists()


def test_two_questions_share_one_micro_test_with_distinct_order():
    lesson = _integrals_lesson()

    first = _publish("a" * 64)
    second = _publish("b" * 64)

    # One micro test, both questions, orders 0 and 1.
    assert first["test_id"] == second["test_id"]
    assert Test.objects.filter(lesson=lesson, type="micro").count() == 1
    orders = sorted(
        TestQuestion.objects.filter(test_id=first["test_id"]).values_list("order", flat=True)
    )
    assert orders == [0, 1]


def test_no_lesson_for_tag_stores_unlinked():
    # No Lesson teaches this tag -> question is stored but not linked anywhere.
    res = _publish("c" * 64, tag_slug="topic-with-no-lesson")
    question = Question.objects.get(pk=res["question_id"])

    assert question.lesson_id is None
    assert res["test_id"] is None
    assert res["was_duplicate"] is False


def test_duplicate_does_not_relink_or_duplicate():
    _integrals_lesson()
    first = _publish("a" * 64)
    second = _publish("a" * 64)  # same hash

    assert second["was_duplicate"] is True
    assert second["question_id"] == first["question_id"]
    assert Question.objects.count() == 1
    # The micro test still has exactly the one question (no double-add).
    assert TestQuestion.objects.filter(test_id=first["test_id"]).count() == 1
