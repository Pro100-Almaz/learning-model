"""Thin auth surface backed by Clerk.

The user-facing login UI is hosted by Clerk. Our backend only needs:

* ``GET  /api/v1/auth/me/``               — return the local user the inbound
  Clerk JWT resolves to (with onboarding_completed). Authenticated by
  ``ClerkAuthentication``.
* ``POST /api/v1/auth/clerk-webhook/``    — Clerk posts user.created /
  user.updated / user.deleted here so we keep the local row in sync without
  waiting for the next request from that user. Public; svix signature
  verification is a TODO (see webhooks.py).
"""

from django.urls import path

from apps.accounts.views import AuthMeView
from apps.accounts.webhooks import ClerkWebhookView

urlpatterns = [
    path("me/", AuthMeView.as_view(), name="auth-me"),
    path("clerk-webhook/", ClerkWebhookView.as_view(), name="auth-clerk-webhook"),
]
