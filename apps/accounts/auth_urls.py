"""URL routes for the JWT auth surface.

Mounted under ``/api/v1/auth/`` in ``conf/urls.py`` alongside (but not
colliding with) the template's existing knox endpoints from
``apps.users.urls``. Routes:

- ``POST /api/v1/auth/google/``  exchange Google id_token for JWTs
- ``POST /api/v1/auth/refresh/`` rotate access token via simplejwt
- ``GET  /api/v1/auth/me/``      current user (onboarding flag included)
"""

from django.urls import path

from .auth_views import AuthMeView, GoogleAuthView, TokenRefreshView

urlpatterns = [
    path("google/", GoogleAuthView.as_view(), name="auth-google"),
    path("refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("me/", AuthMeView.as_view(), name="auth-me"),
]
