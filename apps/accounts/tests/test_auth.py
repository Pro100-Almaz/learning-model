"""Tests for the JWT auth endpoints.

The Google verification call is mocked everywhere — we never hit the
network. We assert the shape of the response, that a fresh user gets
``onboarding_completed=False``, that the refresh endpoint mints a new
access token, and that ``/auth/me/`` returns the current user.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import StudentProfile


GOOGLE_PAYLOAD = {
    "email": "newstudent@example.com",
    "email_verified": True,
    "given_name": "Aigerim",
    "family_name": "Doe",
    "sub": "1234567890",
}


class GoogleAuthViewTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("v1:auth-google")

    def test_happy_path_creates_user_and_returns_tokens(self):
        with patch(
            "apps.accounts.auth_views.google_id_token.verify_oauth2_token",
            return_value=GOOGLE_PAYLOAD,
        ) as mock_verify:
            response = self.client.post(
                self.url, {"id_token": "fake-google-token"}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn("access", body)
        self.assertIn("refresh", body)
        self.assertIn("user", body)
        self.assertTrue(body["access"])
        self.assertTrue(body["refresh"])

        # User created + StudentProfile ensured
        user = get_user_model().objects.get(email="newstudent@example.com")
        self.assertTrue(StudentProfile.objects.filter(user=user).exists())

        # New user must not be onboarded yet
        self.assertEqual(body["user"]["email"], "newstudent@example.com")
        self.assertFalse(body["user"]["onboarding_completed"])

        mock_verify.assert_called_once()

    def test_existing_user_is_reused(self):
        existing = get_user_model().objects.create_user(
            email="newstudent@example.com",
            password="testpass1234",
            first_name="Existing",
        )
        # Pre-existing profile flagged onboarded
        StudentProfile.objects.create(user=existing, onboarding_completed=True)

        with patch(
            "apps.accounts.auth_views.google_id_token.verify_oauth2_token",
            return_value=GOOGLE_PAYLOAD,
        ):
            response = self.client.post(
                self.url, {"id_token": "fake-google-token"}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        # Same user (no duplicate)
        self.assertEqual(
            get_user_model().objects
            .filter(email="newstudent@example.com").count(),
            1,
        )
        self.assertEqual(body["user"]["id"], existing.id)
        self.assertTrue(body["user"]["onboarding_completed"])

    def test_invalid_token_returns_400(self):
        with patch(
            "apps.accounts.auth_views.google_id_token.verify_oauth2_token",
            side_effect=ValueError("bad signature"),
        ):
            response = self.client.post(
                self.url, {"id_token": "bogus"}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            get_user_model().objects
            .filter(email=GOOGLE_PAYLOAD["email"]).exists()
        )

    def test_missing_id_token_returns_400(self):
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("id_token", response.json())

    def test_unverified_email_rejected(self):
        payload = dict(GOOGLE_PAYLOAD, email_verified=False)
        with patch(
            "apps.accounts.auth_views.google_id_token.verify_oauth2_token",
            return_value=payload,
        ):
            response = self.client.post(
                self.url, {"id_token": "tok"}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AuthRefreshViewTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("v1:auth-refresh")
        cls.google_url = reverse("v1:auth-google")

    def _obtain_tokens(self):
        with patch(
            "apps.accounts.auth_views.google_id_token.verify_oauth2_token",
            return_value=GOOGLE_PAYLOAD,
        ):
            response = self.client.post(
                self.google_url,
                {"id_token": "fake-google-token"},
                format="json",
            )
        return response.json()

    def test_refresh_returns_new_access(self):
        tokens = self._obtain_tokens()
        response = self.client.post(
            self.url, {"refresh": tokens["refresh"]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn("access", body)
        self.assertTrue(body["access"])

    def test_refresh_rejects_invalid_token(self):
        response = self.client.post(
            self.url, {"refresh": "not-a-real-token"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AuthMeViewTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("v1:auth-me")
        cls.google_url = reverse("v1:auth-google")

    def test_me_requires_auth(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_current_user_via_jwt(self):
        with patch(
            "apps.accounts.auth_views.google_id_token.verify_oauth2_token",
            return_value=GOOGLE_PAYLOAD,
        ):
            login = self.client.post(
                self.google_url,
                {"id_token": "fake-google-token"},
                format="json",
            )
        access = login.json()["access"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["email"], GOOGLE_PAYLOAD["email"])
        self.assertIn("onboarding_completed", body)
        self.assertFalse(body["onboarding_completed"])

    def test_me_returns_current_user_via_force_auth(self):
        user = get_user_model().objects.create_user(
            email="solo@example.com", password="testpass1234",
        )
        self.client.force_authenticate(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["email"], "solo@example.com")
