"""Business logic for the content app.

These services are intentionally pure: views call them, they return plain
dicts / lookups / model instances. No DRF response shaping happens here.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.apps import apps
from django.db.models import Model


def compute_lesson_completion(user, lesson_ids: Iterable[int]) -> dict[int, bool]:
    """Return {lesson_id: completed_bool} for the given lesson ids and user.

    A lesson is considered completed iff the user has at least one
    `TestAttempt` against a `Test` whose `lesson_id` equals the lesson id
    and `is_completed=True`.

    Unauthenticated users get all-False without hitting the DB.
    Missing lesson ids are filled in with False.
    """
    lesson_ids = list(lesson_ids)
    result: dict[int, bool] = {lid: False for lid in lesson_ids}

    if not lesson_ids:
        return result
    if user is None or not getattr(user, "is_authenticated", False):
        return result

    TestAttempt = apps.get_model("assessments", "TestAttempt")

    completed_lesson_ids = (
        TestAttempt.objects.filter(
            student=user,
            is_completed=True,
            test__lesson_id__in=lesson_ids,
        )
        .values_list("test__lesson_id", flat=True)
        .distinct()
    )

    for lid in completed_lesson_ids:
        if lid in result:
            result[lid] = True
    return result


def get_micro_test_id_for_lesson(lesson: Model) -> int | None:
    """Return the id of the micro `Test` linked to this lesson, or None.

    A lesson can have at most one canonical micro-test in the MVP. If
    several exist (legacy / bad data) we return the lowest pk to be
    deterministic.
    """
    if lesson is None:
        return None

    Test = apps.get_model("assessments", "Test")
    return (
        Test.objects.filter(lesson_id=lesson.pk, type="micro")
        .order_by("pk")
        .values_list("pk", flat=True)
        .first()
    )
