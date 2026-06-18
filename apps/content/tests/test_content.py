"""Tests for the content app endpoints (T-101).

Covers:
- GET /api/v1/modules/        : list shape + lesson_count
- GET /api/v1/modules/{id}/   : detail with nested lessons[] + completion
- GET /api/v1/lessons/{id}/   : detail with micro_test_id resolved
- 404 envelope on missing lesson
- Auth enforcement
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.assessments.models import Test as AssessmentTest
from apps.assessments.models import TestAttempt as AssessmentAttempt
from apps.content.models import Lesson, Module


class ModuleListTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="student@example.com",
            password="testpassword123",
        )
        cls.url = reverse("v1:modules:module-list")
        cls.module_a = Module.objects.create(
            title="Алгебра",
            slug="algebra",
            order=1,
            subject="profile_math",
        )
        cls.module_b = Module.objects.create(
            title="Геометрия",
            slug="geometry",
            order=2,
            subject="profile_math",
        )
        Lesson.objects.create(
            module=cls.module_a,
            title="Логарифмы",
            video_url="https://youtu.be/abc",
            duration_sec=300,
            order=1,
        )
        Lesson.objects.create(
            module=cls.module_a,
            title="Степени",
            video_url="https://youtu.be/def",
            duration_sec=420,
            order=2,
        )

    def test_list_requires_auth(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_modules_with_lesson_count(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 2)

        first = response.data[0]
        expected_keys = {"id", "title", "slug", "order", "subject", "lesson_count"}
        self.assertEqual(set(first.keys()), expected_keys)

        # Ordered by `order`
        self.assertEqual(first["slug"], "algebra")
        self.assertEqual(first["lesson_count"], 2)
        self.assertEqual(response.data[1]["slug"], "geometry")
        self.assertEqual(response.data[1]["lesson_count"], 0)


class ModuleDetailTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="student@example.com",
            password="testpassword123",
        )
        cls.module = Module.objects.create(
            title="Алгебра",
            slug="algebra",
            order=1,
            subject="profile_math",
        )
        cls.lesson_done = Lesson.objects.create(
            module=cls.module,
            title="Логарифмы",
            video_url="https://youtu.be/abc",
            duration_sec=300,
            order=1,
        )
        cls.lesson_pending = Lesson.objects.create(
            module=cls.module,
            title="Степени",
            video_url="https://youtu.be/def",
            duration_sec=420,
            order=2,
        )
        # Micro-test on lesson_done that the student already completed
        cls.micro_test = AssessmentTest.objects.create(
            type="micro",
            title="Микро тест: Логарифмы",
            lesson=cls.lesson_done,
        )
        AssessmentAttempt.objects.create(
            student=cls.user,
            test=cls.micro_test,
            is_completed=True,
        )

    def test_detail_returns_module_with_lessons(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("v1:modules:module-detail", kwargs={"id": self.module.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_keys = {
            "id",
            "title",
            "slug",
            "order",
            "subject",
            "lesson_count",
            "lessons",
        }
        self.assertEqual(set(response.data.keys()), expected_keys)
        self.assertEqual(response.data["lesson_count"], 2)

        lessons = response.data["lessons"]
        self.assertEqual(len(lessons), 2)
        for lesson in lessons:
            self.assertEqual(
                set(lesson.keys()),
                {"id", "title", "order", "duration_sec", "completed"},
            )

        # lesson_done is completed; lesson_pending is not
        by_id = {item["id"]: item for item in lessons}
        self.assertTrue(by_id[self.lesson_done.id]["completed"])
        self.assertFalse(by_id[self.lesson_pending.id]["completed"])

    def test_detail_completion_isolated_per_user(self):
        other = get_user_model().objects.create_user(
            email="other@example.com",
            password="testpassword123",
        )
        self.client.force_authenticate(user=other)
        url = reverse("v1:modules:module-detail", kwargs={"id": self.module.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for lesson in response.data["lessons"]:
            self.assertFalse(lesson["completed"])

    def test_detail_missing_module_404(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("v1:modules:module-detail", kwargs={"id": 99999})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("detail", response.data)


class LessonDetailTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="student@example.com",
            password="testpassword123",
        )
        cls.module = Module.objects.create(
            title="Алгебра",
            slug="algebra",
            order=1,
            subject="profile_math",
        )
        cls.lesson = Lesson.objects.create(
            module=cls.module,
            title="Логарифмы",
            description="Свойства логарифмов",
            video_url="https://youtu.be/abc",
            video_provider="youtube",
            duration_sec=300,
            order=1,
        )
        cls.lesson_no_test = Lesson.objects.create(
            module=cls.module,
            title="Степени",
            video_url="https://youtu.be/def",
            duration_sec=420,
            order=2,
        )
        cls.micro_test = AssessmentTest.objects.create(
            type="micro",
            title="Микро тест",
            lesson=cls.lesson,
        )
        # A mock test on the same lesson must NOT be returned as micro_test_id
        AssessmentTest.objects.create(
            type="mock",
            title="Mock",
            lesson=cls.lesson,
        )

    def test_lesson_detail_returns_micro_test_id(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("v1:lessons:lesson-detail", kwargs={"id": self.lesson.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()),
            {
                "id",
                "title",
                "description",
                "video_url",
                "video_provider",
                "duration_sec",
                "micro_test_id",
            },
        )
        self.assertEqual(response.data["micro_test_id"], self.micro_test.id)
        self.assertEqual(response.data["video_provider"], "youtube")

    def test_lesson_detail_micro_test_id_null_when_absent(self):
        self.client.force_authenticate(user=self.user)
        url = reverse(
            "v1:lessons:lesson-detail", kwargs={"id": self.lesson_no_test.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["micro_test_id"])

    def test_lesson_detail_404_shape(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("v1:lessons:lesson-detail", kwargs={"id": 99999})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        # Error envelope: contract says { "detail": "...", "code": "..." } but
        # DRF's default 404 ships at least "detail" — that's what's wired here.
        self.assertIn("detail", response.data)

    def test_lesson_detail_requires_auth(self):
        url = reverse("v1:lessons:lesson-detail", kwargs={"id": self.lesson.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
