"""Roadmap views — three thin endpoints.

* ``GET /api/v1/roadmap/diagnostic/`` — diagnostic test metadata + this
  student's attempt state.
* ``GET /api/v1/roadmap/`` — current active roadmap with items + stats.
* ``POST /api/v1/roadmap/regenerate/`` — force regeneration from latest
  diagnostic (or mock fallback). Returns the new roadmap payload.
"""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.content.models import Module

from . import ladder as ladder_service
from . import services
from .models import ChapterLadderSession
from .serializers import (
    DiagnosticInfoSerializer,
    LadderNextInputSerializer,
    LadderStepSerializer,
    RoadmapSerializer,
)


def _build_roadmap_payload(roadmap) -> dict:
    """Shape an active roadmap into the RoadmapSerializer input dict."""
    items = list(
        roadmap.items.select_related(
            "lesson",
            "lesson__module",
            "micro_test",
            "weak_tag",
        ).all()
    )
    item_payloads = [
        {
            "id": it.pk,
            "order": it.order,
            "status": it.status,
            "rationale": it.rationale,
            "completed_at": it.completed_at,
            "lesson": {
                "id": it.lesson_id,
                "title": it.lesson.title,
                "module_id": it.lesson.module_id,
                "module_title": it.lesson.module.title,
                "order": it.lesson.order,
                "duration_sec": it.lesson.duration_sec,
            },
            "micro_test": (
                {"id": it.micro_test_id, "title": it.micro_test.title}
                if it.micro_test_id
                else None
            ),
            "weak_tag": it.weak_tag,
        }
        for it in items
    ]
    by_status = {"pending": 0, "in_progress": 0, "completed": 0}
    for it in items:
        by_status[it.status] = by_status.get(it.status, 0) + 1
    return {
        "id": roadmap.pk,
        "source": roadmap.source,
        "created_at": roadmap.created_at,
        "items": item_payloads,
        "stats": {
            "total": len(items),
            "completed": by_status["completed"],
            "in_progress": by_status["in_progress"],
            "pending": by_status["pending"],
        },
    }


class DiagnosticInfoView(APIView):
    """GET /api/v1/roadmap/diagnostic/ — info about THE diagnostic test."""

    permission_classes = [IsAuthenticated]
    serializer_class = DiagnosticInfoSerializer

    @extend_schema(responses=DiagnosticInfoSerializer)
    def get(self, request: Request) -> Response:
        test = services.get_active_diagnostic_test()
        if test is None:
            payload = {
                "test_id": None,
                "test_title": None,
                "question_count": 0,
                "taken": False,
                "attempt_id": None,
                "completed": False,
                "score": None,
            }
            return Response(DiagnosticInfoSerializer(payload).data)

        from apps.assessments.models import TestAttempt

        attempt = (
            TestAttempt.objects.filter(student=request.user, test=test)
            .order_by("-started_at")
            .first()
        )
        payload = {
            "test_id": test.pk,
            "test_title": test.title,
            "question_count": test.questions.count(),
            "taken": attempt is not None,
            "attempt_id": attempt.pk if attempt else None,
            "completed": bool(attempt and attempt.is_completed),
            "score": attempt.score if attempt and attempt.is_completed else None,
        }
        return Response(DiagnosticInfoSerializer(payload).data)


class RoadmapView(APIView):
    """GET /api/v1/roadmap/ — the student's active roadmap."""

    permission_classes = [IsAuthenticated]
    serializer_class = RoadmapSerializer

    @extend_schema(responses=RoadmapSerializer)
    def get(self, request: Request) -> Response:
        roadmap = services.get_active_roadmap(request.user)
        if roadmap is None:
            # Lazy generation: if there's a finished diagnostic but no
            # roadmap yet, build one now.
            roadmap = services.generate_roadmap_for_student(request.user)
        if roadmap is None:
            return Response(
                {
                    "detail": "Сначала пройдите диагностический тест.",
                    "code": "no_diagnostic_attempt",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(RoadmapSerializer(_build_roadmap_payload(roadmap)).data)


class RoadmapRegenerateView(APIView):
    """POST /api/v1/roadmap/regenerate/ — rebuild from latest diagnostic/mock."""

    permission_classes = [IsAuthenticated]
    serializer_class = RoadmapSerializer

    @extend_schema(request=None, responses=RoadmapSerializer)
    def post(self, request: Request) -> Response:
        roadmap = services.generate_roadmap_for_student(request.user)
        if roadmap is None:
            return Response(
                {
                    "detail": "Сначала пройдите диагностический или пробный тест.",
                    "code": "no_completed_attempt",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(RoadmapSerializer(_build_roadmap_payload(roadmap)).data)


def _ladder_step_response(session: ChapterLadderSession) -> Response:
    """Shape a ladder session into the next-question-or-plan step payload."""
    question = None if session.is_complete else ladder_service.next_question(session)
    if question is None:
        payload = {
            "session_id": session.pk,
            "is_complete": True,
            "question": None,
            "plan": ladder_service.chapter_plan(session),
        }
    else:
        payload = {
            "session_id": session.pk,
            "is_complete": False,
            "question": question,
            "plan": None,
        }
    return Response(LadderStepSerializer(payload).data)


class ChapterLadderStartView(APIView):
    """POST /api/v1/roadmap/chapter/<module_id>/ladder/start/ — begin placement."""

    permission_classes = [IsAuthenticated]
    serializer_class = LadderStepSerializer

    @extend_schema(request=None, responses=LadderStepSerializer)
    def post(self, request: Request, module_id: int) -> Response:
        if not settings.CHAPTER_LADDER_ENABLED:
            return Response(
                {"detail": "chapter ladder is disabled", "code": "ladder_disabled"},
                status=status.HTTP_409_CONFLICT,
            )
        module = get_object_or_404(Module, pk=module_id)
        session = ladder_service.start_ladder(request.user, module)
        return _ladder_step_response(session)


class ChapterLadderNextView(APIView):
    """POST /api/v1/roadmap/chapter/ladder/next/ — record an answer, get next step."""

    permission_classes = [IsAuthenticated]
    serializer_class = LadderStepSerializer

    @extend_schema(request=LadderNextInputSerializer, responses=LadderStepSerializer)
    def post(self, request: Request) -> Response:
        payload = LadderNextInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        session = get_object_or_404(
            ChapterLadderSession,
            pk=payload.validated_data["session_id"],
            student=request.user,
        )
        ladder_service.record_answer(
            session,
            question_id=payload.validated_data["question_id"],
            option_id=payload.validated_data["option_id"],
        )
        return _ladder_step_response(session)
