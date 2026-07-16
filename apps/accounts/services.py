"""Business logic for the accounts app.

Pure-function helpers used by views. Keeping logic here lets us unit-test it
without dragging in DRF request/response objects.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from apps.accounts.models import ExpectedScore, StudentProfile


def ensure_profile(user) -> StudentProfile:
    """Return the user's StudentProfile, creating one on first access.

    The User model lives in ``apps.users``; profile lives here so that other
    onboarding-only fields stay isolated from auth. We auto-create on first
    GET so the frontend can render an empty onboarding state right after
    Google sign-in.
    """
    profile, _created = StudentProfile.objects.get_or_create(user=user)
    return profile


def upsert_expected_scores(profile: StudentProfile, items) -> None:
    """Replace the profile's expected scores with the provided list.

    ``items`` is an iterable of ``{"subject": Subject, "score": int}`` dicts,
    where ``subject`` is a resolved ``content.Subject`` instance (the
    serializer maps the incoming slug string to it). Subjects not present in
    the new list are removed so the API behaves like a PUT for this nested
    collection (the parent request is still PATCH).
    """
    seen_subjects: set = set()
    with transaction.atomic():
        for entry in items:
            subject = entry["subject"]
            score = entry["score"]
            seen_subjects.add(subject.pk)
            ExpectedScore.objects.update_or_create(
                profile=profile,
                subject=subject,
                defaults={"score": score},
            )
        if seen_subjects:
            profile.expected_scores.exclude(subject__in=seen_subjects).delete()
        else:
            profile.expected_scores.all().delete()


def complete_onboarding_if_ready(profile: StudentProfile) -> bool:
    """Flip ``onboarding_completed`` to True once the minimum data is present.

    Onboarding is considered complete when the student has chosen a target
    university, specialty, target score and has logged at least one expected
    score for the other subjects we need for the grant predictor.
    """
    if profile.onboarding_completed:
        return False

    # ``other_subjects`` holds Subject slugs (see settings.ENT_CONFIG).
    other_subjects = settings.ENT_CONFIG.get("other_subjects", [])
    has_expected = profile.expected_scores.exists()
    if other_subjects:
        has_expected = profile.expected_scores.filter(
            subject__slug__in=other_subjects
        ).exists()

    ready = bool(
        profile.target_university_id
        and profile.target_specialty_id
        and profile.target_score
        and has_expected
    )
    if ready:
        profile.onboarding_completed = True
        profile.save(update_fields=["onboarding_completed"])
        return True
    return False
