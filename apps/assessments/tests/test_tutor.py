"""Tests for the on-demand, review-only Tutor (Agent 5).

The LLM call (`llm.chat_anthropic`) is always mocked: these tests verify the
gating, the cache, the prompt assembly, and the graceful fallback — not the
model's prose.
"""

import pytest
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from agents_and_engine.prompts import (
    TUTOR_EXPLANATION_SYSTEM,
    TUTOR_RECAP_SYSTEM,
    TUTOR_SYSTEM,
)
from apps.assessments import services
from apps.assessments.models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestQuestion,
    TutorNote,
)
from apps.content.models import Tag
from apps.users.models import CustomUser

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def spy_anthropic(monkeypatch):
    """Replace the lazily-imported chat_anthropic with a call-recording fake."""
    calls = []

    def fake(system, user, *, model, **kwargs):
        calls.append({"system": system, "user": user, "model": model})
        return "  Белгіні шатастырып алдың.  "  # untrimmed on purpose

    # get_tutor_feedback does `from agents_and_engine.llm import chat_anthropic`
    # at call time, so patching the attribute on that module is what takes effect.
    monkeypatch.setattr("agents_and_engine.llm.chat_anthropic", fake)
    return calls


@pytest.fixture
def student():
    return CustomUser.objects.create_user(
        email="tutor-student@example.com", password="testpass123"
    )


@pytest.fixture
def tag():
    return Tag.objects.create(name="Quadratics", slug="quadratics")


def _question(tag, *, solution, wrong_misconception):
    q = Question.objects.create(
        text="Find the roots.",
        explanation="because",
        solution=solution,
    )
    q.tags.add(tag)
    correct = AnswerOption.objects.create(question=q, text="3, 4", is_correct=True)
    wrong = AnswerOption.objects.create(
        question=q, text="-3, -4", is_correct=False, misconception=wrong_misconception
    )
    return q, correct, wrong


@pytest.fixture
def setup(tag):
    """A one-question test with a richly-tagged wrong option."""
    solution = {
        "steps": [
            {"label": "Equation", "detail": "x^2 - 7x + 12 = 0"},
            {"label": "Roots", "detail": "x1 = 3, x2 = 4"},
        ],
        "answer_key": [3, 4],
        "misconceptions": {
            "sign_flip_both": "Reported both roots with the opposite sign.",
        },
    }
    q, correct, wrong = _question(tag, solution=solution, wrong_misconception="sign_flip_both")
    test = Test.objects.create(type="micro", title="Micro")
    TestQuestion.objects.create(test=test, question=q, order=1)
    return {"test": test, "q": q, "correct": correct, "wrong": wrong}


def _finished_attempt_with_wrong_answer(student, setup):
    attempt = services.start_attempt(student, setup["test"])
    services.record_answer(attempt, question_id=setup["q"].pk, option_id=setup["wrong"].pk)
    services.finish_attempt(attempt)
    attempt.refresh_from_db()
    return attempt


def _finished_attempt_with_skipped_question(student, setup):
    """Finish an attempt without ever answering the question (no AttemptAnswer row)."""
    attempt = services.start_attempt(student, setup["test"])
    services.finish_attempt(attempt)
    attempt.refresh_from_db()
    return attempt


# ---------------------------------------------------------------------------
# Happy path + prompt assembly
# ---------------------------------------------------------------------------


def test_returns_note_and_feeds_misconception_to_model(student, setup, spy_anthropic):
    attempt = _finished_attempt_with_wrong_answer(student, setup)

    note = services.get_tutor_feedback(attempt, setup["q"].pk)

    assert note == "Белгіні шатастырып алдың."  # trimmed by the service
    assert len(spy_anthropic) == 1
    # Diagnosis mode must use the non-revealing persona (not the explainer).
    assert spy_anthropic[0]["system"] == TUTOR_SYSTEM
    prompt = spy_anthropic[0]["user"]
    # The specific misconception description must reach the model...
    assert "Reported both roots with the opposite sign." in prompt
    # ...along with the worked solution and the student's wrong answer.
    assert "x1 = 3, x2 = 4" in prompt
    assert "-3, -4" in prompt


def test_caches_per_option_and_skips_second_llm_call(student, setup, spy_anthropic):
    attempt = _finished_attempt_with_wrong_answer(student, setup)

    first = services.get_tutor_feedback(attempt, setup["q"].pk)
    second = services.get_tutor_feedback(attempt, setup["q"].pk)

    assert first == second
    assert len(spy_anthropic) == 1  # second call served from the cache


def test_note_is_persisted_and_reused_across_calls(student, setup, spy_anthropic):
    from apps.assessments.models import TutorNote

    attempt = _finished_attempt_with_wrong_answer(student, setup)
    services.get_tutor_feedback(attempt, setup["q"].pk)

    # One durable row keyed by (question, option), holding the trimmed note.
    row = TutorNote.objects.get(question=setup["q"], selected_option=setup["wrong"])
    assert row.note == "Белгіні шатастырып алдың."

    # A later call with the LLM unavailable still returns — served from the DB.
    def boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("LLM should not be called on a persisted-note hit")

    monkey = pytest.MonkeyPatch()
    monkey.setattr("agents_and_engine.llm.chat_anthropic", boom)
    try:
        again = services.get_tutor_feedback(attempt, setup["q"].pk)
    finally:
        monkey.undo()
    assert again == "Белгіні шатастырып алдың."


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_refused_before_attempt_is_finished(student, setup, spy_anthropic):
    attempt = services.start_attempt(student, setup["test"])
    services.record_answer(attempt, question_id=setup["q"].pk, option_id=setup["wrong"].pk)

    with pytest.raises(ValidationError) as exc:
        services.get_tutor_feedback(attempt, setup["q"].pk)

    assert exc.value.detail["code"] == "attempt_not_finished"
    assert len(spy_anthropic) == 0


def test_recap_for_a_correct_answer(student, setup, spy_anthropic):
    """A correct answer gets a short method recap (in case it was a guess), cached."""
    attempt = services.start_attempt(student, setup["test"])
    services.record_answer(attempt, question_id=setup["q"].pk, option_id=setup["correct"].pk)
    services.finish_attempt(attempt)
    attempt.refresh_from_db()

    note = services.get_tutor_feedback(attempt, setup["q"].pk)

    assert note == "Белгіні шатастырып алдың."  # trimmed by the service
    assert len(spy_anthropic) == 1
    # Recap mode uses the confirm-the-method persona and feeds the model the
    # worked steps and the answer key (revealing it is fine — already correct).
    assert spy_anthropic[0]["system"] == TUTOR_RECAP_SYSTEM
    prompt = spy_anthropic[0]["user"]
    assert "x1 = 3, x2 = 4" in prompt
    assert "[3, 4]" in prompt
    # Cached on the (question, correct-option) row.
    assert TutorNote.objects.filter(
        question=setup["q"], selected_option=setup["correct"]
    ).exists()

    # A second request is served from cache — no second LLM call.
    again = services.get_tutor_feedback(attempt, setup["q"].pk)
    assert again == note
    assert len(spy_anthropic) == 1


# ---------------------------------------------------------------------------
# Explanation mode (skipped questions)
# ---------------------------------------------------------------------------


def test_explains_skipped_question(student, setup, spy_anthropic):
    """A question with no AttemptAnswer is explained, not refused."""
    attempt = _finished_attempt_with_skipped_question(student, setup)

    note = services.get_tutor_feedback(attempt, setup["q"].pk)

    assert note == "Белгіні шатастырып алдың."  # trimmed by the service
    assert len(spy_anthropic) == 1
    # Explanation mode must use the revealing persona...
    assert spy_anthropic[0]["system"] == TUTOR_EXPLANATION_SYSTEM
    prompt = spy_anthropic[0]["user"]
    # ...and feed the model the worked steps and the answer key to reveal.
    assert "x1 = 3, x2 = 4" in prompt
    assert "[3, 4]" in prompt


def test_explains_answered_without_option(student, setup, spy_anthropic):
    """A recorded answer with no option selected also gets an explanation."""
    attempt = services.start_attempt(student, setup["test"])
    AttemptAnswer.objects.create(
        attempt=attempt, question=setup["q"], selected_option=None, is_correct=False
    )
    services.finish_attempt(attempt)
    attempt.refresh_from_db()

    note = services.get_tutor_feedback(attempt, setup["q"].pk)

    assert note == "Белгіні шатастырып алдың."
    assert len(spy_anthropic) == 1
    assert spy_anthropic[0]["system"] == TUTOR_EXPLANATION_SYSTEM


def test_explanation_is_cached(student, setup, spy_anthropic):
    """The second call for the same skipped question is served from the null-option row."""
    attempt = _finished_attempt_with_skipped_question(student, setup)

    first = services.get_tutor_feedback(attempt, setup["q"].pk)
    second = services.get_tutor_feedback(attempt, setup["q"].pk)

    assert first == second
    assert len(spy_anthropic) == 1  # second call served from the cache


# ---------------------------------------------------------------------------
# Graceful fallback
# ---------------------------------------------------------------------------


def test_handles_missing_solution_and_untagged_option(student, tag, spy_anthropic):
    """Legacy/seeded questions have no solution and untagged distractors."""
    q, _correct, wrong = _question(tag, solution={}, wrong_misconception="")
    test = Test.objects.create(type="micro", title="Legacy")
    TestQuestion.objects.create(test=test, question=q, order=1)
    attempt = services.start_attempt(student, test)
    services.record_answer(attempt, question_id=q.pk, option_id=wrong.pk)
    services.finish_attempt(attempt)
    attempt.refresh_from_db()

    note = services.get_tutor_feedback(attempt, q.pk)  # must not raise

    assert note  # still produced something
    prompt = spy_anthropic[0]["user"]
    assert "infer" in prompt.lower()  # told the model to infer the error


# ---------------------------------------------------------------------------
# Endpoint wiring
# ---------------------------------------------------------------------------


def test_endpoint_returns_feedback(student, setup, spy_anthropic):
    from django.urls import reverse

    attempt = _finished_attempt_with_wrong_answer(student, setup)
    client = APIClient()
    client.force_authenticate(user=student)

    url = reverse("v1:attempts:attempt-tutor", kwargs={"id": attempt.pk})
    resp = client.post(url, {"question_id": setup["q"].pk}, format="json")

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["feedback"] == "Белгіні шатастырып алдың."
    # The response must not leak the misconception slug or the answer.
    assert "sign_flip_both" not in str(resp.data)
