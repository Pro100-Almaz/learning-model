"""Business logic for the assessments app.

Views in this app are thin wrappers around these functions. Anything that
touches the database, awards XP, or decides what to return belongs here so
tests can exercise it directly without HTTP plumbing.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from config import TUTOR_MODEL
from agents_and_engine.llm import chat_anthropic
# The UBT blueprint engine's Tutor. Django-free: it takes dicts and strings, and
# the model mapping plus the TutorNote cache stay here. Imported eagerly because
# the line above has already pulled the LLM stack into startup.
from ubt_question_engine import tutor as ubt_tutor

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, ExpressionWrapper, F, Q, DateTimeField
from django.shortcuts import get_object_or_404
from django.utils import timezone, translation
from rest_framework.exceptions import NotFound, ValidationError

from apps.assessments import figures
from apps.content.models import Lesson, Tag

from apps.assessments.models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestQuestion,
    TestAttempt,
    TutorNote,
)

logger = logging.getLogger("apps.assessments")


def _current_language() -> str:
    """The request's active language, set per request by LanguageMiddleware from
    Accept-Language. Falls back to ``settings.LANGUAGE_CODE`` when nothing is
    active (background jobs, management commands, tests with no request).

    Questions are stored one row per language (generated in a single language,
    never translated), so serving in a locale just means selecting the rows in
    that language — each row already carries its own text, options, and
    explanation.
    """
    return translation.get_language() or settings.LANGUAGE_CODE


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
    # Ladder attempts own their mastery writes (roadmap.ladder updates inline per
    # answer) and have no Test — skip the global roadmap path entirely for them.
    if attempt.test_id is None or attempt.source == "ladder":
        return
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

    # Ladder attempts have no Test; score off the answers actually recorded.
    if attempt.test_id is not None:
        total_count = attempt.test.questions.count()
    else:
        total_count = attempt.answers.count()
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

# How many options a MAIQE-generated question carries (1 correct + 3 distractors).
# The Architect builds them; publishing enforces the count.
#
# It is the DEFAULT, not a bank-wide rule: the UBT blueprint engine publishes
# five-option items (A-E, as the real ҰБТ paper does) and passes its own count.
# Nothing else in the bank depends on this number -- grading and the serializers
# read whatever options a question actually has.
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


def _resolve_lesson(tag: Tag, topic: Optional[str]) -> Optional[Lesson]:
    """The Lesson a generated question belongs to: by topic, else by tag.

    Topic first, because tag is too coarse to land a question accurately. The
    UBT engine gives 58 topics only 16 tags — all eight planimetry topics share
    ``planimetriya`` — so tag-only matching would file "regular polygons" and
    "chord and tangent theorems" questions under the same lesson.

    Tag stays as the fallback for questions with no topic in their solution:
    every MAIQE question, and anything hand-authored. Those keep the behaviour
    they have always had.
    """
    if topic:
        lesson = Lesson.objects.filter(topic=topic).order_by("order").first()
        if lesson is not None:
            return lesson
    return _resolve_lesson_for_tag(tag)


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
    language: str,
    solution: dict,
    options: list[dict],
    tag_slug: str,
    tag_name: str,
    content_hash: Optional[str] = None,
    expected_options: int = N_ANSWER_OPTIONS,
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

    _assert_publishable(text, options, expected_options=expected_options)

    lesson = None
    try:
        with transaction.atomic():
            tag, _ = Tag.objects.get_or_create(slug=tag_slug, defaults={"name": tag_name})
            lesson = _resolve_lesson(tag, (solution or {}).get("topic"))
            question = Question.objects.create(
                text=text,
                explanation=explanation,
                difficulty=difficulty,
                language=language,
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
        f"WORKED SOLUTION (correct; use it to structure your explanation):\n"
        f"{steps_text}\n\n"
        f"CORRECT ANSWER (reveal and explain this to the student): "
        f"{solution.get('answer_key', '(unknown)')}\n\n"
        f"STUDENT'S WRONG ANSWER: {option.text}\n\n"
        f"THE STUDENT'S LIKELY MISTAKE: {mistake}"
    )


def _build_explanation_prompt(question: Question) -> str:
    """Assemble the Tutor's user message for a SKIPPED question.

    No student answer and no misconception apply here — the student never
    attempted the problem. We hand over the worked steps and the answer key so
    the model can teach the full method and reveal the answer. Degrades the same
    way as `_build_tutor_prompt`: with no `solution` on file we tell the model to
    solve it from scratch and show its steps.
    """
    solution = question.solution or {}
    steps = solution.get("steps", [])

    steps_text = (
        "\n".join(
            f"{i}. {s.get('label', '')}: {s.get('detail', '')}"
            for i, s in enumerate(steps, 1)
        )
        or "(no worked solution on file — solve it from scratch and show your steps)"
    )

    return (
        f"PROBLEM:\n{question.text}\n\n"
        f"WORKED SOLUTION (correct; use it to structure your explanation):\n"
        f"{steps_text}\n\n"
        f"CORRECT ANSWER (reveal and explain this to the student): "
        f"{solution.get('answer_key', '(unknown)')}"
    )


def _ubt_tutor_feedback(
    question: Question,
    answer: Optional[AttemptAnswer],
    language: str,
) -> str:
    """Tutor review for a question generated by the UBT blueprint engine.

    One prompt covers all three outcomes, so unlike the MAIQE path below this is
    a single code path rather than three. The cache semantics are identical:
    keyed on (question, selected_option), with the skipped-question note living
    on the NULL-option row under the partial unique constraint.
    """
    option = answer.selected_option if answer is not None else None
    outcome = ubt_tutor.outcome_for(
        answered=option is not None,
        is_correct=bool(answer is not None and answer.is_correct),
    )

    # The note depends on the question and the chosen option, never on the
    # student, so every student who picks that option shares one row -- and one
    # bill. Read with IS NULL for the skipped case.
    cached = (
        TutorNote.objects.filter(question=question, selected_option__isnull=True)
        if option is None
        else TutorNote.objects.filter(question=question, selected_option=option)
    ).values_list("note", flat=True).first()
    if cached is not None:
        return cached

    request = ubt_tutor.build_request(
        solution=question.solution or {},
        text=question.text,
        language=language,
        outcome=outcome,
        student_answer_latex=option.text if option is not None else "",
        chosen_distractor_id=option.misconception if option is not None else "",
    )
    note = ubt_tutor.explain(request)

    # get_or_create so a concurrent request that generated first wins, rather
    # than colliding with the uniqueness constraint.
    row, _ = TutorNote.objects.get_or_create(
        question=question,
        selected_option=option,
        defaults={"note": note},
    )
    return row.note


def get_tutor_feedback(attempt: TestAttempt, question_id: int) -> str:
    """Return on-demand Tutor feedback for one question in a finished attempt.

    Three modes: a diagnosis for a wrong answer (names the specific mistake, then
    teaches the full solution and reveals the answer), a worked explanation for a
    skipped question (reveals it), or a short recap for a correct answer that
    confirms the method (reveals it). Review-only: the attempt must be completed —
    we never reveal that an answer was wrong mid-test (correctness on mocks is
    withheld until /finish/). Raises the 4xx-shaped
    errors the view surfaces. Cached per (question, option) — the explanation
    note lives on the NULL-option row — so repeated requests don't re-bill the LLM.
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

    language = question.language

    # A missing row means the student skipped the question entirely — that is a
    # valid case (explanation mode), not an error.
    try:
        answer = AttemptAnswer.objects.select_related("selected_option").get(
            attempt=attempt, question=question
        )
    except AttemptAnswer.DoesNotExist:
        answer = None

    # Questions from the UBT blueprint engine store a different `solution` shape:
    # a reproduction record (topic/mode/seed/parameters/answer_latex), not MAIQE's
    # worked steps. Feeding one to the builders below would report its answer as
    # "(unknown)" while solution["answer_latex"] holds it exactly, and would throw
    # away `mode` -- the name of the method the item tests. Route it to its own
    # Tutor, which reads that shape and covers all three outcomes in one prompt.
    if ubt_tutor.owns(question.solution):
        return _ubt_tutor_feedback(question, answer, language)

    # Decision tree. All three modes share the same LLM plumbing; they differ only
    # in the cache key (which option, if any), the system prompt, and the builder.
    #   - no row, or row with no option picked  -> explanation (reveal + teach)
    #   - correct answer                        -> recap (confirm the method, reveal answer)
    #   - a real wrong option                   -> diagnosis (name mistake, full solution, reveal)
    if answer is not None and answer.is_correct:
        # Recap mode: the student got it right (possibly by guessing), so confirm
        # the method with a short note that also states the answer. Cached per
        # (question, correct-option) like diagnosis mode; record_answer always
        # sets the option, so this is a real (non-null) key.
        option = answer.selected_option

        from config import TUTOR_MODEL
        from agents_and_engine.llm import chat_anthropic
        from agents_and_engine.prompts import tutor_recap_system

        cached = (
            TutorNote.objects.filter(question=question, selected_option=option)
            .values_list("note", flat=True)
            .first()
        )
        if cached is not None:
            return cached

        note = chat_anthropic(
            tutor_recap_system(language),
            _build_explanation_prompt(question),
            model=TUTOR_MODEL,
            max_tokens=250,
        ).strip()

        row, _ = TutorNote.objects.get_or_create(
            question=question,
            selected_option=option,
            defaults={"note": note},
        )
        return row.note

    option = answer.selected_option if answer is not None else None

    # Lazy imports keep the LLM stack (and its API key) out of Django startup and
    # out of any request/test that never reaches the Tutor.
    from config import TUTOR_MODEL
    from agents_and_engine.llm import chat_anthropic

    if option is None:
        # Explanation mode: one note per question, stored on the NULL-option row
        # (guarded by the partial unique constraint). Read via IS NULL.
        from agents_and_engine.prompts import tutor_explanation_system

        cached = (
            TutorNote.objects.filter(question=question, selected_option__isnull=True)
            .values_list("note", flat=True)
            .first()
        )
        if cached is not None:
            return cached

        note = chat_anthropic(
            tutor_explanation_system(language),
            _build_explanation_prompt(question),
            model=TUTOR_MODEL,
            max_tokens=600,
        ).strip()

        row, _ = TutorNote.objects.get_or_create(
            question=question,
            selected_option=None,
            defaults={"note": note},
        )
        return row.note

    # Diagnosis mode. Names the student's specific mistake and then teaches the
    # full correct solution through to the answer (review-only, so revealing is
    # fine). Durable cache: the note depends only on (question, option), so one
    # row is reused across every student who picks that option.
    from agents_and_engine.prompts import tutor_system

    cached = (
        TutorNote.objects.filter(question=question, selected_option=option)
        .values_list("note", flat=True)
        .first()
    )
    if cached is not None:
        return cached

    note = chat_anthropic(
        tutor_system(language),
        _build_tutor_prompt(question, option),
        model=TUTOR_MODEL,
        max_tokens=600,
    ).strip()

    # get_or_create so a concurrent request that generated first wins and we
    # don't violate the (question, option) uniqueness; return the stored note.
    row, _ = TutorNote.objects.get_or_create(
        question=question,
        selected_option=option,
        defaults={"note": note},
    )
    return row.note


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
    """Return questions for a test in the current language, ordered by
    TestQuestion.order. A test links question rows across languages; we serve
    only the rows matching the request locale (see ``_current_language``)."""
    return (
        Question.objects.filter(tests=test, language=_current_language())
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
    # Denominator is the current-language question set — the same rows the
    # student was served — so the score fraction matches what they saw.
    total_count = attempt.test.questions.filter(language=_current_language()).count()
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
        Question.objects.filter(tests=attempt.test, language=_current_language())
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

        mistake_reason = ""
        selected = ans.selected_option if ans else None
        if selected is not None and not selected.is_correct and selected.misconception:
            misconceptions = (question.solution or {}).get("misconceptions", {})
            mistake_reason = misconceptions.get(selected.misconception, "")

        items.append(
            {
                "question_id": question.pk,
                "question_text": question.text,
                "selected_option_id": ans.selected_option_id if ans else None,
                "correct_option_id": correct_option.pk if correct_option else 0,
                "is_correct": bool(ans.is_correct) if ans else False,
                "explanation": question.explanation or "",
                "mistake_reason": mistake_reason,
                "figure": figures.figure_for(question.solution, language=question.language),
                "options": options,
            }
        )

    return {
        "attempt_id": attempt.pk,
        "score": attempt.score,
        "items": items,
    }


def remove_failed_attempts(user, test: Test):
    time_limit_sec = test.time_limit_sec or 0
    cutoff = timezone.now() - datetime.timedelta(seconds=time_limit_sec)
    TestAttempt.objects.filter(
        student=user,
        test=test,
        is_completed=False,
        started_at__lt=cutoff
    ).delete()
