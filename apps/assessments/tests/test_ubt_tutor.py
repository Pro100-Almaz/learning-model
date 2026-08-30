"""The Tutor on UBT questions.

The transport is stubbed throughout: CI must never make a paid model call, and
nothing here is trying to judge the quality of a Kazakh explanation. What IS
testable, and what these tests defend, is everything around the call --

    * a UBT question reaches the UBT builder and not MAIQE's, which would report
      its answer as "(unknown)" while solution["answer_latex"] holds it;
    * the misconception the bank recorded is handed to the model, so the
      diagnosis is ground truth rather than a guess;
    * one note per (question, option) is billed, ever.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.assessments import services
from apps.assessments.models import (AnswerOption, AttemptAnswer, Question, Test,
                                     TestAttempt, TestQuestion, TutorNote)
from ubt_question_engine import tutor as ubt_tutor
from ubt_question_engine.publisher import publish_item
from ubt_question_engine.testing import use_no_translations

pytestmark = pytest.mark.django_db

TOPIC = "ubt_roots_expressions"


@pytest.fixture
def calls(monkeypatch):
    """Record every model call instead of making one."""
    recorded: list[dict] = []

    def fake(system, user, *, model, temperature=0.7, max_tokens=300):
        recorded.append({"system": system, "user": user, "max_tokens": max_tokens})
        return f"note {len(recorded)}"

    monkeypatch.setattr(ubt_tutor, "chat_anthropic", fake)
    return recorded


@pytest.fixture
def question():
    with use_no_translations():
        result = publish_item(TOPIC, difficulty=1, seed=41, languages=("en",))
    return Question.objects.get(pk=result["en"]["question_id"])


@pytest.fixture
def attempt(question):
    user = get_user_model().objects.create(email="student@example.com")
    test = Test.objects.create(title="probe")
    TestQuestion.objects.create(test=test, question=question, order=0)
    return TestAttempt.objects.create(student=user, test=test, is_completed=True)


# --- routing ----------------------------------------------------------------


def test_owns_distinguishes_ubt_records_from_maiqe_ones(question):
    assert ubt_tutor.owns(question.solution)
    assert not ubt_tutor.owns({"steps": [], "misconceptions": {}, "answer_key": "3"})
    assert not ubt_tutor.owns({})
    assert not ubt_tutor.owns(None)


@pytest.mark.parametrize(
    "answered,is_correct,expected",
    [(False, False, "skipped"), (True, True, "correct"), (True, False, "wrong")],
)
def test_outcome_for(answered, is_correct, expected):
    assert ubt_tutor.outcome_for(answered=answered, is_correct=is_correct) == expected


# --- the prompt -------------------------------------------------------------


def test_the_correct_answer_reaches_the_model(question):
    """MAIQE's builder would have said "(unknown)" here."""
    request = ubt_tutor.build_request(
        solution=question.solution, text=question.text, language="en",
        outcome="skipped")
    rendered = ubt_tutor.render_request(request)
    assert question.solution["answer_latex"] in rendered
    assert "(unknown)" not in rendered


def test_the_mode_is_labelled_as_the_method_under_test(question):
    request = ubt_tutor.build_request(
        solution=question.solution, text=question.text, language="en",
        outcome="skipped")
    rendered = ubt_tutor.render_request(request)
    assert question.solution["mode"] in rendered
    assert "method" in rendered.lower()


def test_the_recorded_misconception_is_handed_over(question):
    wrong = AnswerOption.objects.filter(question=question, is_correct=False).first()
    request = ubt_tutor.build_request(
        solution=question.solution, text=question.text, language="en",
        outcome="wrong", student_answer_latex=wrong.text,
        chosen_distractor_id=wrong.misconception)
    rendered = ubt_tutor.render_request(request)
    assert wrong.misconception in rendered
    assert wrong.text in rendered


def test_a_missing_misconception_falls_back_to_inference(question):
    """7 blueprints once labelled distractors d1..d6, which name nothing."""
    request = ubt_tutor.build_request(
        solution=question.solution, text=question.text, language="en",
        outcome="wrong", student_answer_latex="99", chosen_distractor_id="")
    assert "infer the error" in ubt_tutor.render_request(request)


def test_the_student_saw_this_exact_text(question):
    """Read from the row, never regenerated -- regeneration invites drift."""
    request = ubt_tutor.build_request(
        solution=question.solution, text=question.text, language="en",
        outcome="skipped")
    assert question.text in ubt_tutor.render_request(request)


def test_a_correct_answer_gets_a_short_review(question, calls):
    """A right answer out of five can be a guess, but needs no walkthrough."""
    for outcome, expected in (("wrong", ubt_tutor.MAX_TOKENS_FULL),
                              ("skipped", ubt_tutor.MAX_TOKENS_FULL),
                              ("correct", ubt_tutor.MAX_TOKENS_RECAP)):
        request = ubt_tutor.build_request(
            solution=question.solution, text=question.text, language="en",
            outcome=outcome)
        ubt_tutor.explain(request)
        assert calls[-1]["max_tokens"] == expected


def test_a_renamed_topic_does_not_break_a_published_question(question):
    """Blueprints move; a review of an old row must still work."""
    solution = dict(question.solution)
    solution["topic"] = "ubt_topic_that_no_longer_exists"
    request = ubt_tutor.build_request(
        solution=solution, text=question.text, language="en", outcome="skipped")
    assert request["display_name"] == "ubt_topic_that_no_longer_exists"


# --- through the service ----------------------------------------------------


def _answer(attempt, question, option, is_correct):
    AttemptAnswer.objects.filter(attempt=attempt, question=question).delete()
    if option is not None or is_correct:
        AttemptAnswer.objects.create(attempt=attempt, question=question,
                                     selected_option=option, is_correct=is_correct)


@pytest.mark.parametrize("kind", ["wrong", "correct", "skipped"])
def test_every_outcome_routes_through_the_ubt_tutor(kind, question, attempt, calls):
    if kind == "wrong":
        option = AnswerOption.objects.filter(question=question, is_correct=False).first()
        _answer(attempt, question, option, False)
    elif kind == "correct":
        option = AnswerOption.objects.get(question=question, is_correct=True)
        _answer(attempt, question, option, True)
    else:
        AttemptAnswer.objects.filter(attempt=attempt, question=question).delete()

    note = services.get_tutor_feedback(attempt, question.pk)
    assert note
    assert len(calls) == 1
    assert f"OUTCOME: {kind}" in calls[0]["user"]


def test_a_note_is_generated_once_and_reused(question, attempt, calls):
    """The note depends on (question, option), never on the student.

    Every student who picks that option shares one row, and one bill.
    """
    option = AnswerOption.objects.filter(question=question, is_correct=False).first()
    _answer(attempt, question, option, False)

    first = services.get_tutor_feedback(attempt, question.pk)
    assert len(calls) == 1

    second = services.get_tutor_feedback(attempt, question.pk)
    assert second == first
    assert len(calls) == 1, "the model was called twice for the same option"
    assert TutorNote.objects.count() == 1


def test_the_skipped_note_lives_on_the_null_option_row(question, attempt, calls):
    AttemptAnswer.objects.filter(attempt=attempt, question=question).delete()
    services.get_tutor_feedback(attempt, question.pk)
    assert TutorNote.objects.filter(question=question,
                                    selected_option__isnull=True).count() == 1


def test_feedback_is_refused_before_the_attempt_is_finished(question, attempt, calls):
    """Correctness on a mock is withheld until the student finishes."""
    attempt.is_completed = False
    attempt.save()
    with pytest.raises(Exception):
        services.get_tutor_feedback(attempt, question.pk)
    assert calls == []
