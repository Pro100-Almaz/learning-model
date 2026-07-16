"""Views for the accounts app.

Two student-facing endpoints (profile + onboarding-options) and an
``AuthMeView`` that the OAuth agent wires into ``/api/v1/auth/me/``.
"""

from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.careers.models import Specialty, University

from apps.accounts import services
from apps.accounts.serializers import (
    AuthUserSerializer,
    OnboardingSpecialtySerializer,
    OnboardingUniversitySerializer,
    StudentProfileSerializer,
    StudentProfileUpdateSerializer,
)


_OnboardingOptionsResponse = inline_serializer(
    name="OnboardingOptionsResponse",
    fields={
        "universities": OnboardingUniversitySerializer(many=True),
        "specialties": OnboardingSpecialtySerializer(many=True),
        "subjects": drf_serializers.ListField(child=drf_serializers.CharField()),
    },
)


class ProfileView(APIView):
    """GET / PATCH the current student's profile.

    GET auto-creates the StudentProfile on first hit so the onboarding
    wizard can immediately PATCH it with target values.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = StudentProfileSerializer

    def _profile_queryset(self):
        return (
            services.ensure_profile(self.request.user).__class__.objects
            .select_related("target_university", "target_specialty")
            .prefetch_related("subjects", "expected_scores__subject")
        )

    @extend_schema(responses=StudentProfileSerializer)
    def get(self, request: Request) -> Response:
        services.ensure_profile(request.user)
        profile = self._profile_queryset().get(user=request.user)
        return Response(StudentProfileSerializer(profile).data)

    @extend_schema(
        request=StudentProfileUpdateSerializer,
        responses=StudentProfileSerializer,
    )
    def patch(self, request: Request) -> Response:
        profile = services.ensure_profile(request.user)
        serializer = StudentProfileUpdateSerializer(
            profile, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)

        expected_scores = serializer.validated_data.pop("expected_scores", None)
        # M2M can't be assigned via setattr — pop it out and use .set() below.
        # subjects = serializer.validated_data.pop("subjects", None)
        subjects = None
        if expected_scores is not None:
            subjects = [e["subject"] for e in expected_scores if e.get("subject", False)]

        for field, value in serializer.validated_data.items():
            setattr(profile, field, value)
        profile.save()

        if subjects is not None:
            profile.subjects.set(subjects)

        if expected_scores is not None:
            services.upsert_expected_scores(profile, expected_scores)

        services.complete_onboarding_if_ready(profile)

        fresh = self._profile_queryset().get(pk=profile.pk)
        return Response(
            StudentProfileSerializer(fresh).data, status=status.HTTP_200_OK
        )


class OnboardingOptionsView(APIView):
    """Bundle of catalog data the onboarding wizard renders.

    - ``universities``: full University+Specialty catalog (matches openapi
      ``University`` schema).
    - ``specialties``: every specialty flattened (the UI uses this for the
      "pick a specialty" step before the user has chosen a university).
    - ``subjects``: the list of "other ENT subjects" we ask the student to
      estimate, sourced from ``settings.ENT_CONFIG``.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = _OnboardingOptionsResponse

    @extend_schema(responses=_OnboardingOptionsResponse)
    def get(self, request: Request) -> Response:
        universities = (
            University.objects.all()
            .prefetch_related("specialties__thresholds")
            .order_by("name")
        )
        specialties = (
            Specialty.objects.all()
            .select_related("university")
            .prefetch_related("thresholds")
            .order_by("university__name", "name")
        )

        return Response(
            {
                "universities": OnboardingUniversitySerializer(
                    universities, many=True
                ).data,
                "specialties": OnboardingSpecialtySerializer(
                    specialties, many=True
                ).data,
                "subjects": list(settings.ENT_CONFIG.get("other_subjects", [])),
            }
        )


class AuthMeView(APIView):
    """GET /api/v1/auth/me/ — the current user with onboarding flag.

    Wired into the auth url namespace by the OAuth agent.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AuthUserSerializer

    @extend_schema(responses=AuthUserSerializer)
    def get(self, request: Request) -> Response:
        # Touch the profile so onboarding_completed is accurate on first call.
        services.ensure_profile(request.user)
        user = (
            request.user.__class__.objects
            .select_related("profile")
            .get(pk=request.user.pk)
        )
        return Response(AuthUserSerializer(user).data)
