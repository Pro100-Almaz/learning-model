"""DRF authentication backed by Clerk.

Verifies the inbound ``Authorization: Bearer <jwt>`` against Clerk's JWKS
(cached for ~10 minutes), maps the ``sub`` claim to a local ``CustomUser``
(creating one lazily on first sign-in), and returns the user to DRF.

Configuration (env-driven via ``conf/settings.py``):

  CLERK_JWKS_URL    https://<your-instance>.clerk.accounts.dev/.well-known/jwks.json
  CLERK_ISSUER      https://<your-instance>.clerk.accounts.dev
  CLERK_AUDIENCE    (optional) expected `aud` claim, e.g. your API identifier
  CLERK_SECRET_KEY  Clerk secret (used for webhook svix verification + admin API)

Behaviour when ``CLERK_JWKS_URL`` is empty (local dev / tests without Clerk):
the authentication returns ``None`` — i.e. it's a no-op, falling through to
the next class or to AnonymousUser. This keeps tests that use
``APIClient.force_authenticate(user=...)`` working without ever calling out
to Clerk.
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Optional

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

logger = logging.getLogger("apps.accounts")


# Cache JWKS for this long; Clerk rotates keys infrequently.
_JWKS_TTL_SECONDS = 600
_JWKS_CACHE: dict[str, tuple[float, dict]] = {}
_JWKS_LOCK = Lock()


def _fetch_jwks(url: str) -> dict:
    """Fetch (and cache) Clerk's JSON Web Key Set."""
    now = time.monotonic()
    with _JWKS_LOCK:
        cached = _JWKS_CACHE.get(url)
        if cached and now - cached[0] < _JWKS_TTL_SECONDS:
            return cached[1]

    # Imported here so module import doesn't tax tests that never authenticate.
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network failure shouldn't 500 the whole API
        logger.warning("ClerkAuthentication: JWKS fetch failed: %s", exc)
        raise AuthenticationFailed({"detail": "auth provider unavailable", "code": "auth_failed"}) from exc

    with _JWKS_LOCK:
        _JWKS_CACHE[url] = (now, data)
    return data


def _key_for_kid(jwks: dict, kid: str):
    """Pick the matching RS256 public key from the JWKS by ``kid``."""
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            # PyJWT can convert a JWK dict to an RSA key.
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
    return None


class ClerkAuthentication(BaseAuthentication):
    """Verify a Clerk session JWT and return the matching local user."""

    keyword = "Bearer"

    def authenticate(self, request):
        jwks_url = getattr(settings, "CLERK_JWKS_URL", "") or ""
        if not jwks_url:
            # Clerk not configured — no-op (dev / tests). DRF will fall back
            # to AnonymousUser; force_authenticate keeps tests working.
            return None

        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != self.keyword.lower().encode():
            return None  # No Bearer header → try next auth class / anonymous.
        if len(auth) != 2:
            raise AuthenticationFailed({"detail": "invalid auth header", "code": "auth_failed"})

        token = auth[1].decode("utf-8")

        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationFailed({"detail": "invalid token", "code": "auth_failed"}) from exc

        kid = unverified_header.get("kid")
        if not kid:
            raise AuthenticationFailed({"detail": "token missing kid", "code": "auth_failed"})

        jwks = _fetch_jwks(jwks_url)
        key = _key_for_kid(jwks, kid)
        if key is None:
            raise AuthenticationFailed({"detail": "unknown token signing key", "code": "auth_failed"})

        issuer = getattr(settings, "CLERK_ISSUER", "") or None
        audience = getattr(settings, "CLERK_AUDIENCE", "") or None

        try:
            payload = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=issuer,
                audience=audience,
                options={
                    "require": ["exp", "iat", "sub"],
                    "verify_aud": bool(audience),
                    "verify_iss": bool(issuer),
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationFailed({"detail": "token expired", "code": "auth_failed"}) from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationFailed({"detail": "token invalid", "code": "auth_failed"}) from exc

        user = self._upsert_user(payload)
        if user is None:
            raise AuthenticationFailed({"detail": "could not resolve user", "code": "auth_failed"})
        if not user.is_active:
            raise AuthenticationFailed({"detail": "user inactive", "code": "auth_failed"})

        return (user, payload)

    def authenticate_header(self, request) -> str:
        return self.keyword

    # ------------------------------------------------------------------
    # Local-user upsert
    # ------------------------------------------------------------------

    def _upsert_user(self, payload: dict) -> Optional["CustomUser"]:  # noqa: F821
        """Resolve / create the local user the Clerk subject maps to.

        Match rules (in order):
          1. by ``clerk_user_id == payload["sub"]``
          2. by ``email == payload["email"]`` (link legacy / seeded users)
          3. otherwise create a brand-new local user

        Email is kept in sync from the token's claims on every request so an
        email-change in Clerk propagates to the local row on next call.
        """
        sub = payload.get("sub")
        if not sub:
            return None

        User = get_user_model()
        email = (
            payload.get("email")
            or payload.get("primary_email_address")
            or payload.get("email_address")
            or ""
        ).strip().lower()
        first_name = payload.get("given_name") or payload.get("first_name") or ""
        last_name = payload.get("family_name") or payload.get("last_name") or ""

        with transaction.atomic():
            user = User.objects.filter(clerk_user_id=sub).first()
            if user is None and email:
                user = User.objects.filter(email__iexact=email).first()
                if user is not None and not user.clerk_user_id:
                    user.clerk_user_id = sub
            if user is None:
                if not email:
                    # No way to construct a unique local row.
                    return None
                user = User(email=email, clerk_user_id=sub, is_active=True)
                user.set_unusable_password()

            # Sync the lightweight claims so the local row stays fresh.
            dirty: list[str] = []
            if email and user.email.lower() != email:
                user.email = email
                dirty.append("email")
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                dirty.append("first_name")
            if last_name and user.last_name != last_name:
                user.last_name = last_name
                dirty.append("last_name")
            if user.clerk_user_id != sub:
                user.clerk_user_id = sub
                dirty.append("clerk_user_id")

            if user.pk is None:
                user.save()
            elif dirty:
                user.save(update_fields=dirty)

        return user


# Tell drf-spectacular how to document the Clerk-protected endpoints.
try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension

    class _ClerkAuthScheme(OpenApiAuthenticationExtension):
        target_class = "apps.accounts.authentication.ClerkAuthentication"
        name = "bearerAuth"

        def get_security_definition(self, auto_schema):
            return {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Clerk session JWT (RS256). Verified via JWKS.",
            }
except Exception:  # pragma: no cover - defensive
    pass
