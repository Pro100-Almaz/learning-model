"""Thin views for assessments — all logic lives in services.py."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assessments import services
from apps.assessments.models import Test
from apps.assessments.serializers import (
    AttemptAnswerInputSerializer,
    AttemptCreateInputSerializer,
    AttemptResultSerializer,
    AttemptReviewSerializer,
    AttemptStartSerializer,
    TestSerializer,
    TutorFeedbackSerializer,
    TutorRequestInputSerializer,
)
from apps.content.models import Lesson


class TestDetailView(APIView):
    """GET /api/v1/tests/{id}/ — metadata only, no questions or answers."""

    permission_classes = [IsAuthenticated]
    serializer_class = TestSerializer

    @extend_schema(responses=TestSerializer)
    def get(self, request, id: int):
        test = get_object_or_404(Test, pk=id)
        return Response(TestSerializer(test).data)


class AttemptCreateView(APIView):
    """POST /api/v1/attempts/ — start a new attempt."""

    permission_classes = [IsAuthenticated]
    serializer_class = AttemptStartSerializer

    @extend_schema(
        request=AttemptCreateInputSerializer,
        responses={201: AttemptStartSerializer},
    )
    def post(self, request):
        payload = AttemptCreateInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        lesson = get_object_or_404(Lesson, id=payload.data["lesson_id"])
        test = get_object_or_404(Test, lesson=lesson)
        services.remove_failed_attempts(request.user, test)
        attempt = services.start_attempt(request.user, test)
        body = services.build_attempt_start_payload(attempt)
        return Response(
            AttemptStartSerializer(body).data, status=status.HTTP_201_CREATED
        )


class AttemptAnswerView(APIView):
    """POST /api/v1/attempts/{id}/answer/ — record one selection."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "answer"
    serializer_class = AttemptAnswerInputSerializer

    @extend_schema(
        request=AttemptAnswerInputSerializer,
        responses={200: AttemptAnswerInputSerializer},
    )
    def post(self, request, id: int):
        attempt = services.get_attempt_for_owner(request.user, id)
        # Enforce timeout *before* recording — refuses late answers on mocks.
        if services.enforce_mock_timeout(attempt):
            return Response(
                {"detail": "attempt time expired", "code": "time_expired"},
                status=status.HTTP_409_CONFLICT,
            )

        payload = AttemptAnswerInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        result = services.record_answer(
            attempt,
            question_id=payload.validated_data["question_id"],
            option_id=payload.validated_data["option_id"],
        )

        # Mock + diagnostic withhold correctness until /finish/.
        is_correct_for_client = (
            None if attempt.test.type in ("mock", "diagnostic") else result.is_correct
        )
        return Response(
            {
                "is_correct": is_correct_for_client,
                "xp_awarded": result.xp_awarded,
            }
        )


class AttemptFinishView(APIView):
    """POST /api/v1/attempts/{id}/finish/ — finalize and score."""

    permission_classes = [IsAuthenticated]
    serializer_class = AttemptResultSerializer

    @extend_schema(request=None, responses=AttemptResultSerializer)
    def post(self, request, id: int):
        attempt = services.get_attempt_for_owner(request.user, id)
        services.enforce_mock_timeout(attempt)
        if not attempt.is_completed:
            attempt = services.finish_attempt(attempt)
        body = services.build_attempt_result_payload(attempt)
        return Response(AttemptResultSerializer(body).data)


class AttemptReviewView(APIView):
    """GET /api/v1/attempts/{id}/review/ — owner-only error review."""

    permission_classes = [IsAuthenticated]
    serializer_class = AttemptReviewSerializer

    @extend_schema(responses=AttemptReviewSerializer)
    def get(self, request, id: int):
        attempt = services.get_attempt_for_owner(request.user, id)
        body = services.build_attempt_review_payload(attempt)
        return Response(AttemptReviewSerializer(body).data)


class AttemptTutorView(APIView):
    """POST /api/v1/attempts/{id}/tutor/ — on-demand help for one question.

    The student opts in (this never fires automatically). Review-only: the
    attempt must be finished. For a wrong answer it returns a short 'margin note'
    that names the mistake without revealing the answer; for a skipped question
    it returns a worked explanation that reveals the answer.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "tutor"
    serializer_class = TutorFeedbackSerializer

    @extend_schema(
        request=TutorRequestInputSerializer,
        responses={200: TutorFeedbackSerializer},
    )
    def post(self, request, id: int):
        attempt = services.get_attempt_for_owner(request.user, id)
        payload = TutorRequestInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        feedback = services.get_tutor_feedback(
            attempt, question_id=payload.validated_data["question_id"]
        )
        return Response(TutorFeedbackSerializer({"feedback": feedback}).data)
