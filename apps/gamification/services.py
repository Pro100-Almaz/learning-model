"""Business logic for the gamification app.

Exposes pure helpers used by other apps (assessments, content) to:
  - award XP for events (video watched, correct answer)
  - update the daily streak
  - compute the user's level + XP-to-next-level based on settings.LEVELS

State is persisted on StudentProgress, Streak, and XPEvent.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Streak, StudentProgress, XPEvent


# ---------------------------------------------------------------------------
# Level helpers
# ---------------------------------------------------------------------------


def _levels() -> list[tuple[int, str, str]]:
    """Return LEVELS sorted ascending by min_total_xp."""
    return sorted(settings.LEVELS, key=lambda lvl: lvl[0])


def level_for_xp(total_xp: int) -> Tuple[str, str]:
    """Return (code, label) for the level the user currently sits in."""
    current_code, current_label = "novice", "Новичок"
    for min_xp, code, label in _levels():
        if total_xp >= min_xp:
            current_code, current_label = code, label
        else:
            break
    return current_code, current_label


def compute_xp_to_next_level(progress: StudentProgress) -> int:
    """How many XP points until the next tier — 0 when already maxed out."""
    total_xp = progress.total_xp
    for min_xp, _code, _label in _levels():
        if total_xp < min_xp:
            return min_xp - total_xp
    return 0


# ---------------------------------------------------------------------------
# Progress / Streak retrieval
# ---------------------------------------------------------------------------


def get_or_create_progress(user) -> StudentProgress:
    progress, _ = StudentProgress.objects.get_or_create(student=user)
    return progress


def get_or_create_streak(user) -> Streak:
    streak, _ = Streak.objects.get_or_create(student=user)
    return streak


# ---------------------------------------------------------------------------
# Streak
# ---------------------------------------------------------------------------


def update_streak(user) -> Streak:
    """Bump the user's streak for today's activity.

    Rules:
      - First-ever activity OR a gap of >1 day -> current_streak resets to 1.
      - Activity exactly one day after last activity -> current_streak += 1.
      - Same calendar day as last activity -> no change.
    Always sets last_active_date to today.
    """
    today = timezone.localdate()
    with transaction.atomic():
        streak, _ = Streak.objects.select_for_update().get_or_create(student=user)
        last = streak.last_active_date

        if last is None:
            streak.current_streak = 1
        elif last == today:
            # Already counted today — no change to current_streak.
            pass
        elif last == today - timedelta(days=1):
            streak.current_streak = (streak.current_streak or 0) + 1
        else:
            # Gap of more than one day — reset.
            streak.current_streak = 1

        if streak.current_streak > streak.longest_streak:
            streak.longest_streak = streak.current_streak

        streak.last_active_date = today
        streak.save()
    return streak


# ---------------------------------------------------------------------------
# XP
# ---------------------------------------------------------------------------


def award_xp(user, reason: str) -> int:
    """Award XP for a known reason, persist event, recompute level, bump streak.

    Returns the amount of XP awarded. Unknown reasons return 0 without
    creating an event — callers should ensure reason is in settings.XP_RULES.
    """
    amount = settings.XP_RULES.get(reason, 0)
    if amount <= 0:
        return 0

    with transaction.atomic():
        XPEvent.objects.create(student=user, amount=amount, reason=reason)
        progress, _ = StudentProgress.objects.select_for_update().get_or_create(
            student=user
        )
        progress.total_xp = (progress.total_xp or 0) + amount
        code, _label = level_for_xp(progress.total_xp)
        progress.level_code = code
        progress.save(update_fields=["total_xp", "level_code"])

    update_streak(user)
    return amount
