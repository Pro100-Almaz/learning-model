"""Roadmap business logic.

Two responsibilities:

1. ``generate_roadmap_for_student`` — given a finished diagnostic (or mock)
   ``TestAttempt``, build a per-tag-weakness-ordered list of lessons (with
   linked micro-tests) and persist as a fresh ``Roadmap``. Prior active
   roadmaps for that student are archived (``is_active=False``).
2. ``mark_item_status_from_attempt`` — called when ANY ``TestAttempt`` finishes.
   If the attempt's test is a micro-test referenced by an item in the
   student's active roadmap, the item flips to ``in_progress`` or
   ``completed`` based on a pass threshold.

Both functions are no-ops when prerequisites are missing (no diagnostic
attempt, no active roadmap, no matching item, etc.) so callers can invoke
them defensively without guards.
"""

from __future__ import annotations

from typing import Optional

from django.db import transaction
from django.utils import timezone


MICRO_TEST_PASS_THRESHOLD = 70.0  # percent — same as roadmap "completed" cutoff


def get_active_diagnostic_test():
    """Return the canonical diagnostic Test (newest one), or ``None``.

    There's at most one diagnostic test in normal operation, but we tolerate
    multiple by picking the most-recently-created so admin tweaks don't break.
    """
    from apps.assessments.models import Test  # local import: avoid app-load cycle

    return (
        Test.objects.filter(type="diagnostic")
        .order_by("-pk")
        .first()
    )


def get_latest_diagnostic_attempt(student):
    """Most recent COMPLETED diagnostic attempt for the student, or None."""
    from apps.assessments.models import TestAttempt

    return (
        TestAttempt.objects.filter(
            student=student,
            test__type="diagnostic",
            is_completed=True,
        )
        .order_by("-finished_at")
        .first()
    )


def get_latest_mock_attempt(student):
    """Fallback when no diagnostic exists — most recent completed math mock."""
    from apps.assessments.models import TestAttempt

    return (
        TestAttempt.objects.filter(
            student=student,
            test__type="mock",
            is_completed=True,
        )
        .order_by("-finished_at")
        .first()
    )


def get_active_roadmap(student):
    """The student's currently-active Roadmap, or None."""
    from .models import Roadmap

    return (
        Roadmap.objects.filter(student=student, is_active=True)
        .order_by("-created_at")
        .first()
    )


def _compute_tag_mastery(attempt) -> list[tuple[int, float]]:
    """Return [(tag_id, percent), ...] sorted weakest first.

    Tags that appear in 0 questions of the attempt are excluded.
    """
    from apps.content.models import Tag

    answers = attempt.answers.prefetch_related("question__tags").all()
    buckets: dict[int, dict[str, int]] = {}
    for ans in answers:
        for tag in ans.question.tags.all():
            slot = buckets.setdefault(tag.id, {"correct": 0, "total": 0})
            slot["total"] += 1
            if ans.is_correct:
                slot["correct"] += 1

    scores: list[tuple[int, float]] = []
    for tag_id, stats in buckets.items():
        if stats["total"] == 0:
            continue
        percent = round(stats["correct"] / stats["total"] * 100, 1)
        scores.append((tag_id, percent))
    # Weakest first; tiebreak by tag id for determinism.
    scores.sort(key=lambda x: (x[1], x[0]))

    # Make sure tags with NO answered questions in the attempt aren't picked
    # by accident later — caller decides if they want full Tag table or not.
    _ = Tag  # keep import readable in case later we widen the universe
    return scores


def _ordered_lesson_iter(tag_ids: list[int]):
    """Yield Lessons that have at least one Question with each of ``tag_ids``.

    Iterates tag-by-tag in the order given. Lessons that have already been
    yielded under an earlier (weaker) tag are skipped — first weakness wins
    as the "primary" rationale.
    """
    from apps.content.models import Lesson

    seen_lesson_ids: set[int] = set()

    for tag_id in tag_ids:
        lessons = (
            Lesson.objects.filter(questions__tags__id=tag_id)
            .distinct()
            .order_by("module__order", "order", "pk")
        )
        for lesson in lessons:
            if lesson.pk in seen_lesson_ids:
                continue
            seen_lesson_ids.add(lesson.pk)
            yield lesson, tag_id

    # After we've emitted everything tied to a weak tag, the caller may want
    # to also emit lessons unrelated to any tag in the attempt for full
    # coverage. That's done in generate_roadmap_for_student so the rationale
    # there is "general practice".


def generate_roadmap_for_student(student, source_attempt=None, source: str = "diagnostic"):
    """Create a fresh active ``Roadmap`` from the student's diagnostic results.

    Returns the new Roadmap. If no source attempt is provided AND none can be
    inferred, returns ``None`` (the student needs to take a diagnostic first).

    Algorithm:
      1. Resolve the source attempt (param > latest diagnostic > latest mock).
      2. Compute per-tag mastery from that attempt's answers.
      3. For each weak tag (ordered weakest first), emit its lessons with
         rationale "Слабая тема: {Tag} ({percent}%)".
      4. After the weakness-driven set, emit any remaining lessons that
         weren't yet covered, with no specific rationale, in module order.
      5. Link each item to its lesson's micro-test (Test.type='micro').
      6. Atomically: archive prior active roadmaps, write new one + items.
    """
    from apps.assessments.models import Test as AssessmentsTest
    from apps.content.models import Lesson, Tag

    from .models import Roadmap, RoadmapItem

    # 1. Resolve source attempt.
    if source_attempt is None:
        source_attempt = get_latest_diagnostic_attempt(student)
        if source_attempt is None:
            source_attempt = get_latest_mock_attempt(student)
            if source_attempt is not None:
                source = "mock_recompute"
    if source_attempt is None:
        return None  # Nothing to base a roadmap on yet.

    # 2. Per-tag mastery, weakest first.
    tag_scores = _compute_tag_mastery(source_attempt)
    tag_by_id = {t.pk: t for t in Tag.objects.filter(pk__in=[tid for tid, _ in tag_scores])}

    # 3 + 4. Build item plan.
    items_plan: list[dict] = []
    seen_lesson_ids: set[int] = set()

    for tag_id, percent in tag_scores:
        tag = tag_by_id.get(tag_id)
        for lesson, _ in _ordered_lesson_iter([tag_id]):
            if lesson.pk in seen_lesson_ids:
                continue
            seen_lesson_ids.add(lesson.pk)
            micro = (
                AssessmentsTest.objects.filter(lesson=lesson, type="micro")
                .order_by("pk")
                .first()
            )
            items_plan.append(
                {
                    "lesson_id": lesson.pk,
                    "micro_test_id": micro.pk if micro else None,
                    "weak_tag_id": tag.pk if tag else None,
                    "rationale": (
                        f"Слабая тема: {tag.name} ({percent:.0f}%)"
                        if tag
                        else ""
                    ),
                }
            )

    # 4. Full coverage — any leftover lessons in module/lesson order.
    leftover = (
        Lesson.objects.exclude(pk__in=seen_lesson_ids)
        .order_by("module__order", "order", "pk")
    )
    for lesson in leftover:
        micro = (
            AssessmentsTest.objects.filter(lesson=lesson, type="micro")
            .order_by("pk")
            .first()
        )
        items_plan.append(
            {
                "lesson_id": lesson.pk,
                "micro_test_id": micro.pk if micro else None,
                "weak_tag_id": None,
                "rationale": "Общая практика",
            }
        )

    # 5 + 6. Persist atomically.
    with transaction.atomic():
        Roadmap.objects.filter(student=student, is_active=True).update(is_active=False)
        roadmap = Roadmap.objects.create(
            student=student,
            is_active=True,
            source=source,
            source_attempt=source_attempt,
        )
        RoadmapItem.objects.bulk_create(
            [
                RoadmapItem(
                    roadmap=roadmap,
                    order=order,
                    lesson_id=plan["lesson_id"],
                    micro_test_id=plan["micro_test_id"],
                    weak_tag_id=plan["weak_tag_id"],
                    rationale=plan["rationale"],
                    status="pending",
                )
                for order, plan in enumerate(items_plan, start=1)
            ]
        )
    return roadmap


def mark_item_status_from_attempt(attempt) -> Optional["RoadmapItem"]:  # noqa: F821
    """If the attempt completes a roadmap item's micro-test, update it.

    Returns the touched ``RoadmapItem`` (or ``None`` if no match).

    Rules:
      - The attempt must be completed.
      - The attempt's test must be linked to some RoadmapItem on the
        student's active roadmap.
      - score >= MICRO_TEST_PASS_THRESHOLD ⇒ status="completed", completed_at=now.
      - score <  threshold ⇒ status="in_progress" (so the UI flags partial
        progress without locking the student out of retries).
    """
    if not getattr(attempt, "is_completed", False):
        return None

    roadmap = get_active_roadmap(attempt.student)
    if roadmap is None:
        return None

    item = (
        roadmap.items.filter(micro_test_id=attempt.test_id)
        .order_by("order")
        .first()
    )
    if item is None:
        return None

    score = attempt.score or 0.0
    if score >= MICRO_TEST_PASS_THRESHOLD:
        item.status = "completed"
        item.completed_at = timezone.now()
    elif item.status == "pending":
        item.status = "in_progress"
    item.save(update_fields=["status", "completed_at"])
    return item
