"""Serializers for the JWT auth endpoints.

These shape the I/O for the Google OAuth flow and ``/auth/me/``. The
``AuthUserSerializer`` lives in ``apps.accounts.serializers`` and is reused
here for the embedded ``user`` payload — kept thin on purpose so that the
auth surface stays focused on token plumbing.
"""

from __future__ import annotations

from rest_framework import serializers


class GoogleAuthRequestSerializer(serializers.Serializer):
    """Body for ``POST /api/v1/auth/google/``.

    The frontend obtains the ``id_token`` from Google's JS client and posts
    it here; the server verifies it via ``google.oauth2.id_token``.
    """

    id_token = serializers.CharField(required=True, trim_whitespace=True)


class AuthTokensSerializer(serializers.Serializer):
    """Response for ``POST /api/v1/auth/google/`` — matches openapi
    ``AuthTokens``: ``{access, refresh, user}``.

    The ``user`` field is shaped by ``AuthUserSerializer`` at the view layer
    so the schema generator can introspect it; here we declare it as a
    plain dict field for documentation purposes only.
    """

    access = serializers.CharField()
    refresh = serializers.CharField()
    user = serializers.DictField()
