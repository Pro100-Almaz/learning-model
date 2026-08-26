"""Clerk webhook receiver — keeps local users in sync with Clerk events.

Mounted at ``POST /api/v1/auth/clerk-webhook/``. Clerk POSTs JSON events
(``user.created``, ``user.updated``, ``user.deleted``, etc.) signed via Svix.

The endpoint is unauthenticated by necessity -- Clerk holds no session -- and it
creates users, rewrites their email, and deactivates accounts. The Svix
signature IS its authentication; without it, anybody who learns the URL can
POST ``user.deleted`` and lock a student out, or ``user.updated`` and move an
account's email to one they control. So a missing secret is refused rather than
waved through, outside DEBUG.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.svix import SignatureError, verify

logger = logging.getLogger("apps.accounts")


class ClerkWebhookSerializer(serializers.Serializer):
    """Just the envelope shape; full validation lives in the handler."""

    type = serializers.CharField()
    data = serializers.DictField()


class ClerkWebhookView(APIView):
    """Public endpoint Clerk posts user-lifecycle events to."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    serializer_class = ClerkWebhookSerializer

    @extend_schema(request=ClerkWebhookSerializer, responses={200: None})
    def post(self, request: Request) -> Response:
        denied = self._authenticate(request)
        if denied is not None:
            return denied

        serializer = ClerkWebhookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event_type = serializer.validated_data["type"]
        data = serializer.validated_data["data"] or {}

        if event_type in ("user.created", "user.updated"):
            self._upsert(data)
        elif event_type == "user.deleted":
            self._delete(data)
        else:
            logger.info("ClerkWebhook: ignoring event type %s", event_type)

        return Response(status=status.HTTP_200_OK, data={"received": True})

    @staticmethod
    def _authenticate(request: Request) -> Response | None:
        """None to proceed, or the response to return instead.

        Verifies against ``request.body`` -- the raw bytes -- not
        ``request.data``. The signature covers what was sent, and a
        re-serialized dict is not that.
        """
        secret = getattr(settings, "CLERK_WEBHOOK_SECRET", "")
        if not secret:
            if not settings.DEBUG:
                # 503, not 403: nothing is wrong with the REQUEST. The server is
                # misconfigured, and saying so is what gets the secret set
                # instead of someone hunting a signature bug that isn't there.
                logger.error(
                    "ClerkWebhook: CLERK_WEBHOOK_SECRET is not set; refusing. "
                    "Set it from the Clerk dashboard (Webhooks -> Signing Secret)."
                )
                return Response(
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    data={"detail": "webhook signing secret is not configured",
                          "code": "webhook_not_configured"},
                )
            logger.warning(
                "ClerkWebhook: no CLERK_WEBHOOK_SECRET and DEBUG is on; "
                "accepting an UNVERIFIED webhook. Never run this way in production."
            )
            return None

        try:
            verify(secret, request.headers, request.body)
        except SignatureError as error:
            # The reason is logged, not returned. Telling a caller whether the
            # timestamp or the signature failed helps them iterate towards a
            # forgery; the sender holding the secret never needs to know.
            logger.warning("ClerkWebhook: rejected unverified request: %s", error)
            return Response(
                status=status.HTTP_401_UNAUTHORIZED,
                data={"detail": "invalid webhook signature",
                      "code": "invalid_signature"},
            )
        return None

    @staticmethod
    def _primary_email(data: dict) -> str:
        # Clerk shape: {"email_addresses": [{"email_address": "..."}], "primary_email_address_id": "..."}
        emails = data.get("email_addresses") or []
        primary_id = data.get("primary_email_address_id")
        for entry in emails:
            if entry.get("id") == primary_id and entry.get("email_address"):
                return entry["email_address"].strip().lower()
        if emails and emails[0].get("email_address"):
            return emails[0]["email_address"].strip().lower()
        return ""

    @transaction.atomic
    def _upsert(self, data: dict) -> None:
        sub = data.get("id")
        if not sub:
            return
        email = self._primary_email(data)
        first = (data.get("first_name") or "").strip()
        last = (data.get("last_name") or "").strip()

        User = get_user_model()
        user = User.objects.filter(clerk_user_id=sub).first()
        if user is None and email:
            user = User.objects.filter(email__iexact=email).first()
        if user is None:
            if not email:
                logger.warning("ClerkWebhook: user.created with no email; skipping")
                return
            user = User(email=email, clerk_user_id=sub, is_active=True)
            user.set_unusable_password()
            user.first_name = first
            user.last_name = last
            user.save()
            return

        dirty: list[str] = []
        if user.clerk_user_id != sub:
            user.clerk_user_id = sub
            dirty.append("clerk_user_id")
        if email and user.email.lower() != email:
            user.email = email
            dirty.append("email")
        if user.first_name != first:
            user.first_name = first
            dirty.append("first_name")
        if user.last_name != last:
            user.last_name = last
            dirty.append("last_name")
        if dirty:
            user.save(update_fields=dirty)

    def _delete(self, data: dict) -> None:
        sub = data.get("id")
        if not sub:
            return
        User = get_user_model()
        # Soft-delete: mark inactive rather than removing rows so attempt
        # history, roadmap items, gamification state stay intact.
        User.objects.filter(clerk_user_id=sub).update(is_active=False)
