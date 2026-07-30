"""Business logic for the careers app.

Implements the grant calculator (T-303). Pure-function style — views call
``calculate_grant(user)`` and serialize the dict.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Max

import config
from apps.assessments.models import TestAttempt

from apps.careers.models import Specialty

User = get_user_model()


class NoMockError(Exception):
    """Raised when the student has no completed math mock attempt yet."""


def _weakest_tag_name(user) -> str | None:
    """Return the weakest tag's name (lowest percent) via analytics.services.

    Returns None if the analytics service yields an empty list or fails to
    surface a tag with a `name` field. Import is local to avoid a hard
    dependency on analytics implementation order.
    """

    try:
        from apps.analytics.services import compute_tag_stats
    except Exception:
        return None

    try:
        stats = compute_tag_stats(user) or []
    except Exception:
        return None

    if not stats:
        return None

    def _percent(stat: Any) -> float:
        if isinstance(stat, dict):
            return float(stat.get("percent", 0) or 0)
        return float(getattr(stat, "percent", 0) or 0)

    def _tag_name(stat: Any) -> str | None:
        if isinstance(stat, dict):
            tag = stat.get("tag")
            if isinstance(tag, dict):
                return tag.get("name")
            return getattr(tag, "name", None)
        tag = getattr(stat, "tag", None)
        if isinstance(tag, dict):
            return tag.get("name")
        return getattr(tag, "name", None)

    weakest = min(stats, key=_percent)
    return _tag_name(weakest)


def _build_advice(gap: float, weakest_tag: str | None) -> str:
    """Russian advice string for GoalTracker.advice.

    Plan example: "До цели не хватает 20 — упор на Тригонометрию".
    """

    if gap <= 0:
        if weakest_tag:
            return f"Цель достигнута — продолжай практику по теме «{weakest_tag}»."
        return "Цель достигнута — продолжай практику."

    rounded_gap = int(round(gap))
    if weakest_tag:
        return f"До цели не хватает {rounded_gap} — упор на {weakest_tag}"
    return f"До цели не хватает {rounded_gap} — продолжай практику"


def _qualifying_grants(predicted_score: float) -> list[dict]:
    """Return Specialty rows whose newest threshold min_score <= predicted_score."""

    # latest year per specialty, annotated for filtering + min_score lookup
    specialties = (
        Specialty.objects.select_related("university")
        .prefetch_related("thresholds")
        .annotate(_latest_year=Max("thresholds__year"))
        .filter(_latest_year__isnull=False)
    )

    result: list[dict] = []
    for sp in specialties:
        latest = None
        for t in sp.thresholds.all():
            if latest is None or t.year > latest.year:
                latest = t
        if latest is None:
            continue
        if latest.min_score <= predicted_score:
            result.append(
                {
                    "university_name": sp.university.name,
                    "specialty_name": sp.name,
                    "min_score": int(latest.min_score),
                    "margin": int(round(predicted_score - latest.min_score)),
                }
            )

    # Deterministic order: biggest margin first.
    result.sort(key=lambda r: r["margin"], reverse=True)
    return result


def calculate_grant(user) -> dict:
    """Compute the GrantCalcResult dict for the given user.

    Steps (per plan/03_Ticket_Breakdown T-303 and openapi GrantCalcResult):

    1. Find the latest COMPLETED math mock attempt. 409 if none.
    2. math_score = latest.score
    3. other_subjects_total = sum(expected_scores on the profile)
    4. predicted_score = math_score + other_subjects_total
    5. qualifying_grants: specialties whose newest threshold <= predicted_score.
    6. goal: None if target_score not set; else GoalTracker with weakest tag.
    """

    latest = (
        TestAttempt.objects.filter(
            student=user,
            test__type="mock",
            is_completed=True,
        )
        .order_by("-finished_at")
        .first()
    )
    if latest is None:
        raise NoMockError

    math_score: float = float(latest.score or 0.0)

    profile = getattr(user, "profile", None)
    if profile is not None:
        other_subjects_total = float(
            sum(int(es.score) for es in profile.expected_scores.all())
        )
    else:
        other_subjects_total = 0.0

    predicted_score = math_score + other_subjects_total
    qualifying = _qualifying_grants(predicted_score)

    goal: dict | None = None
    if profile is not None and profile.target_score is not None:
        target_score = int(profile.target_score)
        gap = float(target_score) - predicted_score
        weakest_tag = _weakest_tag_name(user)
        goal = {
            "target_score": target_score,
            "predicted_score": predicted_score,
            "gap": gap,
            "weakest_tag": weakest_tag,
            "advice": _build_advice(gap, weakest_tag),
        }

    return {
        "predicted_score": predicted_score,
        "math_score": math_score,
        "other_subjects_total": other_subjects_total,
        "qualifying_grants": qualifying,
        "goal": goal,
    }

def near_miss_grants(predicted_score: float, within: int = config.NEAR_MISS_WITHIN): #to calculate and identify the grants which is almost reachable for a student
    # latest year per specialty, annotated for filtering + min_score lookup
    '''
      The intuition behind the two changed values:
        - _qualifying_grants answers "how comfortably did I clear this?" → margin is predicted − cutoff, positive because they're at or above
            it, and it sorts biggest-first (safest bets on top).
        - near_miss_grants answers "how far short am I?" → points_needed is cutoff − predicted, positive because they're below it (that's what
            the strict > in the filter guarantees), and it sorts smallest-first (closest targets on top — the most motivating).
    '''
    specialties = (
        Specialty.objects.select_related("university")
        .prefetch_related("thresholds")
        .annotate(_latest_year=Max("thresholds__year"))
        .filter(_latest_year__isnull=False)
    )


    result: list[dict] = []
    for sp in specialties:
        latest = None
        for t in sp.thresholds.all():
            if latest is None or t.year > latest.year:
                latest = t
        if latest is None:
            continue
        if predicted_score < latest.min_score and latest.min_score <= predicted_score + within:
            result.append(
                {
                    "university_name": sp.university.name,
                    "specialty_name": sp.name,
                    "min_score": int(latest.min_score),
                    "points_needed": int(round(latest.min_score - predicted_score)),
                }
            )
    # Deterministic order: lowest points_needed first.
    result.sort(key=lambda r: r["points_needed"])
    return result