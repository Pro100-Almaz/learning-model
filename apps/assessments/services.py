"""Business logic for the assessments app.

Views in this app are thin wrappers around these functions. Anything that
touches the database, awards XP, or decides what to return belongs here so
tests can exercise it directly without HTTP plumbing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.content.models import Lesson, Tag

from .models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestQuestion,
    TestAttempt,
)

logger = logging.getLogger("apps.assessments")


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


def _trigger_roadmap_hooks(attempt: TestAttempt) -> None:
    """Notify the roadmap app that an attempt just finished.

    Lazy import to keep assessments standalone and to avoid an app-loading
    cycle. Failures here must never break the attempt finish flow.
    """
    try:
        from apps.roadmap import services as roadmap_services
    except Exception:  # pragma: no cover - roadmap optional at runtime
        return
    try:
        if attempt.test.type == "diagnostic":
            roadmap_services.generate_roadmap_for_student(
                attempt.student, source_attempt=attempt, source="diagnostic"
            )
        # Mark any matching micro-test item on the active roadmap.
        roadmap_services.mark_item_status_from_attempt(attempt)
    except Exception:  # pragma: no cover - defensive
        return


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
    _trigger_roadmap_hooks(attempt)
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
# Publishing generated questions (the MAIQE graph's Publisher node calls this)
# ---------------------------------------------------------------------------

# Every generated question carries exactly this many options (1 correct + 3
# distractors). The Architect builds them; publishing enforces the count.
N_ANSWER_OPTIONS = 4


def _assert_publishable(
    text: str, options: list[dict], *, expected_options: int = N_ANSWER_OPTIONS
) -> None:
    """Refuse to persist anything that breaks the bank's core invariants.

    Downstream automated grading assumes EXACTLY ONE correct option, so a
    violation here is a data/logic bug — we raise (the batch worker logs and
    skips) instead of storing a broken item.
    """
    if not text:
        raise ValueError("publish: text is empty; refusing to persist a blank question.")
    if len(options) != expected_options:
        raise ValueError(
            f"publish: expected {expected_options} answer options, got {len(options)}."
        )
    n_correct = sum(1 for o in options if o["is_correct"])
    if n_correct != 1:
        raise ValueError(f"publish: expected exactly 1 correct option, got {n_correct}.")
    texts = [o["text"] for o in options]
    if len(set(texts)) != len(texts):
        raise ValueError(f"publish: answer options are not distinct: {texts}.")


def _resolve_lesson_for_tag(tag: Tag) -> Optional[Lesson]:
    """The Lesson that teaches this tag's topic, or None.

    Uses the explicit Lesson.tag link. If several lessons teach the tag, the
    earliest by ``order`` wins (the intro lesson). None means no lesson covers
    this topic yet — the question is still stored, but unreachable until one
    exists (the caller logs this).
    """
    return Lesson.objects.filter(tag=tag).order_by("order").first()


def _link_to_micro_test(question: Question, lesson: Lesson) -> Test:
    """Add ``question`` to its lesson's micro Test, creating the Test if needed.

    The roadmap pulls practice via ``Test.objects.filter(lesson=lesson,
    type='micro')`` and the student answer-flow requires a question to belong to
    the attempt's test — so joining the micro test is what makes a generated
    question reachable at all. Appends with the next order; idempotent via the
    (test, question) unique constraint. Returns the Test.
    """
    test, _ = Test.objects.get_or_create(
        lesson=lesson,
        type="micro",
        defaults={"title": f"{lesson.title} — практика"},
    )
    TestQuestion.objects.get_or_create(
        test=test,
        question=question,
        defaults={"order": TestQuestion.objects.filter(test=test).count()},
    )
    return test


def publish_generated_question(
    *,
    text: str,
    explanation: str,
    difficulty: int,
    solution: dict,
    options: list[dict],
    tag_slug: str,
    tag_name: str,
    content_hash: Optional[str] = None,
) -> dict:
    """Persist one generated question, its options, and its content links.

    The single DB boundary for the MAIQE Publisher node. In one transaction it:
      0. dedups on ``content_hash`` — reuses the existing row if the same
         problem was already published (so batches can't duplicate the bank);
      1. get_or_creates the Tag and resolves the Lesson that teaches it;
      2. creates the Question (linked to that lesson) + its AnswerOptions;
      3. joins the question to the lesson's micro Test so students and the
         roadmap can actually reach it.

    Returns ``{question_id, was_duplicate, lesson_id, test_id}``. On a dedup hit
    or when no lesson teaches the tag, the link ids are None.
    """

    def _dup(qid: int) -> dict:
        return {"question_id": qid, "was_duplicate": True, "lesson_id": None, "test_id": None}

    # Fast path: this exact problem is already in the bank -> reuse it.
    if content_hash:
        existing = (
            Question.objects.filter(content_hash=content_hash)
            .values_list("pk", flat=True)
            .first()
        )
        if existing is not None:
            return _dup(existing)

    _assert_publishable(text, options)

    lesson = None
    try:
        with transaction.atomic():
            tag, _ = Tag.objects.get_or_create(slug=tag_slug, defaults={"name": tag_name})
            lesson = _resolve_lesson_for_tag(tag)
            question = Question.objects.create(
                text=text,
                explanation=explanation,
                difficulty=difficulty,
                solution=solution,
                content_hash=content_hash,
                lesson=lesson,
            )
            question.tags.add(tag)
            AnswerOption.objects.bulk_create(
                [
                    AnswerOption(
                        question=question,
                        text=opt["text"],
                        is_correct=opt["is_correct"],
                        misconception=opt.get("misconception", ""),
                    )
                    for opt in options
                ]
            )
            test = _link_to_micro_test(question, lesson) if lesson else None
    except IntegrityError:
        # Lost a race: a concurrent worker inserted the same hash between our
        # pre-check and our write. The unique constraint is the real guarantee.
        if content_hash:
            existing = (
                Question.objects.filter(content_hash=content_hash)
                .values_list("pk", flat=True)
                .first()
            )
            if existing is not None:
                return _dup(existing)
        raise  # not our dedup constraint -> a genuine error

    if lesson is None:
        logger.warning(
            "Published Question #%s (tag=%s) but no Lesson teaches that tag yet; "
            "it won't surface to students or the roadmap until one does.",
            question.pk,
            tag_slug,
        )

    return {
        "question_id": question.pk,
        "was_duplicate": False,
        "lesson_id": lesson.pk if lesson else None,
        "test_id": test.pk if test else None,
    }


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
    from maiqe.config import TUTOR_MODEL
    from maiqe.llm import chat_anthropic
    from maiqe.prompts import TUTOR_SYSTEM

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
