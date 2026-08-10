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
from django.db import transaction
from django.db.models import Model, Max, QuerySet, OuterRef, Exists, Q
from django.utils import timezone

import config
from apps.assessments.models import TestAttempt
from apps.roadmap.models import StudentTopicMastery
from apps.users.models import CustomUser

if TYPE_CHECKING:
    from apps.content.models import Lesson, NextLesson, Subject, ClassGrade


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


def get_passed_lesson_ids(lessons: QuerySet[Lesson], student: CustomUser) -> set[int]:
    """Ids of the lessons among `lessons` the student has already passed.

    Passed == the best completed attempt on the lesson's test scored at least
    config.TEST_PASS_THRESHOLD, or the lesson's tag is already mastered.
    """
    mastery_exists = StudentTopicMastery.objects.filter(
        student=student,
        tag_id=OuterRef("test__lesson__tag_id"),
        theta__gte=1,
    )

    return set(
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
        .values_list("test__lesson", flat=True)
    )


def get_passed_lesson_count(lessons: QuerySet[Lesson], student: CustomUser) -> int:
    return len(get_passed_lesson_ids(lessons, student))


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


def get_micro_test_ids_for_lessons(lesson_ids: Iterable[int]) -> dict[int, int]:
    """Batched version of get_micro_test_id_for_lesson: {lesson_id: test_id}.

    Lessons without a micro test are simply absent from the mapping. When a
    lesson has several (legacy data) the lowest pk wins, same as the single
    lookup — the reverse iteration below lets the lowest pk overwrite.
    """
    lesson_ids = list(lesson_ids)
    if not lesson_ids:
        return {}

    Test = apps.get_model("assessments", "Test")
    pairs = (
        Test.objects.filter(lesson_id__in=lesson_ids, type="micro")
        .order_by("-pk")
        .values_list("lesson_id", "pk")
    )
    return {lesson_id: test_id for lesson_id, test_id in pairs}


def find_next_lessons(
    student: CustomUser,
    subjects: Iterable[Subject],
) -> dict[int, Lesson]:
    """{subject_id: first lesson the student has not passed yet}.

    Curriculum order is class grade -> module order -> lesson order, so "the
    next lesson" is simply the first un-passed lesson in that walk. Subjects
    the student has fully finished are absent from the mapping.

    Two queries total (one for the lessons, one for the passed ids); the
    per-subject pick is done in Python so it stays portable across backends.
    """
    from apps.content.models import Lesson

    subject_ids = [subject.pk for subject in subjects]
    if not subject_ids:
        return {}

    lessons = (
        Lesson.objects
        .filter(module__class_grade__subject_id__in=subject_ids)
        .select_related("module__class_grade")
        .order_by(
            "module__class_grade__subject_id",
            "module__class_grade__grade",
            "module__order",
            "module_id",
            "order",
            "id",
        )
    )
    passed_ids = get_passed_lesson_ids(lessons, student)

    next_by_subject: dict[int, Lesson] = {}
    for lesson in lessons:
        subject_id = lesson.module.class_grade.subject_id
        if subject_id in next_by_subject or lesson.pk in passed_ids:
            continue
        next_by_subject[subject_id] = lesson
    return next_by_subject


def sync_next_lessons(student: CustomUser) -> list[NextLesson]:
    """Rebuild and return today's next-lesson plan for `student`.

    Called on every fetch, so it is also where the plan is kept honest:

    1. rows whose date has passed — and rows for subjects the student has
       since dropped — are deleted, leaving only today's plan behind;
    2. today's surviving rows are re-checked against the student's attempts
       and flipped to "done" when the lesson has since been passed;
    3. subjects with no row for today get one, pointing at their first
       un-passed lesson.

    A subject the student has fully completed gets no row at all.
    """
    from apps.content.models import NextLesson

    profile = getattr(student, "profile", None)
    if profile is None:
        return []

    today = timezone.localdate()
    subjects = list(profile.subjects.all())
    subject_ids = [subject.pk for subject in subjects]

    with transaction.atomic():
        (
            NextLesson.objects
            .filter(student=student)
            .filter(Q(date__lt=today) | ~Q(subject_id__in=subject_ids))
            .delete()
        )

        rows = list(NextLesson.objects.filter(student=student, date=today))
        _refresh_next_lesson_statuses(student, rows)

        covered = {row.subject_id for row in rows}
        missing = [subject for subject in subjects if subject.pk not in covered]
        if missing:
            NextLesson.objects.bulk_create(
                [
                    NextLesson(
                        student=student,
                        subject_id=subject_id,
                        lesson=lesson,
                        date=today,
                        status="todo",
                    )
                    for subject_id, lesson in find_next_lessons(student, missing).items()
                ],
                # Two parallel fetches can race here; the unique constraint on
                # (student, subject, date) decides and the loser is dropped.
                ignore_conflicts=True,
            )

    return list(
        NextLesson.objects
        .filter(student=student, date=today)
        .select_related("subject", "lesson__module__class_grade__subject", "lesson__tag")
        .order_by("subject__name", "id")
    )


def _refresh_next_lesson_statuses(student: CustomUser, rows: list[NextLesson]) -> None:
    """Flip today's rows to "done" for lessons the student has since passed."""
    from apps.content.models import Lesson, NextLesson

    if not rows:
        return

    passed_ids = get_passed_lesson_ids(
        Lesson.objects.filter(id__in=[row.lesson_id for row in rows]),
        student,
    )

    changed = []
    for row in rows:
        status = "done" if row.lesson_id in passed_ids else "todo"
        if row.status != status:
            row.status = status
            changed.append(row)

    if changed:
        NextLesson.objects.bulk_update(changed, ["status"])
