"""Persistent per-topic student model — the update math (06 Phase 2, scoped).

Elo-lite: each ``(student, tag)`` carries one latent ability ``theta`` on the
logit scale (stored on :class:`~apps.roadmap.models.StudentTopicMastery`). Every
observed answer nudges ``theta`` toward the prediction error, weighted by the
question's difficulty. A learning rate that decays with ``n_observations`` makes
early answers move the estimate a lot and later ones barely at all — that decay
is exactly what stops a single lucky/careless MCQ from flipping a verdict.

The Chapter Ladder (``07_Chapter_Ladder_Spec.md``) is the first caller: it feeds
one ``(difficulty, outcome)`` observation per rung answered. The same
``update_mastery`` is reused by ``update_mastery_from_attempt`` so the global
roadmap can share one update path when its Phase 2 lands.

This module is pure Python + the ORM; no LLM, no external calls.
"""

from __future__ import annotations

import math

from django.utils import timezone

from .models import StudentTopicMastery

# Logit anchors for Question.difficulty 1..3 (07 §"Mapping the verdict to the
# student model"). Wide enough that "easy" reads as clearly easy: at theta=0,
# P(correct on easy) = sigmoid(0 - (-1.0)) ≈ 0.73, P(correct on hard) ≈ 0.27.
DIFFICULTY_LOGITS: dict[int, float] = {1: -1.0, 2: 0.0, 3: 1.0}

# Verdict thresholds on theta. Reused for the ladder's skip-on-prior so there is
# no third magic number: theta >= MASTERED_THETA => mastered; >= SOLID_THETA =>
# solid; below => gap.
SOLID_THETA = 0.0
MASTERED_THETA = 1.0

# Learning-rate schedule K(n) = K0 / (1 + n / N_SCALE). K0 is the first-answer
# step; N_SCALE sets how fast confidence damps it (at n=5 the rate halves).
_K0 = 0.8
_N_SCALE = 5.0


def difficulty_to_logit(difficulty: int) -> float:
    """Map a ``Question.difficulty`` onto the logit scale.

    Known rungs use the tuned anchors; an out-of-range difficulty extrapolates
    linearly at 1 logit per level off the medium anchor, so a future d=4 or a
    stray d=0 still produces a sane value instead of raising.
    """
    if difficulty in DIFFICULTY_LOGITS:
        return DIFFICULTY_LOGITS[difficulty]
    return float(difficulty - 2)


def learning_rate(n_observations: int) -> float:
    """K(n): large early moves, small once confident (damps single-MCQ noise)."""
    return _K0 / (1.0 + n_observations / _N_SCALE)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def update_mastery(student, tag, difficulty: int, outcome: int) -> StudentTopicMastery:
    """Apply one difficulty-weighted Elo observation to ``(student, tag)``.

    ``outcome`` is 1 (correct) or 0 (wrong). Upserts the mastery row, moves
    ``theta`` toward the prediction error, increments ``n_observations``, and
    stamps ``last_seen_at``. Returns the updated row.
    """
    row, _ = StudentTopicMastery.objects.get_or_create(student=student, tag=tag)
    p_pred = _sigmoid(row.theta - difficulty_to_logit(difficulty))
    row.theta += learning_rate(row.n_observations) * (outcome - p_pred)
    row.n_observations += 1
    row.last_seen_at = timezone.now()
    row.save(update_fields=["theta", "n_observations", "last_seen_at", "updated_at"])
    return row


def verdict_for_theta(theta: float) -> str:
    """UI-facing label (``gap`` / ``solid`` / ``mastered``) from ``theta``."""
    if theta >= MASTERED_THETA:
        return "mastered"
    if theta >= SOLID_THETA:
        return "solid"
    return "gap"


def update_mastery_from_attempt(attempt) -> None:
    """Apply :func:`update_mastery` for every answer in a finished attempt.

    One observation per (question tag) at the question's difficulty. Kept thin so
    the ladder (which updates inline, answer by answer) and the global roadmap
    hook share a single update path.
    """
    answers = attempt.answers.select_related("question").prefetch_related("question__tags")
    for ans in answers:
        outcome = 1 if ans.is_correct else 0
        difficulty = ans.question.difficulty
        for tag in ans.question.tags.all():
            update_mastery(attempt.student, tag, difficulty, outcome)
