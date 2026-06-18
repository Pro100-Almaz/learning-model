"""Happy-path tests for the accounts endpoints."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import ExpectedScore, StudentProfile
from apps.careers.models import GrantThreshold, Specialty, University


class ProfileViewTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="student@example.com",
            password="testpass1234",
            first_name="Aigerim",
        )
        cls.url = reverse("v1:accounts:profile")
        cls.uni = University.objects.create(
            name="KBTU", city="Almaty", code="kbtu"
        )
        cls.spec = Specialty.objects.create(
            university=cls.uni,
            name="Computer Science",
            code="cs",
            required_subjects=["profile_math"],
        )

    def test_get_profile_auto_creates(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(StudentProfile.objects.filter(user=self.user).exists())
        body = response.json()
        self.assertIn("target_university", body)
        self.assertIn("expected_scores", body)
        self.assertFalse(body["onboarding_completed"])
        self.assertEqual(body["expected_scores"], [])

    def test_patch_updates_targets_and_flips_onboarding(self):
        self.client.force_authenticate(self.user)
        payload = {
            "target_university": self.uni.id,
            "target_specialty": self.spec.id,
            "target_score": 120,
            "expected_scores": [
                {"subject": "История Казахстана", "score": 18},
                {"subject": "Грамотность чтения", "score": 17},
            ],
        }
        response = self.client.patch(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["target_university"], self.uni.id)
        self.assertEqual(body["target_specialty"], self.spec.id)
        self.assertEqual(body["target_score"], 120)
        self.assertTrue(body["onboarding_completed"])

        # Expected scores persisted as ExpectedScore rows
        subjects = set(
            ExpectedScore.objects.filter(
                profile__user=self.user
            ).values_list("subject", flat=True)
        )
        self.assertEqual(
            subjects, {"История Казахстана", "Грамотность чтения"}
        )

    def test_patch_partial_does_not_flip_onboarding(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            self.url, {"target_score": 100}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.json()["onboarding_completed"])

    def test_patch_replaces_expected_scores(self):
        self.client.force_authenticate(self.user)
        # seed initial scores
        self.client.patch(
            self.url,
            {
                "expected_scores": [
                    {"subject": "История Казахстана", "score": 18},
                    {"subject": "Грамотность чтения", "score": 17},
                ]
            },
            format="json",
        )
        # send a smaller list — the missing one must be removed
        response = self.client.patch(
            self.url,
            {
                "expected_scores": [
                    {"subject": "История Казахстана", "score": 19},
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subjects = list(
            ExpectedScore.objects.filter(
                profile__user=self.user
            ).values_list("subject", "score")
        )
        self.assertEqual(subjects, [("История Казахстана", 19)])

    def test_get_requires_auth(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class OnboardingOptionsViewTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="opts@example.com", password="testpass1234"
        )
        cls.url = reverse("v1:accounts:onboarding-options")
        cls.uni = University.objects.create(
            name="NU", city="Astana", code="nu"
        )
        cls.spec = Specialty.objects.create(
            university=cls.uni, name="Math", code="math",
            required_subjects=["profile_math"],
        )
        GrantThreshold.objects.create(
            specialty=cls.spec, year=2024, min_score=110
        )

    def test_options_shape(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()

        self.assertIn("universities", body)
        self.assertIn("specialties", body)
        self.assertIn("subjects", body)
        self.assertIsInstance(body["subjects"], list)
        self.assertGreater(len(body["subjects"]), 0)

        uni_row = body["universities"][0]
        self.assertEqual(
            set(uni_row.keys()),
            {"id", "name", "city", "code", "specialties"},
        )
        spec_row = body["specialties"][0]
        self.assertEqual(
            set(spec_row.keys()),
            {"id", "university_id", "name", "code", "latest_threshold"},
        )
        self.assertEqual(spec_row["latest_threshold"], 110)


class AuthMeViewTests(APITestCase):
    """AuthMeView is wired by the OAuth agent, but we can still test it via the serializer surface."""

    def test_auth_user_serializer_defaults_false_without_profile(self):
        from apps.accounts.serializers import AuthUserSerializer

        user = get_user_model().objects.create_user(
            email="solo@example.com", password="testpass1234"
        )
        data = AuthUserSerializer(user).data
        self.assertEqual(data["email"], "solo@example.com")
        self.assertFalse(data["onboarding_completed"])

    def test_auth_user_serializer_reflects_profile(self):
        from apps.accounts.serializers import AuthUserSerializer

        user = get_user_model().objects.create_user(
            email="done@example.com", password="testpass1234"
        )
        StudentProfile.objects.create(user=user, onboarding_completed=True)
        # refresh so the related profile is attached
        user = get_user_model().objects.select_related("profile").get(pk=user.pk)
        data = AuthUserSerializer(user).data
        self.assertTrue(data["onboarding_completed"])
