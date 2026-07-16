"""Tests for the grant calculator service + endpoint (T-303)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import ExpectedScore, StudentProfile
from apps.assessments.models import Test, TestAttempt
from apps.careers.models import GrantThreshold, Specialty, University
from apps.content.models import Subject

User = get_user_model()


def _subject(slug: str, name: str = "Subject") -> Subject:
    subject, _ = Subject.objects.get_or_create(slug=slug, defaults={"name": name})
    return subject


def _make_student(email="student@example.com", target_score=None):
    user = User.objects.create_user(email=email, password="passw0rd!!")
    profile = StudentProfile.objects.create(user=user, target_score=target_score)
    return user, profile


def _make_mock_attempt(user, *, score: float, completed: bool = True):
    test = Test.objects.create(type="mock", title="Mock 1")
    attempt = TestAttempt.objects.create(
        student=user,
        test=test,
        score=score,
        is_completed=completed,
        finished_at=timezone.now() if completed else None,
    )
    return attempt


class GrantCalculate409Tests(APITestCase):
    """409 when the student has no completed math mock attempt yet."""

    def setUp(self):
        self.url = reverse("v1:careers:calculate")
        self.user, _ = _make_student()
        self.client.force_authenticate(user=self.user)

    def test_no_completed_mock_returns_409(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get("code"), "no_completed_mock")
        self.assertIn("detail", response.data)

    def test_incomplete_mock_attempt_still_409(self):
        _make_mock_attempt(self.user, score=80.0, completed=False)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get("code"), "no_completed_mock")


class GrantCalculateHappyPathTests(APITestCase):
    """200: returns the GrantCalcResult shape with qualifying grants."""

    def setUp(self):
        self.url = reverse("v1:careers:calculate")
        self.user, self.profile = _make_student()
        self.client.force_authenticate(user=self.user)

        ExpectedScore.objects.create(profile=self.profile, subject=_subject("history-of-kazakhstan"), score=20)
        ExpectedScore.objects.create(profile=self.profile, subject=_subject("reading-literacy"), score=15)

        _make_mock_attempt(self.user, score=40.0)  # predicted = 40 + 20 + 15 = 75

        uni = University.objects.create(name="KBTU", city="Алматы", code="KBTU")
        sp_ok = Specialty.objects.create(university=uni, name="IS", code="6B06")
        GrantThreshold.objects.create(specialty=sp_ok, year=2024, min_score=70)

        sp_no = Specialty.objects.create(university=uni, name="Medicine", code="6B10")
        GrantThreshold.objects.create(specialty=sp_no, year=2024, min_score=130)

    def test_returns_grant_calc_result_shape(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data

        self.assertEqual(set(data.keys()), {
            "predicted_score",
            "math_score",
            "other_subjects_total",
            "qualifying_grants",
            "goal",
        })

        self.assertAlmostEqual(float(data["math_score"]), 40.0)
        self.assertAlmostEqual(float(data["other_subjects_total"]), 35.0)
        self.assertAlmostEqual(float(data["predicted_score"]), 75.0)
        self.assertIsNone(data["goal"])

        grants = data["qualifying_grants"]
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]["specialty_name"], "IS")
        self.assertEqual(grants[0]["min_score"], 70)
        self.assertEqual(grants[0]["margin"], 5)
        self.assertEqual(grants[0]["university_name"], "KBTU")


class GrantCalculateLatestThresholdTests(APITestCase):
    """The qualifying calculation uses the newest year's threshold per specialty."""

    def setUp(self):
        self.url = reverse("v1:careers:calculate")
        self.user, self.profile = _make_student()
        self.client.force_authenticate(user=self.user)
        _make_mock_attempt(self.user, score=100.0)

        uni = University.objects.create(name="ENU", city="Астана", code="ENU")
        sp = Specialty.objects.create(university=uni, name="SE", code="6B061")
        # Old year: very low threshold; new year: high threshold > predicted.
        GrantThreshold.objects.create(specialty=sp, year=2020, min_score=50)
        GrantThreshold.objects.create(specialty=sp, year=2024, min_score=120)

    def test_latest_threshold_excludes_specialty(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # predicted = 100 < 120 (latest) — must NOT qualify on old 50 threshold.
        self.assertEqual(response.data["qualifying_grants"], [])

    def test_latest_threshold_includes_specialty_when_high_predicted(self):
        # Add expected scores to boost predicted above latest threshold (120).
        ExpectedScore.objects.create(profile=self.profile, subject=_subject("x"), score=25)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        grants = response.data["qualifying_grants"]
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]["min_score"], 120)
        self.assertEqual(grants[0]["margin"], 5)


class GrantCalculateGoalTests(APITestCase):
    """When target_score is set we get a GoalTracker (gap + advice)."""

    def setUp(self):
        self.url = reverse("v1:careers:calculate")
        self.user, self.profile = _make_student(target_score=140)
        self.client.force_authenticate(user=self.user)
        ExpectedScore.objects.create(profile=self.profile, subject=_subject("x"), score=30)
        _make_mock_attempt(self.user, score=90.0)  # predicted = 120

    def test_goal_present_when_target_set(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        goal = response.data["goal"]
        self.assertIsNotNone(goal)
        self.assertEqual(goal["target_score"], 140)
        self.assertAlmostEqual(float(goal["predicted_score"]), 120.0)
        self.assertAlmostEqual(float(goal["gap"]), 20.0)
        self.assertIn("advice", goal)
        self.assertIn("weakest_tag", goal)
        self.assertIn("20", goal["advice"])
