"""Tests for the friendships endpoints.

The friendships routes carry no url names (see apps/friendships/urls.py), so these
use literal paths under the ``/api/v1/`` mount from conf/urls.py.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import StudentProfile
from apps.friendships.models import Friendship

FRIENDSHIPS = "/api/v1/friendships/"


def friends_url(profile_id: int) -> str:
    return f"/api/v1/friendships/friends/{profile_id}/"


def requests_url(profile_id: int, direction: str = "received") -> str:
    return f"/api/v1/friendships/requests/{profile_id}/?direction={direction}"


class FriendshipFlowTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.alice_user = User.objects.create_user(
            email="alice@example.com", password="testpass1234", first_name="Alice"
        )
        cls.bob_user = User.objects.create_user(
            email="bob@example.com", password="testpass1234", first_name="Bob"
        )
        cls.alice = StudentProfile.objects.create(user=cls.alice_user, username="alice_k")
        cls.bob = StudentProfile.objects.create(user=cls.bob_user, username="bob_t")

    # --- send -----------------------------------------------------------------

    def test_send_request_returns_pending_friendship(self):
        self.client.force_authenticate(self.alice_user)
        response = self.client.post(FRIENDSHIPS, {"receiver_id": self.bob.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertEqual(body["status"], "pending")
        # The mobile client keys rows off these ids and labels them with username.
        self.assertEqual(body["from_profile"], {"id": self.alice.id, "username": "alice_k"})
        self.assertEqual(body["to_profile"], {"id": self.bob.id, "username": "bob_t"})

    def test_send_request_ignores_client_supplied_sender(self):
        """The sender is the authenticated user, never something from the body."""
        self.client.force_authenticate(self.alice_user)
        response = self.client.post(
            FRIENDSHIPS,
            {"receiver_id": self.bob.id, "sender_id": self.bob.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["from_profile"]["id"], self.alice.id)

    def test_send_request_to_self_is_rejected(self):
        self.client.force_authenticate(self.alice_user)
        response = self.client.post(FRIENDSHIPS, {"receiver_id": self.alice.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_send_request_to_unknown_profile_is_404(self):
        self.client.force_authenticate(self.alice_user)
        response = self.client.post(FRIENDSHIPS, {"receiver_id": 99999}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_send_request_when_reverse_pending_accepts_instead(self):
        """Bob asked first; Alice asking back should accept, not open a second row."""
        Friendship.objects.create(from_profile=self.bob, to_profile=self.alice)

        self.client.force_authenticate(self.alice_user)
        response = self.client.post(FRIENDSHIPS, {"receiver_id": self.bob.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(Friendship.objects.count(), 1)

    def test_send_request_when_already_friends_is_rejected(self):
        Friendship.objects.create(
            from_profile=self.alice, to_profile=self.bob, status=Friendship.Status.ACCEPTED
        )
        self.client.force_authenticate(self.alice_user)
        response = self.client.post(FRIENDSHIPS, {"receiver_id": self.bob.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_resending_after_rejection_reopens_the_request(self):
        Friendship.objects.create(
            from_profile=self.alice, to_profile=self.bob, status=Friendship.Status.REJECTED
        )
        self.client.force_authenticate(self.alice_user)
        response = self.client.post(FRIENDSHIPS, {"receiver_id": self.bob.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(Friendship.objects.count(), 1)

    # --- respond --------------------------------------------------------------

    def test_receiver_can_accept(self):
        friendship = Friendship.objects.create(from_profile=self.alice, to_profile=self.bob)

        self.client.force_authenticate(self.bob_user)
        response = self.client.patch(
            FRIENDSHIPS, {"id": friendship.id, "action": "accept"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], "accepted")
        friendship.refresh_from_db()
        self.assertEqual(friendship.status, Friendship.Status.ACCEPTED)

    def test_receiver_can_reject(self):
        friendship = Friendship.objects.create(from_profile=self.alice, to_profile=self.bob)

        self.client.force_authenticate(self.bob_user)
        response = self.client.patch(
            FRIENDSHIPS, {"id": friendship.id, "action": "reject"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], "rejected")

    def test_sender_cannot_accept_own_request(self):
        friendship = Friendship.objects.create(from_profile=self.alice, to_profile=self.bob)

        self.client.force_authenticate(self.alice_user)
        response = self.client.patch(
            FRIENDSHIPS, {"id": friendship.id, "action": "accept"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_responding_to_unknown_request_is_404(self):
        self.client.force_authenticate(self.bob_user)
        response = self.client.patch(
            FRIENDSHIPS, {"id": 99999, "action": "accept"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- cancel ---------------------------------------------------------------

    def test_sender_can_cancel_pending_request(self):
        friendship = Friendship.objects.create(from_profile=self.alice, to_profile=self.bob)

        self.client.force_authenticate(self.alice_user)
        response = self.client.delete(FRIENDSHIPS, {"id": friendship.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Friendship.objects.filter(id=friendship.id).exists())

    def test_receiver_cannot_cancel(self):
        friendship = Friendship.objects.create(from_profile=self.alice, to_profile=self.bob)

        self.client.force_authenticate(self.bob_user)
        response = self.client.delete(FRIENDSHIPS, {"id": friendship.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Friendship.objects.filter(id=friendship.id).exists())

    def test_cannot_cancel_an_accepted_friendship(self):
        friendship = Friendship.objects.create(
            from_profile=self.alice, to_profile=self.bob, status=Friendship.Status.ACCEPTED
        )
        self.client.force_authenticate(self.alice_user)
        response = self.client.delete(FRIENDSHIPS, {"id": friendship.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- lists ----------------------------------------------------------------

    def test_list_friends_returns_the_other_side_only(self):
        Friendship.objects.create(
            from_profile=self.alice, to_profile=self.bob, status=Friendship.Status.ACCEPTED
        )
        self.client.force_authenticate(self.alice_user)
        response = self.client.get(friends_url(self.alice.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), [{"id": self.bob.id, "username": "bob_t"}])

    def test_list_friends_excludes_pending(self):
        Friendship.objects.create(from_profile=self.alice, to_profile=self.bob)
        self.client.force_authenticate(self.alice_user)
        response = self.client.get(friends_url(self.alice.id))
        self.assertEqual(response.json(), [])

    def test_list_friends_never_leaks_private_profile_fields(self):
        self.alice.target_score = 130
        self.alice.save(update_fields=["target_score"])
        Friendship.objects.create(
            from_profile=self.alice, to_profile=self.bob, status=Friendship.Status.ACCEPTED
        )

        self.client.force_authenticate(self.bob_user)
        response = self.client.get(friends_url(self.bob.id))

        self.assertEqual(set(response.json()[0]), {"id", "username"})

    def test_list_received_requests(self):
        Friendship.objects.create(from_profile=self.alice, to_profile=self.bob)

        self.client.force_authenticate(self.bob_user)
        response = self.client.get(requests_url(self.bob.id, "received"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["from_profile"]["id"], self.alice.id)

    def test_list_sent_requests(self):
        Friendship.objects.create(from_profile=self.alice, to_profile=self.bob)

        self.client.force_authenticate(self.alice_user)
        received = self.client.get(requests_url(self.alice.id, "received")).json()
        sent = self.client.get(requests_url(self.alice.id, "sent")).json()

        self.assertEqual(received, [])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["to_profile"]["id"], self.bob.id)

    def test_requests_default_to_received(self):
        Friendship.objects.create(from_profile=self.alice, to_profile=self.bob)
        self.client.force_authenticate(self.bob_user)
        response = self.client.get(f"/api/v1/friendships/requests/{self.bob.id}/")
        self.assertEqual(len(response.json()), 1)

    def test_unknown_direction_is_rejected(self):
        self.client.force_authenticate(self.alice_user)
        response = self.client.get(requests_url(self.alice.id, "sideways"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_endpoints_require_authentication(self):
        self.assertEqual(
            self.client.get(friends_url(self.alice.id)).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )


class ProfileIdExposureTests(APITestCase):
    """GET /profile/ must carry the id the friendships endpoints are keyed by."""

    def test_profile_payload_includes_id_and_username(self):
        user = get_user_model().objects.create_user(
            email="carol@example.com", password="testpass1234", first_name="Carol"
        )
        self.client.force_authenticate(user)
        body = self.client.get("/api/v1/profile/").json()

        self.assertIn("id", body)
        self.assertIn("username", body)
        self.assertEqual(body["id"], StudentProfile.objects.get(user=user).id)
