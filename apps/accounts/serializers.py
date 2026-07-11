"""Serializers for the accounts app.

These shape the User / StudentProfile / ExpectedScore I/O to match
``plan/openapi.yaml`` exactly. snake_case fields only.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.content.models import Subject

from apps.accounts.models import ExpectedScore, StudentProfile


class AuthUserSerializer(serializers.Serializer):
    """Matches the openapi ``User`` schema.

    ``onboarding_completed`` is sourced from the student's profile; if the
    profile does not yet exist (first login before GET /profile/) we default
    to ``False`` so the frontend can show the onboarding wizard.
    """

    id = serializers.IntegerField(read_only=True)
    email = serializers.EmailField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    is_staff = serializers.BooleanField(read_only=True)
    onboarding_completed = serializers.SerializerMethodField()

    def get_onboarding_completed(self, obj) -> bool:
        profile = getattr(obj, "profile", None)
        if profile is None:
            return False
        return bool(profile.onboarding_completed)


class ExpectedScoreSerializer(serializers.ModelSerializer):
    """ExpectedScore I/O — exposes only subject + score per openapi."""

    subject = serializers.SlugRelatedField(
        slug_field="slug", queryset=Subject.objects.all()
    )
    score = serializers.IntegerField(min_value=0)

    class Meta:
        model = ExpectedScore
        fields = ("subject", "score")


class StudentProfileSerializer(serializers.ModelSerializer):
    """Read shape for GET /profile/ — matches openapi StudentProfile."""

    expected_scores = ExpectedScoreSerializer(many=True, read_only=True)

    class Meta:
        model = StudentProfile
        fields = (
            "target_university",
            "target_specialty",
            "target_score",
            "onboarding_completed",
            "expected_scores",
        )
        read_only_fields = ("onboarding_completed",)


class StudentProfileUpdateSerializer(serializers.ModelSerializer):
    """Write shape for PATCH /profile/.

    ``expected_scores`` is a nested upsert handled in the view via
    ``services.upsert_expected_scores`` after the parent fields are saved.
    """

    expected_scores = ExpectedScoreSerializer(many=True, required=False)
    subjects = serializers.SlugRelatedField(
        slug_field="slug",
        many=True,
        required=False,
        queryset=Subject.objects.all(),
    )
    target_score = serializers.IntegerField(
        min_value=0, allow_null=True, required=False
    )

    class Meta:
        model = StudentProfile
        fields = (
            "target_university",
            "target_specialty",
            "target_score",
            "subjects",
            "expected_scores",
        )
        extra_kwargs = {
            "target_university": {"required": False, "allow_null": True},
            "target_specialty": {"required": False, "allow_null": True},
        }


class OnboardingSpecialtySerializer(serializers.Serializer):
    """Mirror of openapi ``Specialty`` used inside the onboarding wizard."""

    id = serializers.IntegerField()
    university_id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()
    latest_threshold = serializers.SerializerMethodField()

    def get_latest_threshold(self, obj) -> int | None:
        threshold = obj.thresholds.order_by("-year").first()
        return threshold.min_score if threshold else None


class OnboardingUniversitySerializer(serializers.Serializer):
    """Minimal University shape used by the onboarding-options endpoint.

    Mirrors the openapi ``University`` schema (id, name, city, code,
    specialties). We re-declare it here so the accounts app stays decoupled
    from careers' serializer module.
    """

    id = serializers.IntegerField()
    name = serializers.CharField()
    city = serializers.CharField()
    code = serializers.CharField()
    specialties = OnboardingSpecialtySerializer(many=True, read_only=True)
