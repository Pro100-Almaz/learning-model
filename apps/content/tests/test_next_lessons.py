"""Tests for GET /api/v1/lessons/next_lessons/ (NextLessonsView).

Covers the three jobs the endpoint does on every fetch: build today's plan,
refresh the status of rows that were already there, and drop rows whose date
has passed.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

import config
from apps.accounts.models import StudentProfile
from apps.assessments.models import Test as AssessmentTest, TestAttempt
from apps.content.models import ClassGrade, Lesson, Module, NextLesson, Subject


class NextLessonsTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("v1:lessons:next-lessons")
        cls.user = get_user_model().objects.create_user(
            email="student@example.com",
            password="testpassword123",
        )
        cls.profile = StudentProfile.objects.create(user=cls.user)

        cls.math = Subject.objects.create(name="Математика", slug="math")
        cls.history = Subject.objects.create(name="История", slug="history")
        cls.profile.subjects.add(cls.math, cls.history)

        # Math spans two grades so the walk order (grade -> module -> lesson)
        # is actually exercised.
        cls.math_10 = ClassGrade.objects.create(grade=10, subject=cls.math)
        cls.math_11 = ClassGrade.objects.create(grade=11, subject=cls.math)
        module_10 = Module.objects.create(
            title="Функции", slug="functions", order=1, class_grade=cls.math_10
        )
        module_11 = Module.objects.create(
            title="Логарифмы", slug="logs", order=1, class_grade=cls.math_11
        )
        cls.math_first = cls._lesson(module_10, "Функция", 1)
        cls.math_second = cls._lesson(module_10, "График", 2)
        cls.math_third = cls._lesson(module_11, "Логарифм", 1)

        history_grade = ClassGrade.objects.create(grade=11, subject=cls.history)
        history_module = Module.objects.create(
            title="Қазақ хандығы", slug="khanate", order=1, class_grade=history_grade
        )
        cls.history_first = cls._lesson(history_module, "Хандық құрылуы", 1)

    @classmethod
    def _lesson(cls, module: Module, title: str, order: int) -> Lesson:
        return Lesson.objects.create(
            module=module,
            title=title,
            video_url=f"https://youtu.be/{module.slug}-{order}",
            duration_sec=300,
            order=order,
        )

    def _pass(self, lesson: Lesson, score: float | None = None) -> None:
        """Give the user a completed attempt on `lesson` at a passing score."""
        test = AssessmentTest.objects.create(
            type="micro", title=f"Micro {lesson.title}", lesson=lesson
        )
        TestAttempt.objects.create(
            student=self.user,
            test=test,
            score=config.TEST_PASS_THRESHOLD if score is None else score,
            is_completed=True,
            finished_at=timezone.now(),
        )

    def test_requires_auth(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_first_unpassed_lesson_per_subject(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

        by_subject = {row["subject"]["slug"]: row for row in response.data}
        self.assertEqual(by_subject["math"]["lesson"]["id"], self.math_first.id)
        self.assertEqual(by_subject["history"]["lesson"]["id"], self.history_first.id)
        self.assertEqual(by_subject["math"]["status"], "todo")
        self.assertEqual(str(by_subject["math"]["date"]), str(timezone.localdate()))

        # One persisted row per subject, tied to the student.
        self.assertEqual(NextLesson.objects.filter(student=self.user).count(), 2)

    def test_skips_passed_lessons_and_crosses_grades(self):
        self._pass(self.math_first)
        self._pass(self.math_second)
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url)

        by_subject = {row["subject"]["slug"]: row for row in response.data}
        # Both grade-10 lessons are done, so the plan moves on to grade 11.
        self.assertEqual(by_subject["math"]["lesson"]["id"], self.math_third.id)

    def test_below_threshold_score_is_not_passed(self):
        self._pass(self.math_first, score=config.TEST_PASS_THRESHOLD - 1)
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url)

        by_subject = {row["subject"]["slug"]: row for row in response.data}
        self.assertEqual(by_subject["math"]["lesson"]["id"], self.math_first.id)
        self.assertEqual(by_subject["math"]["status"], "todo")

    def test_refetch_marks_done_without_creating_new_row(self):
        self.client.force_authenticate(user=self.user)
        self.client.get(self.url)

        # Student passes today's math lesson, then the front-end refetches.
        self._pass(self.math_first)
        response = self.client.get(self.url)

        by_subject = {row["subject"]["slug"]: row for row in response.data}
        self.assertEqual(by_subject["math"]["status"], "done")
        self.assertEqual(by_subject["math"]["lesson"]["id"], self.math_first.id)
        # Today's plan stays fixed — the next lesson is tomorrow's business.
        self.assertEqual(NextLesson.objects.filter(student=self.user).count(), 2)

    def test_rows_from_past_days_are_deleted_and_rebuilt(self):
        self.client.force_authenticate(user=self.user)
        self.client.get(self.url)

        yesterday = timezone.localdate() - timedelta(days=1)
        NextLesson.objects.filter(student=self.user).update(date=yesterday)
        self._pass(self.math_first)

        response = self.client.get(self.url)

        self.assertFalse(
            NextLesson.objects.filter(student=self.user, date=yesterday).exists()
        )
        self.assertEqual(
            NextLesson.objects.filter(student=self.user).count(), 2
        )
        by_subject = {row["subject"]["slug"]: row for row in response.data}
        # A fresh day picks up where the student actually is.
        self.assertEqual(by_subject["math"]["lesson"]["id"], self.math_second.id)

    def test_finished_subject_is_omitted(self):
        for lesson in (self.math_first, self.math_second, self.math_third):
            self._pass(lesson)
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url)

        slugs = {row["subject"]["slug"] for row in response.data}
        self.assertEqual(slugs, {"history"})

    def test_dropped_subject_row_is_removed(self):
        self.client.force_authenticate(user=self.user)
        self.client.get(self.url)

        self.profile.subjects.remove(self.history)
        response = self.client.get(self.url)

        slugs = {row["subject"]["slug"] for row in response.data}
        self.assertEqual(slugs, {"math"})
        self.assertEqual(NextLesson.objects.filter(student=self.user).count(), 1)

    def test_repeated_fetches_are_idempotent(self):
        self.client.force_authenticate(user=self.user)
        first = self.client.get(self.url)
        second = self.client.get(self.url)

        self.assertEqual(
            [row["id"] for row in first.data], [row["id"] for row in second.data]
        )
        self.assertEqual(NextLesson.objects.filter(student=self.user).count(), 2)

    def test_lesson_payload_carries_micro_test_id(self):
        test = AssessmentTest.objects.create(
            type="micro", title="Micro Функция", lesson=self.math_first
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url)

        by_subject = {row["subject"]["slug"]: row for row in response.data}
        self.assertEqual(by_subject["math"]["lesson"]["micro_test_id"], test.id)
        self.assertIsNone(by_subject["history"]["lesson"]["micro_test_id"])

    def test_student_without_profile_gets_empty_list(self):
        other = get_user_model().objects.create_user(
            email="noprofile@example.com", password="testpassword123"
        )
        self.client.force_authenticate(user=other)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])
