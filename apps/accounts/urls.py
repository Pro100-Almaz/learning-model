"""URL routes for the accounts app.

Mounted under ``/api/v1/profile/`` in ``conf/urls.py``:
- "" -> GET/PATCH the student's profile
- "onboarding-options/" -> options bundle for the onboarding wizard
"""

from django.urls import path

from apps.accounts.views import OnboardingOptionsView, ProfileView

app_name = "accounts"

urlpatterns = [
    path("", ProfileView.as_view(), name="profile"),
    path(
        "onboarding-options/",
        OnboardingOptionsView.as_view(),
        name="onboarding-options",
    ),
]
