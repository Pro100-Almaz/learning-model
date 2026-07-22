"""Chapter Ladder — per-chapter, per-topic placement (07_Chapter_Ladder_Spec.md).

Scopes diagnosis to one chapter (``content.Module``) at a time: for each topic
the chapter teaches, walk a short easy→medium→hard difficulty ladder with
early-stop and record where the student's success crosses ~50%. That crossing is
both the per-topic verdict and a mastery (``theta``) update, so this shares the
one student model in :mod:`apps.roadmap.mastery` rather than inventing its own.

The ladder per topic (starting medium):

    medium✓ → hard      hard✓ → mastered   hard✗ → solid
    medium✗ → easy      easy✓ → solid      easy✗ → gap

Right steps up a rung, wrong steps down; the topic resolves in 2 questions (plus
one asymmetric-confirm question, see ``LADDER_CONFIRM``). The verdict is decided
by the highest difficulty the student clears: cleared hard → mastered, cleared
medium → solid, otherwise gap.

Everything is server-driven (the client never sees rung logic); the flow is
``start_ladder`` → repeated ``next_question`` / ``record_answer`` → ``chapter_plan``.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.assessments.models import AnswerOption, AttemptAnswer, Question, TestAttempt
from apps.content.models import Lesson, Module, Tag

from . import mastery
from .models import ChapterLadderSession, StudentTopicMastery

logger = logging.getLogger(__name__)

# The ladder's difficulty rungs, easy -> hard.
RUNGS = (1, 2, 3)

# Skip-on-prior gates (07 §Services / Resolved decisions #2). The ability bar
# reuses the mastered threshold — no third magic number (the spec's illustrative
# 0.85 p_mastery is replaced by mastery.MASTERED_THETA to keep one source of
# truth). A returning student must also have enough observations and a recent
# last_seen to be skipped without re-probing.
LADDER_SKIP_MIN_OBS = 4
LADDER_STALE_DAYS = 45

# Verdicts that let the student SKIP a topic's lessons — the ones a lucky guess
# could wrongly grant, so they are the ones the asymmetric confirm protects.
_SKIP_VERDICTS = ("solid", "mastered")
_DOWNGRADE = {"mastered": "solid", "solid": "gap"}


# ---------------------------------------------------------------------------
# Topic discovery
# ---------------------------------------------------------------------------
def topics_for_module(module: Module) -> list[Tag]:
    """The distinct topics a chapter teaches, in curriculum order.

    A chapter's topics are the distinct ``Tag``s of its lessons; the order is the
    order the lessons introduce them (first ``Lesson.order`` that carries each
    tag). Lessons with no tag are skipped — they teach nothing the ladder can
    assess.
    """
    lessons = (
        Lesson.objects.filter(module=module, tag__isnull=False)
        .select_related("tag")
        .order_by("order")
    )
    seen: dict[int, Tag] = {}
    for lesson in lessons:
        if lesson.tag_id not in seen:
            seen[lesson.tag_id] = lesson.tag
    return list(seen.values())


# ---------------------------------------------------------------------------
# Bank helpers
# ---------------------------------------------------------------------------
def _rungs_with_questions(tag: Tag) -> list[int]:
    """Sorted difficulties (1..3) that have at least one question for this tag."""
    present = set(
        Question.objects.filter(tags=tag, difficulty__in=RUNGS)
        .values_list("difficulty", flat=True)
        .distinct()
    )
    return sorted(present)


def _unseen_question(tag: Tag, rung: int, asked: list[int]) -> Question | None:
    """A random question for ``tag`` at difficulty ``rung`` not already asked."""
    return (
        Question.objects.filter(tags=tag, difficulty=rung)
        .exclude(pk__in=asked)
        .order_by("?")
        .first()
    )


def _pick_start_rung(avail: list[int]) -> int:
    """Available rung nearest ``LADDER_START_RUNG`` (ties → the lower rung)."""
    start = settings.LADDER_START_RUNG
    return sorted(avail, key=lambda d: (abs(d - start), d))[0]


def _neighbor(avail: list[int], rung: int, direction: int) -> int | None:
    """Next available rung strictly above (+1) or below (-1) ``rung``, or None."""
    if direction > 0:
        higher = [r for r in avail if r > rung]
        return min(higher) if higher else None
    lower = [r for r in avail if r < rung]
    return max(lower) if lower else None


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------
def _new_topic_state(avail: list[int]) -> dict:
    return {
        "phase": "probe",
        "rung": None,
        "avail": avail,
        "asked": [],
        "cleared": [],
        "stepped": False,
        "pending": None,
        "prior_theta": 0.0,
        "degraded": False,
        "verdict": None,
    }


def _active_state(session: ChapterLadderSession) -> tuple[Tag, dict] | tuple[None, None]:
    """The first unresolved topic (Tag + its state dict), or (None, None)."""
    per_topic = session.state["per_topic"]
    for tag_id in session.state["topics"]:
        st = per_topic[str(tag_id)]
        if st["verdict"] is None:
            return Tag.objects.get(pk=tag_id), st
    return None, None


def _finalize(st: dict, verdict: str) -> None:
    st["verdict"] = verdict
    st["phase"] = "done"
    st["rung"] = None
    st["pending"] = None


def _recent(last_seen_at) -> bool:
    if last_seen_at is None:
        return False
    return (timezone.now() - last_seen_at).days <= LADDER_STALE_DAYS


def _maybe_complete(session: ChapterLadderSession) -> None:
    """Flag the session done (and finish its attempt) once every topic resolves."""
    per_topic = session.state["per_topic"]
    if all(st["verdict"] is not None for st in per_topic.values()):
        session.is_complete = True
        if session.attempt_id and not session.attempt.is_completed:
            # Reuses assessments scoring; its roadmap hooks no-op for source=ladder.
            from apps.assessments.services import finish_attempt

            finish_attempt(session.attempt)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
def start_ladder(student, module: Module) -> ChapterLadderSession:
    """Create a placement session for (student, module) and seed per-topic state.

    Applies skip-on-prior so a strong/returning student's entry stays short:
    a fresh, confident ``mastered``-level prior skips the topic outright; a
    confident-but-stale prior is re-probed with a single hard question before we
    trust it; everyone else gets the full ladder. Topics whose bank is sparse
    degrade gracefully (see ``_seed_topic``).
    """
    session = ChapterLadderSession.objects.create(
        student=student, module=module, state={"topics": [], "per_topic": {}}
    )
    attempt = TestAttempt.objects.create(student=student, test=None, source="ladder")
    session.attempt = attempt

    topics = topics_for_module(module)
    order: list[int] = []
    per_topic: dict[str, dict] = {}
    for tag in topics:
        order.append(tag.id)
        per_topic[str(tag.id)] = _seed_topic(student, tag)

    session.state = {"topics": order, "per_topic": per_topic}
    _maybe_complete(session)
    session.save()
    return session


def _seed_topic(student, tag: Tag) -> dict:
    """Initial per-topic state: skip / stale-probe / full ladder / gate / no-bank."""
    avail = _rungs_with_questions(tag)
    st = _new_topic_state(avail)

    if not avail:
        # Nothing to assess — send them to the lessons rather than guess mastery.
        logger.warning("ladder: tag '%s' has no questions; verdict=gap by default", tag.slug)
        st["degraded"] = True
        _finalize(st, "gap")
        return st

    prior = (
        StudentTopicMastery.objects.filter(student=student, tag=tag).first()
    )
    confident = (
        prior is not None
        and prior.theta >= mastery.MASTERED_THETA
        and prior.n_observations >= LADDER_SKIP_MIN_OBS
    )
    if confident and _recent(prior.last_seen_at):
        # Fresh, confident prior — skip the ladder entirely.
        _finalize(st, mastery.verdict_for_theta(prior.theta))
        return st
    if confident:
        # Confident but stale — don't skip blind; ask one hard confirming question.
        st["phase"] = "stale_probe"
        st["prior_theta"] = prior.theta
        st["rung"] = max(avail)
        return st

    # Full ladder, or a single-rung pass/fail gate when the bank has only one rung.
    if len(avail) == 1:
        logger.info("ladder: tag '%s' has one rung %s; degrading to a pass/fail gate", tag.slug, avail)
        st["degraded"] = True
        st["phase"] = "gate"
    st["rung"] = _pick_start_rung(avail)
    return st


# ---------------------------------------------------------------------------
# Next question
# ---------------------------------------------------------------------------
def next_question(session: ChapterLadderSession) -> Question | None:
    """The next question to pose, or None when every topic is resolved."""
    if session.is_complete:
        return None
    tag, st = _active_state(session)
    if tag is None:
        session.is_complete = True
        session.save(update_fields=["is_complete", "updated_at"])
        return None

    question = _unseen_question(tag, st["rung"], st["asked"])
    if question is None:
        # The bank ran out at the expected rung mid-ladder (e.g. confirm has no
        # second question). Accept what we have; resolve conservatively and move on.
        logger.info(
            "ladder: tag '%s' exhausted at rung %s; resolving without more questions",
            tag.slug,
            st["rung"],
        )
        st["degraded"] = True
        _finalize(st, st["pending"] or st["verdict"] or "gap")
        _maybe_complete(session)
        session.save(update_fields=["state", "is_complete", "updated_at"])
        return next_question(session)
    return question


# ---------------------------------------------------------------------------
# Record an answer + advance the state machine
# ---------------------------------------------------------------------------
def record_answer(
    session: ChapterLadderSession, question_id: int, option_id: int | None
) -> None:
    """Record one ladder answer and advance the active topic's state machine.

    Persists an ``AttemptAnswer`` (so the answer is first-class), applies the
    inline mastery update, then steps the rung / applies early-stop + asymmetric
    confirm and writes the verdict. Raises DRF 4xx errors the view surfaces.

    ``option_id=None`` means "don't know" — the student did not answer. It is
    graded as wrong (outcome=0, NULL option) so the theta drop and the rung
    step-down happen exactly as for a wrong pick; the asymmetric confirm, which
    only fires on a correct answer, ensures a forced guess can't grant a skip.
    """
    if session.is_complete:
        raise ValidationError({"detail": "ladder already complete", "code": "ladder_complete"})

    tag, st = _active_state(session)
    if tag is None:
        raise ValidationError({"detail": "ladder already complete", "code": "ladder_complete"})

    try:
        question = Question.objects.get(pk=question_id, tags=tag)
    except Question.DoesNotExist as exc:
        raise NotFound({"detail": "question not in active topic", "code": "question_not_active"}) from exc
    if question.id in st["asked"]:
        raise ValidationError({"detail": "question already answered", "code": "already_answered"})
    if question.difficulty != st["rung"]:
        raise ValidationError({"detail": "question is not at the expected rung", "code": "wrong_rung"})
    if option_id is None:
        # "Don't know" — no option picked; grade as wrong.
        option = None
        outcome = 0
    else:
        try:
            option = AnswerOption.objects.get(pk=option_id, question=question)
        except AnswerOption.DoesNotExist as exc:
            raise NotFound({"detail": "option not in question", "code": "option_not_in_question"}) from exc
        outcome = 1 if option.is_correct else 0

    AttemptAnswer.objects.update_or_create(
        attempt=session.attempt,
        question=question,
        defaults={"selected_option": option, "is_correct": bool(outcome)},
    )
    # An "I don't know" drives the ladder verdict but is not evidence of ability,
    # so it is deliberately excluded from the theta update.
    if not dont_know:
        mastery.update_mastery(session.student, tag, question.difficulty, outcome)

    st["asked"].append(question.id)
    _transition(st, tag, question.difficulty, outcome)

    _maybe_complete(session)
    session.save(update_fields=["state", "is_complete", "updated_at"])


def _transition(st: dict, tag: Tag, difficulty: int, outcome: int) -> None:
    """Advance one topic's ladder given the just-answered (difficulty, outcome)."""
    phase = st["phase"]

    if phase == "gate":
        # Single-rung bank: pass/fail only, can't distinguish mastered.
        _finalize(st, "solid" if outcome else "gap")
        return

    if phase == "stale_probe":
        if outcome:
            _finalize(st, mastery.verdict_for_theta(st["prior_theta"]))
        else:
            # Prior no longer holds — fall into the full ladder from the start.
            avail = st["avail"]
            st["phase"] = "gate" if len(avail) == 1 else "probe"
            st["cleared"] = []
            st["stepped"] = False
            st["rung"] = _pick_start_rung(avail)
        return

    if phase == "confirm":
        pending = st["pending"]
        _finalize(st, pending if outcome else _DOWNGRADE[pending])
        return

    # phase == "probe"
    if outcome:
        st["cleared"].append(difficulty)

    if not st["stepped"]:
        # Just answered the start rung: take exactly one step, or resolve if the
        # bank has no rung to step to in that direction.
        neighbor = _neighbor(st["avail"], difficulty, +1 if outcome else -1)
        if neighbor is None:
            _resolve_probe(st, tag, difficulty, outcome)
        else:
            st["stepped"] = True
            st["rung"] = neighbor
    else:
        # Second probe answer — the ladder resolves here.
        _resolve_probe(st, tag, difficulty, outcome)


def _resolve_probe(st: dict, tag: Tag, difficulty: int, outcome: int) -> None:
    """Compute the verdict from cleared rungs; maybe enter asymmetric confirm."""
    cleared = set(st["cleared"])
    if 3 in cleared:
        verdict = "mastered"
    elif 2 in cleared:
        verdict = "solid"
    else:
        verdict = "gap"

    # Asymmetric confirm: only a verdict-deciding *correct* answer that grants a
    # skip is re-checked with a second question at that rung (a lucky guess there
    # would wrongly let the student skip the topic). A deciding wrong answer is
    # accepted on a single attempt.
    if (
        settings.LADDER_CONFIRM
        and outcome
        and verdict in _SKIP_VERDICTS
    ):
        confirm_q = _unseen_question(tag, difficulty, st["asked"])
        if confirm_q is not None:
            st["phase"] = "confirm"
            st["pending"] = verdict
            st["rung"] = difficulty
            return
        # No second question to confirm with — accept the single answer, logged.
        logger.info("ladder: tag '%s' no confirm question at rung %s; accepting single answer", tag.slug, difficulty)
        st["degraded"] = True

    _finalize(st, verdict)


# ---------------------------------------------------------------------------
# Final plan
# ---------------------------------------------------------------------------
def chapter_plan(session: ChapterLadderSession) -> dict:
    """Per-topic verdicts + the branch payload for each (07 §"Locked design").

    ``gap`` → this topic's lessons in ``Lesson.order`` (soft fail — only this
    topic, not the whole chapter). ``mastered`` → the chapter's hard problems for
    this topic. ``solid`` → known, no remediation.
    """
    per_topic = session.state["per_topic"]
    topics_payload = []
    for tag_id in session.state["topics"]:
        st = per_topic[str(tag_id)]
        tag = Tag.objects.get(pk=tag_id)
        entry = {
            "tag_id": tag_id,
            "tag_slug": tag.slug,
            "tag_name": tag.name,
            "verdict": st["verdict"],
            "degraded": st.get("degraded", False),
            "lessons": [],
            "hard_question_ids": [],
        }
        if st["verdict"] == "gap":
            entry["lessons"] = [
                {"id": lsn.id, "title": lsn.title, "order": lsn.order}
                for lsn in Lesson.objects.filter(tag=tag).order_by("order")
            ]
        elif st["verdict"] == "mastered":
            entry["hard_question_ids"] = list(
                Question.objects.filter(tags=tag, difficulty=3).values_list("id", flat=True)
            )
        topics_payload.append(entry)
    return {"module_id": session.module_id, "topics": topics_payload}
