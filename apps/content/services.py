"""Business logic for the content app.

These services are intentionally pure: views call them, they return plain
dicts / lookups / model instances. No DRF response shaping happens here.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

from django.apps import apps
from django.core.cache import cache
from django.db.models import Model, Max, QuerySet, OuterRef, Exists, Q
from django.utils import timezone

import config
from apps.assessments.models import TestAttempt
from apps.roadmap.models import StudentTopicMastery
from apps.users.models import CustomUser

if TYPE_CHECKING:
    from apps.content.models import Lesson, Subject, ClassGrade


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


def calculate_student_progress(lessons: QuerySet[Lesson], student: CustomUser, cache_key: str) -> int:
    progress = cache.get(cache_key)
    if progress is not None:
        return progress

    total = lessons.count()
    passed = get_passed_lesson_count(lessons, student)
    progress = round(passed / total * 100) if total else 0

    cache.set(cache_key, progress, timeout=300)
    return progress


def get_passed_lesson_count(lessons: QuerySet[Lesson], student: CustomUser) -> int:
    mastery_exists = StudentTopicMastery.objects.filter(
        student=student,
        tag_id=OuterRef("test__lesson__tag_id"),
        theta__gte=1,
    )

    passed = (
        TestAttempt.objects
        .filter(
            student=student,
            test__lesson__in=lessons,
            is_completed=True,
            score__isnull=False,
        )
        .values("test__lesson")
        .annotate(
            best_score=Max("score"),
            has_mastery=Exists(mastery_exists)
        )
        .filter(
            Q(best_score__gte=config.TEST_PASS_THRESHOLD) |
            Q(has_mastery=True)
        )
        .count()
    )
    return passed


def invalidate_cache_for_student_and_lesson(lesson_id: int, student_id: int | None = None):
    from apps.content.models import Lesson
    lesson = Lesson.objects.select_related("module__class_grade__subject").get(id=lesson_id)
    module = lesson.module
    class_grade = module.class_grade
    subject = class_grade.subject

    if student_id is None:
        student_ids = subject.students.values_list("user_id", flat=True)
    else:
        student_ids = [student_id]

    for s_id in student_ids:
        cache.delete_many([
            f"student_{s_id}:module_{module.id}",
            f"student_{s_id}:class_grade_{class_grade.id}",
            f"student_{s_id}:subject_{subject.id}",
        ])


def seconds_until_midnight() -> int:
    now = timezone.localtime()
    midnight = (now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return int((midnight - now).total_seconds())


def find_next_lesson(subject: Subject) -> Lesson:
    lessons = Lesson.objects.filter(module__class_grade__subject=subject).select_related("module__class_grade__subject")

    pass
