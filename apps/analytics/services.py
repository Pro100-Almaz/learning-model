"""Business logic for the analytics app.

These services compute per-tag performance statistics and weak-tag
recommendations based on completed test attempts. They are pure
functions so they can be reused from views, management commands, or
tasks without DRF context.
"""

from __future__ import annotations

from typing import Iterable

from django.db.models import Count, Q

from apps.content.models import Lesson, Tag


def _percent(correct: int, total: int) -> float:
    """Null-safe percent: returns 0.0 when total is 0."""
    if not total:
        return 0.0
    return round((correct / total) * 100, 1)


def _serialize_tag(tag: Tag) -> dict:
    return {"id": tag.id, "name": tag.name, "slug": tag.slug}


def _serialize_lesson_summary(lesson: Lesson, *, completed: bool = False) -> dict:
    return {
        "id": lesson.id,
        "title": lesson.title,
        "order": lesson.order,
        "duration_sec": lesson.duration_sec,
        "completed": completed,
    }


def compute_tag_stats(user) -> list[dict]:
    """Per-tag correct/total/percent for the given user.

    Counts AttemptAnswer rows where the attempt belongs to ``user`` and
    is completed. A row matches a Tag when its question is tagged with
    that Tag.

    Returns a list of dicts shaped per the openapi.yaml ``TagStat``
    schema. Ordered by Tag.name.
    """
    # The reverse relation from Question -> AttemptAnswer uses Django's
    # default related accessor (``attemptanswer``) because the FK on
    # ``AttemptAnswer.question`` has no explicit related_name.
    total_filter = Q(
        questions__attemptanswer__attempt__student=user,
        questions__attemptanswer__attempt__is_completed=True,
    )
    correct_filter = total_filter & Q(questions__attemptanswer__is_correct=True)

    qs = Tag.objects.annotate(
        total=Count("questions__attemptanswer", filter=total_filter),
        correct=Count("questions__attemptanswer", filter=correct_filter),
    ).order_by("name")

    stats: list[dict] = []
    for tag in qs:
        total = tag.total or 0
        correct = tag.correct or 0
        stats.append(
            {
                "tag": _serialize_tag(tag),
                "correct": correct,
                "total": total,
                "percent": _percent(correct, total),
            }
        )
    return stats


def _lessons_for_tag_ids(tag_ids: Iterable[int]) -> dict[int, list[Lesson]]:
    """Group ordered, deduplicated lessons by tag id.

    A lesson is associated with a tag when it has at least one question
    tagged with that tag. Lessons are returned ordered by
    ``Lesson.order`` and deduplicated per-tag.
    """
    grouped: dict[int, list[Lesson]] = {tag_id: [] for tag_id in tag_ids}
    if not grouped:
        return grouped

    lessons = (
        Lesson.objects.filter(questions__tags__in=grouped.keys())
        .prefetch_related("questions__tags")
        .distinct()
        .order_by("order", "id")
    )
    seen: dict[int, set[int]] = {tag_id: set() for tag_id in grouped}
    for lesson in lessons:
        lesson_tag_ids: set[int] = set()
        for question in lesson.questions.all():
            for tag in question.tags.all():
                lesson_tag_ids.add(tag.id)
        for tag_id in lesson_tag_ids:
            if tag_id in grouped and lesson.id not in seen[tag_id]:
                grouped[tag_id].append(lesson)
                seen[tag_id].add(lesson.id)
    return grouped


def compute_recommendations(user) -> list[dict]:
    """Recommendations for tags where percent < 50.

    Empty list when the user has no weak tags (or no answers at all).
    Each entry contains the tag, the percent and a deduplicated list of
    LessonSummary dicts ordered by ``Lesson.order``.
    """
    stats = compute_tag_stats(user)
    weak = [s for s in stats if s["total"] > 0 and s["percent"] < 50]
    if not weak:
        return []

    tag_ids = [s["tag"]["id"] for s in weak]
    lessons_by_tag = _lessons_for_tag_ids(tag_ids)

    recommendations: list[dict] = []
    for s in weak:
        tag_id = s["tag"]["id"]
        lessons = lessons_by_tag.get(tag_id, [])
        recommendations.append(
            {
                "tag": s["tag"],
                "percent": s["percent"],
                "lessons": [_serialize_lesson_summary(lesson) for lesson in lessons],
            }
        )
    return recommendations
