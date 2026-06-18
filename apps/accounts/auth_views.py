"""JWT auth endpoints for the ENT prep platform.

Three endpoints exposed under ``/api/v1/auth/``:

* ``POST /api/v1/auth/google/`` — exchange a Google ``id_token`` for app
  JWTs. Public. Verifies the token via ``google.oauth2.id_token``,
  upserts a ``CustomUser`` keyed by email, ensures a ``StudentProfile``
  exists, then returns ``{access, refresh, user}``.
* ``POST /api/v1/auth/refresh/`` — wraps simplejwt's ``TokenRefreshView``.
  Re-exported from this module so the urlconf has a single import site.
* ``GET /api/v1/auth/me/`` — current user payload. Provided by
  ``apps.accounts.views.AuthMeView``; re-exported here for parity with
  the urlconf import block.

Business logic (profile creation) lives in
``apps.accounts.services.ensure_profile``; this module only handles the
HTTP/JWT plumbing.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from drf_spectacular.utils import extend_schema
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts import services
from apps.accounts.serializers import AuthUserSerializer
from apps.accounts.views import AuthMeView

from .auth_serializers import AuthTokensSerializer, GoogleAuthRequestSerializer

logger = logging.getLogger(__name__)

# Re-export so the urlconf can import everything from a single module.
__all__ = ["GoogleAuthView", "TokenRefreshView", "AuthMeView"]


class GoogleAuthView(APIView):
    """Exchange a Google ``id_token`` for app JWT tokens.

    Flow:
      1. Validate request body (``id_token`` required).
      2. Verify the id_token against Google's signing keys using the
         configured OAuth client id as audience.
      3. ``get_or_create`` a ``CustomUser`` keyed by the verified email.
      4. ``ensure_profile`` so the user always has a StudentProfile row
         (onboarding_completed defaults to False).
      5. Issue an access/refresh pair via simplejwt and return the
         openapi ``AuthTokens`` shape.

    Throttle scope ``"auth"`` is applied (10/minute) to limit token
    verification load and brute-force attempts.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "auth"
    serializer_class = AuthTokensSerializer

    @extend_schema(
        request=GoogleAuthRequestSerializer,
        responses=AuthTokensSerializer,
    )
    def post(self, request: Request) -> Response:
        serializer = GoogleAuthRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["id_token"]

        client_id = getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", None) or (
            settings.SOCIALACCOUNT_PROVIDERS.get("google", {})
            .get("APP", {})
            .get("client_id")
        )

        try:
            payload = google_id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                client_id or None,
            )
        except ValueError as exc:
            logger.warning("Google id_token verification failed: %s", exc)
            return Response(
                {"detail": "Invalid Google id_token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = (payload.get("email") or "").lower().strip()
        if not email:
            return Response(
                {"detail": "Google token did not include an email."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if payload.get("email_verified") is False:
            return Response(
                {"detail": "Google email is not verified."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        first_name = payload.get("given_name") or ""
        last_name = payload.get("family_name") or ""

        User = get_user_model()
        with transaction.atomic():
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_active": True,
                },
            )
            if created:
                # Social-only accounts have no usable password.
                user.set_unusable_password()
                # Persist any names we just got from Google.
                user.save(update_fields=["password"])
            services.ensure_profile(user)

        # Refresh the user with the related profile so the serializer
        # reflects the freshly-ensured onboarding flag.
        user = (
            User.objects.select_related("profile").get(pk=user.pk)
        )

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": AuthUserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )
