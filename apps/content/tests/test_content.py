"""Tests for the content app endpoints.

Aligned with the mobile-reworked contract on the `endpoints` branch:
- GET /api/v1/classes/{class_grade_id}/ : modules for a class grade
- GET /api/v1/modules/{module_id}/      : lessons for a module (flat list)
- GET /api/v1/lessons/{lesson_id}/      : full lesson detail + micro_test_id
- Auth enforcement
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.assessments.models import Test as AssessmentTest
from apps.content.models import ClassGrade, Lesson, Module, Subject


class ModuleListTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="student@example.com",
            password="testpassword123",
        )
        cls.subject = Subject.objects.create(
            name="Профильная математика", slug="profile_math"
        )
        cls.grade = ClassGrade.objects.create(grade=11, subject=cls.subject)
        cls.url = reverse(
            "v1:classes:module-list", kwargs={"class_grade_id": cls.grade.id}
        )
        cls.module_a = Module.objects.create(
            title="Алгебра",
            slug="algebra",
            order=1,
            class_grade=cls.grade,
        )
        cls.module_b = Module.objects.create(
            title="Геометрия",
            slug="geometry",
            order=2,
            class_grade=cls.grade,
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
        expected_keys = {
            "id",
            "title",
            "slug",
            "order",
            "class_grade_id",
            "lessons",
            "description",
            "done",
            "progress",
        }
        self.assertEqual(set(first.keys()), expected_keys)

        # Ordered by `order`; `lessons` is the denormalized lesson count.
        self.assertEqual(first["slug"], "algebra")
        self.assertEqual(first["lessons"], 2)
        self.assertEqual(response.data[1]["slug"], "geometry")
        self.assertEqual(response.data[1]["lessons"], 0)

    def test_list_filters_by_class_grade(self):
        # A module in a different subject + grade that must be filtered out.
        other_subject = Subject.objects.create(
            name="Мат. грамотность", slug="math_literacy"
        )
        other_grade = ClassGrade.objects.create(grade=10, subject=other_subject)
        Module.objects.create(
            title="Функции",
            slug="functions",
            order=3,
            class_grade=other_grade,
        )
        self.client.force_authenticate(user=self.user)

        # This grade returns only its own modules.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slugs = {row["slug"] for row in response.data}
        self.assertEqual(slugs, {"algebra", "geometry"})

        # The other grade returns only the grade-10 module.
        other_url = reverse(
            "v1:classes:module-list", kwargs={"class_grade_id": other_grade.id}
        )
        response = self.client.get(other_url)
        self.assertEqual([r["slug"] for r in response.data], ["functions"])


class ModuleLessonsListTests(APITestCase):
    """GET /api/v1/modules/{module_id}/ returns the module's lessons."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="student@example.com",
            password="testpassword123",
        )
        cls.subject = Subject.objects.create(
            name="Профильная математика", slug="profile_math"
        )
        cls.grade = ClassGrade.objects.create(grade=11, subject=cls.subject)
        cls.module = Module.objects.create(
            title="Алгебра",
            slug="algebra",
            order=1,
            class_grade=cls.grade,
        )
        cls.lesson_first = Lesson.objects.create(
            module=cls.module,
            title="Логарифмы",
            video_url="https://youtu.be/abc",
            duration_sec=300,
            order=1,
        )
        cls.lesson_second = Lesson.objects.create(
            module=cls.module,
            title="Степени",
            video_url="https://youtu.be/def",
            duration_sec=420,
            order=2,
        )

    def test_returns_lessons_for_module(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("v1:modules:module-detail", kwargs={"module_id": self.module.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 2)

        for lesson in response.data:
            self.assertEqual(
                set(lesson.keys()),
                {"id", "title", "duration_sec", "module_id", "status", "progress"},
            )

        # Ordered by `order`.
        self.assertEqual(response.data[0]["id"], self.lesson_first.id)
        self.assertEqual(response.data[1]["id"], self.lesson_second.id)
        self.assertEqual(response.data[0]["module_id"], self.module.id)

    def test_unknown_module_returns_empty_list(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("v1:modules:module-detail", kwargs={"module_id": 99999})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_requires_auth(self):
        url = reverse("v1:modules:module-detail", kwargs={"module_id": self.module.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class LessonDetailTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="student@example.com",
            password="testpassword123",
        )
        cls.subject = Subject.objects.create(
            name="Профильная математика", slug="profile_math"
        )
        cls.grade = ClassGrade.objects.create(grade=11, subject=cls.subject)
        cls.module = Module.objects.create(
            title="Алгебра",
            slug="algebra",
            order=1,
            class_grade=cls.grade,
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
        url = reverse("v1:lessons:lesson-detail", kwargs={"lesson_id": self.lesson.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()),
            {
                "id",
                "title",
                "duration_sec",
                "module_id",
                "status",
                "progress",
                "description",
                "video_url",
                "video_provider",
                "micro_test_id",
                "tag",
            },
        )
        self.assertEqual(response.data["micro_test_id"], self.micro_test.id)
        self.assertEqual(response.data["video_provider"], "youtube")

    def test_lesson_detail_micro_test_id_null_when_absent(self):
        self.client.force_authenticate(user=self.user)
        url = reverse(
            "v1:lessons:lesson-detail", kwargs={"lesson_id": self.lesson_no_test.id}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["micro_test_id"])

    def test_lesson_detail_404_shape(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("v1:lessons:lesson-detail", kwargs={"lesson_id": 99999})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("detail", response.data)

    def test_lesson_detail_requires_auth(self):
        url = reverse("v1:lessons:lesson-detail", kwargs={"lesson_id": self.lesson.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
