"""Tests for the attempt lifecycle endpoints."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.assessments.models import (
    AnswerOption,
    Question,
    Test,
    TestAttempt,
    TestQuestion,
)
from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag
from apps.users.models import CustomUser


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def student():
    return CustomUser.objects.create_user(
        email="student@example.com", password="testpass123"
    )


@pytest.fixture
def other_student():
    return CustomUser.objects.create_user(
        email="other@example.com", password="testpass123"
    )


@pytest.fixture
def tag():
    return Tag.objects.create(name="Algebra", slug="algebra")


def _build_question(tag, text="Q?"):
    q = Question.objects.create(text=text, explanation="because")
    q.tags.add(tag)
    correct = AnswerOption.objects.create(question=q, text="A", is_correct=True)
    wrong = AnswerOption.objects.create(question=q, text="B", is_correct=False)
    return q, correct, wrong


@pytest.fixture
def lesson(tag):
    subject, _ = Subject.objects.get_or_create(
        slug="profile_math", defaults={"name": "Profile Math"}
    )
    grade, _ = ClassGrade.objects.get_or_create(grade=11, subject=subject)
    module = Module.objects.create(title="Algebra", slug="algebra-mod", class_grade=grade)
    return Lesson.objects.create(
        module=module, tag=tag, title="Algebra: basics", video_url="https://x", order=1
    )


@pytest.fixture
def micro_test(tag, lesson):
    test = Test.objects.create(type="micro", title="Micro 1", lesson=lesson)
    q1, c1, w1 = _build_question(tag, "1+1?")
    q2, c2, w2 = _build_question(tag, "2+2?")
    TestQuestion.objects.create(test=test, question=q1, order=1)
    TestQuestion.objects.create(test=test, question=q2, order=2)
    return {
        "test": test,
        "lesson": lesson,
        "questions": [q1, q2],
        "correct_options": [c1, c2],
        "wrong_options": [w1, w2],
    }


@pytest.fixture
def mock_test(tag):
    test = Test.objects.create(type="mock", title="Mock 1", time_limit_sec=3600)
    q1, c1, w1 = _build_question(tag, "Mock Q1")
    q2, c2, w2 = _build_question(tag, "Mock Q2")
    TestQuestion.objects.create(test=test, question=q1, order=1)
    TestQuestion.objects.create(test=test, question=q2, order=2)
    return {
        "test": test,
        "questions": [q1, q2],
        "correct_options": [c1, c2],
        "wrong_options": [w1, w2],
    }


@pytest.fixture
def auth_client(student):
    client = APIClient()
    client.force_authenticate(user=student)
    return client


# ---------------------------------------------------------------------------
# /tests/{id}/
# ---------------------------------------------------------------------------


def test_test_detail_returns_metadata_without_questions(auth_client, micro_test):
    url = reverse("v1:tests:test-detail", kwargs={"id": micro_test["test"].pk})
    resp = auth_client.get(url)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert set(data.keys()) == {"id", "type", "title", "time_limit_sec", "question_count"}
    assert data["question_count"] == 2
    assert data["type"] == "micro"


# ---------------------------------------------------------------------------
# /attempts/
# ---------------------------------------------------------------------------


def test_start_attempt_returns_questions_without_correct_flags(auth_client, micro_test):
    url = reverse("v1:attempts:attempt-create")
    resp = auth_client.post(url, {"lesson_id": micro_test["lesson"].pk}, format="json")
    assert resp.status_code == status.HTTP_201_CREATED
    body = resp.json()
    assert "attempt_id" in body
    assert body["test"]["id"] == micro_test["test"].pk
    assert len(body["questions"]) == 2
    for q in body["questions"]:
        assert set(q.keys()) == {"id", "text", "image", "options"}
        for opt in q["options"]:
            assert set(opt.keys()) == {"id", "text"}
            assert "is_correct" not in opt


# ---------------------------------------------------------------------------
# /attempts/{id}/answer/
# ---------------------------------------------------------------------------


def test_micro_answer_returns_is_correct_immediately(auth_client, student, micro_test):
    attempt = TestAttempt.objects.create(student=student, test=micro_test["test"])
    url = reverse("v1:attempts:attempt-answer", kwargs={"id": attempt.pk})

    # Correct answer
    resp = auth_client.post(
        url,
        {
            "question_id": micro_test["questions"][0].pk,
            "option_id": micro_test["correct_options"][0].pk,
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["is_correct"] is True

    # Wrong answer
    resp = auth_client.post(
        url,
        {
            "question_id": micro_test["questions"][1].pk,
            "option_id": micro_test["wrong_options"][1].pk,
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["is_correct"] is False


def test_mock_answer_withholds_is_correct(auth_client, student, mock_test):
    attempt = TestAttempt.objects.create(student=student, test=mock_test["test"])
    url = reverse("v1:attempts:attempt-answer", kwargs={"id": attempt.pk})

    resp = auth_client.post(
        url,
        {
            "question_id": mock_test["questions"][0].pk,
            "option_id": mock_test["correct_options"][0].pk,
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["is_correct"] is None
    # Truth is still stored in the DB.
    answer = attempt.answers.get(question=mock_test["questions"][0])
    assert answer.is_correct is True


# ---------------------------------------------------------------------------
# /attempts/{id}/finish/
# ---------------------------------------------------------------------------


def test_finish_computes_score(auth_client, student, micro_test):
    attempt = TestAttempt.objects.create(student=student, test=micro_test["test"])
    answer_url = reverse("v1:attempts:attempt-answer", kwargs={"id": attempt.pk})
    # one right, one wrong → 50.0
    auth_client.post(
        answer_url,
        {
            "question_id": micro_test["questions"][0].pk,
            "option_id": micro_test["correct_options"][0].pk,
        },
        format="json",
    )
    auth_client.post(
        answer_url,
        {
            "question_id": micro_test["questions"][1].pk,
            "option_id": micro_test["wrong_options"][1].pk,
        },
        format="json",
    )

    finish_url = reverse("v1:attempts:attempt-finish", kwargs={"id": attempt.pk})
    resp = auth_client.post(finish_url)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["correct_count"] == 1
    assert body["total_count"] == 2
    assert body["score"] == 50.0
    assert body["finished_at"] is not None

    attempt.refresh_from_db()
    assert attempt.is_completed is True


# ---------------------------------------------------------------------------
# Mock timeout auto-finish (T-204)
# ---------------------------------------------------------------------------


def test_mock_timeout_auto_finishes(auth_client, student, mock_test):
    attempt = TestAttempt.objects.create(student=student, test=mock_test["test"])
    # Backdate started_at so we're past the time limit.
    attempt.started_at = timezone.now() - timedelta(
        seconds=mock_test["test"].time_limit_sec + 60
    )
    attempt.save(update_fields=["started_at"])

    url = reverse("v1:attempts:attempt-answer", kwargs={"id": attempt.pk})
    resp = auth_client.post(
        url,
        {
            "question_id": mock_test["questions"][0].pk,
            "option_id": mock_test["correct_options"][0].pk,
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_409_CONFLICT
    attempt.refresh_from_db()
    assert attempt.is_completed is True
    assert attempt.finished_at is not None


# ---------------------------------------------------------------------------
# /attempts/{id}/review/
# ---------------------------------------------------------------------------


def test_review_returns_correct_option_and_explanations(
    auth_client, student, micro_test
):
    attempt = TestAttempt.objects.create(student=student, test=micro_test["test"])
    answer_url = reverse("v1:attempts:attempt-answer", kwargs={"id": attempt.pk})
    auth_client.post(
        answer_url,
        {
            "question_id": micro_test["questions"][0].pk,
            "option_id": micro_test["wrong_options"][0].pk,
        },
        format="json",
    )
    finish_url = reverse("v1:attempts:attempt-finish", kwargs={"id": attempt.pk})
    auth_client.post(finish_url)

    review_url = reverse("v1:attempts:attempt-review", kwargs={"id": attempt.pk})
    resp = auth_client.get(review_url)
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["attempt_id"] == attempt.pk
    assert len(body["items"]) == 2
    first = body["items"][0]
    assert first["correct_option_id"] == micro_test["correct_options"][0].pk
    assert first["selected_option_id"] == micro_test["wrong_options"][0].pk
    assert first["is_correct"] is False
    assert first["explanation"] == "because"
    # Options on review include is_correct
    for opt in first["options"]:
        assert set(opt.keys()) == {"id", "text", "is_correct"}


def test_review_denied_for_non_owner(other_student, student, micro_test):
    attempt = TestAttempt.objects.create(student=student, test=micro_test["test"])
    client = APIClient()
    client.force_authenticate(user=other_student)

    url = reverse("v1:attempts:attempt-review", kwargs={"id": attempt.pk})
    resp = client.get(url)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_review_requires_auth(micro_test, student):
    attempt = TestAttempt.objects.create(student=student, test=micro_test["test"])
    client = APIClient()
    url = reverse("v1:attempts:attempt-review", kwargs={"id": attempt.pk})
    resp = client.get(url)
    assert resp.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )
