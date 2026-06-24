"""Business logic for the assessments app.

Views in this app are thin wrappers around these functions. Anything that
touches the database, awards XP, or decides what to return belongs here so
tests can exercise it directly without HTTP plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from .models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestAttempt,
)


@dataclass
class AnswerResult:
    """Return shape from record_answer.

    is_correct is the real correctness stored in the DB. The view layer is
    responsible for withholding it on mock tests.
    """

    is_correct: bool
    xp_awarded: int


# ---------------------------------------------------------------------------
# Attempt lifecycle
# ---------------------------------------------------------------------------


def start_attempt(user, test: Test) -> TestAttempt:
    """Create a fresh attempt for the student on a given test."""
    return TestAttempt.objects.create(student=user, test=test)


def _award_correct_answer_xp(user) -> int:
    """Award XP for a correct answer via the gamification service.

    Imported lazily to avoid a circular import at module load time and so
    tests that don't care about XP don't pay for the import.
    """
    try:
        from apps.gamification.services import award_xp
    except Exception:  # pragma: no cover - defensive
        return 0
    try:
        return int(award_xp(user, "correct_answer") or 0)
    except Exception:  # pragma: no cover - gamification not yet wired
        return 0


@transaction.atomic
def record_answer(
    attempt: TestAttempt, question_id: int, option_id: int
) -> AnswerResult:
    """Record (or replace) the student's answer for one question.

    For mock tests the view will withhold is_correct; the DB still stores the
    truth so /finish/ and /review/ work.
    """
    if attempt.is_completed:
        raise ValidationError({"detail": "attempt already finished", "code": "attempt_finished"})

    # Ensure the question belongs to this test.
    try:
        question = Question.objects.get(pk=question_id, tests=attempt.test)
    except Question.DoesNotExist as exc:
        raise NotFound({"detail": "question not in test", "code": "question_not_in_test"}) from exc

    try:
        option = AnswerOption.objects.get(pk=option_id, question=question)
    except AnswerOption.DoesNotExist as exc:
        raise NotFound({"detail": "option not in question", "code": "option_not_in_question"}) from exc

    is_correct = bool(option.is_correct)

    answer, created = AttemptAnswer.objects.update_or_create(
        attempt=attempt,
        question=question,
        defaults={"selected_option": option, "is_correct": is_correct},
    )

    xp_awarded = 0
    # Only reward the first time the student gets a question right within an
    # attempt — flipping answers shouldn't farm XP.
    if is_correct and created:
        xp_awarded = _award_correct_answer_xp(attempt.student)

    return AnswerResult(is_correct=is_correct, xp_awarded=xp_awarded)


@transaction.atomic
def finish_attempt(attempt: TestAttempt) -> TestAttempt:
    """Mark the attempt finished and compute its score (0–100)."""
    if attempt.is_completed:
        return attempt

    total_count = attempt.test.questions.count()
    correct_count = attempt.answers.filter(is_correct=True).count()
    if total_count > 0:
        score = round((correct_count / total_count) * 100, 1)
    else:
        score = 0.0

    attempt.score = score
    attempt.is_completed = True
    attempt.finished_at = timezone.now()
    attempt.save(update_fields=["score", "is_completed", "finished_at"])
    return attempt


def enforce_mock_timeout(attempt: TestAttempt) -> bool:
    """Auto-finish a mock attempt that has exceeded its time_limit_sec.

    Returns True if the attempt was auto-finished by this call. Safe to call
    on any attempt: it no-ops for completed attempts and tests without a
    time limit (i.e. micro tests).
    """
    if attempt.is_completed:
        return False
    time_limit = getattr(attempt.test, "time_limit_sec", None)
    if not time_limit:
        return False
    elapsed = (timezone.now() - attempt.started_at).total_seconds()
    if elapsed > time_limit:
        finish_attempt(attempt)
        return True
    return False


# ---------------------------------------------------------------------------
# Tutor (Agent 5) — on-demand, review-only feedback for a wrong answer
# ---------------------------------------------------------------------------

# Process-local cache of generated notes, keyed by (question_id, option_id). The
# note depends on the question and which wrong option was chosen — not on the
# individual student — so it is safe to share across students. This is a
# deliberate placeholder: it is ephemeral (cleared on restart, not shared across
# worker processes), so the same note may occasionally regenerate. Swap it for
# Django's cache or a TutorNote model later; get_tutor_feedback is the only
# caller, so nothing else changes.
_TUTOR_CACHE: dict[tuple[int, int], str] = {}


def _build_tutor_prompt(question: Question, option: AnswerOption) -> str:
    """Assemble the Tutor's user message from the data the Architect persisted.

    Degrades gracefully: legacy/seeded questions have no `solution`, and some
    distractors carry no misconception tag — in both cases we tell the model to
    infer the error rather than crashing on a missing key.
    """
    solution = question.solution or {}
    steps = solution.get("steps", [])
    misconceptions = solution.get("misconceptions", {})

    steps_text = (
        "\n".join(
            f"{i}. {s.get('label', '')}: {s.get('detail', '')}"
            for i, s in enumerate(steps, 1)
        )
        or "(no worked solution on file — infer the method from the problem)"
    )

    slug = option.misconception
    if slug and slug in misconceptions:
        mistake = misconceptions[slug]
    else:
        mistake = (
            "unknown — infer the most likely error from the worked solution and "
            "the student's answer."
        )

    return (
        f"PROBLEM:\n{question.text}\n\n"
        f"WORKED SOLUTION (correct; for your reasoning only):\n{steps_text}\n\n"
        f"CORRECT ANSWER (never reveal to the student): "
        f"{solution.get('answer_key', '(unknown)')}\n\n"
        f"STUDENT'S WRONG ANSWER: {option.text}\n\n"
        f"THE STUDENT'S LIKELY MISTAKE: {mistake}"
    )


def get_tutor_feedback(attempt: TestAttempt, question_id: int) -> str:
    """Return an on-demand 'margin note' for one wrong answer in a finished attempt.

    Review-only: the attempt must be completed — we never reveal that an answer
    was wrong mid-test (correctness on mocks is withheld until /finish/). Raises
    the 4xx-shaped errors the view surfaces. Cached per (question, option) so
    repeated requests don't re-bill the LLM.
    """
    if not attempt.is_completed:
        raise ValidationError(
            {
                "detail": "tutor feedback is available only after finishing the attempt",
                "code": "attempt_not_finished",
            }
        )

    try:
        question = Question.objects.get(pk=question_id, tests=attempt.test)
    except Question.DoesNotExist as exc:
        raise NotFound(
            {"detail": "question not in test", "code": "question_not_in_test"}
        ) from exc

    try:
        answer = AttemptAnswer.objects.select_related("selected_option").get(
            attempt=attempt, question=question
        )
    except AttemptAnswer.DoesNotExist as exc:
        raise ValidationError(
            {"detail": "no answer recorded for this question", "code": "not_answered"}
        ) from exc

    if answer.is_correct:
        raise ValidationError(
            {"detail": "this answer was correct; no feedback needed", "code": "answer_correct"}
        )

    option = answer.selected_option
    if option is None:
        raise ValidationError(
            {"detail": "no option was selected", "code": "no_option"}
        )

    cache_key = (question.pk, option.pk)
    if cache_key in _TUTOR_CACHE:
        return _TUTOR_CACHE[cache_key]

    # Lazy imports keep the LLM stack (and its API key) out of Django startup and
    # out of any request/test that never reaches the Tutor.
    from config import TUTOR_MODEL
    from llm import chat_anthropic
    from prompts import TUTOR_SYSTEM

    note = chat_anthropic(
        TUTOR_SYSTEM,
        _build_tutor_prompt(question, option),
        model=TUTOR_MODEL,
    ).strip()

    _TUTOR_CACHE[cache_key] = note
    return note


# ---------------------------------------------------------------------------
# Read helpers used by views
# ---------------------------------------------------------------------------


def get_attempt_for_owner(user, attempt_id: int) -> TestAttempt:
    """Fetch an attempt the user owns or 404 otherwise."""
    attempt = get_object_or_404(
        TestAttempt.objects.select_related("test"),
        pk=attempt_id,
    )
    if attempt.student_id != user.id:
        # Hide existence to non-owners.
        raise NotFound({"detail": "attempt not found", "code": "not_found"})
    return attempt


def get_test_questions_ordered(test: Test):
    """Return questions for a test, ordered by TestQuestion.order."""
    return (
        Question.objects.filter(tests=test)
        .prefetch_related(
            Prefetch(
                "options",
                queryset=AnswerOption.objects.order_by("id"),
            )
        )
        .order_by("testquestion__order", "testquestion__id")
    )


def build_attempt_start_payload(attempt: TestAttempt) -> dict:
    """Serializer-friendly payload for AttemptStart."""
    questions = list(get_test_questions_ordered(attempt.test))
    return {
        "attempt_id": attempt.pk,
        "test": attempt.test,
        "started_at": attempt.started_at,
        "questions": questions,
    }


def build_attempt_result_payload(attempt: TestAttempt) -> dict:
    total_count = attempt.test.questions.count()
    correct_count = attempt.answers.filter(is_correct=True).count()
    return {
        "attempt_id": attempt.pk,
        "score": attempt.score if attempt.score is not None else 0.0,
        "correct_count": correct_count,
        "total_count": total_count,
        "finished_at": attempt.finished_at,
    }


def build_attempt_review_payload(attempt: TestAttempt) -> dict:
    """Per-question review with correct option + explanation.

    Only the owner should ever see this; the view enforces that.
    """
    answers_by_question: dict[int, AttemptAnswer] = {
        ans.question_id: ans
        for ans in attempt.answers.select_related("selected_option").all()
    }

    questions = list(
        Question.objects.filter(tests=attempt.test)
        .prefetch_related(
            Prefetch(
                "options",
                queryset=AnswerOption.objects.order_by("id"),
            )
        )
        .order_by("testquestion__order", "testquestion__id")
    )

    items: list[dict] = []
    for question in questions:
        options = list(question.options.all())
        correct_option: Optional[AnswerOption] = next(
            (o for o in options if o.is_correct), None
        )
        ans = answers_by_question.get(question.pk)
        items.append(
            {
                "question_id": question.pk,
                "question_text": question.text,
                "selected_option_id": ans.selected_option_id if ans else None,
                "correct_option_id": correct_option.pk if correct_option else 0,
                "is_correct": bool(ans.is_correct) if ans else False,
                "explanation": question.explanation or "",
                "options": options,
            }
        )

    return {
        "attempt_id": attempt.pk,
        "score": attempt.score,
        "items": items,
    }
