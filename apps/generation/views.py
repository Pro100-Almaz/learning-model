"""Generation views.

Five endpoints under ``/api/v1/generation/``:

* ``POST   /jobs/``                  — start a batch.
* ``GET    /jobs/``                  — list batches the caller can see (staff: all; others: own).
* ``GET    /jobs/<id>/``             — full snapshot incl. steps.
* ``GET    /jobs/<id>/stream/``      — Server-Sent Events stream of live progress.
* ``POST   /jobs/<id>/cancel/``      — flag the job cancelled (worker checks each iteration).

Creating a job triggers an LLM-spending Celery task, so it's restricted to
``IsAdminUser`` by default. Read access is owner-or-staff.
"""

from __future__ import annotations

import logging

from django.http import StreamingHttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import GenerationJob
from .serializers import (
    GenerationJobCreateSerializer,
    GenerationJobListSerializer,
    GenerationJobSerializer,
)
from .services import dispatch_job, iter_event_stream

logger = logging.getLogger("apps.generation")


def _get_visible_job(user, job_id: int) -> GenerationJob:
    """Fetch a job the user is allowed to see (own or staff). 404 otherwise."""
    try:
        job = GenerationJob.objects.prefetch_related("steps").get(pk=job_id)
    except GenerationJob.DoesNotExist as exc:
        raise NotFound({"detail": "job not found", "code": "not_found"}) from exc
    if not user.is_staff and job.user_id != user.id:
        # Hide existence from non-owners.
        raise NotFound({"detail": "job not found", "code": "not_found"})
    return job


class JobListCreateView(APIView):
    """POST starts a new job; GET lists jobs the caller can see."""

    permission_classes = [IsAdminUser]
    serializer_class = GenerationJobSerializer

    @extend_schema(responses=GenerationJobListSerializer(many=True))
    def get(self, request):
        qs = GenerationJob.objects.all().order_by("-created_at")
        if not request.user.is_staff:
            qs = qs.filter(user=request.user)
        data = GenerationJobListSerializer(qs[:100], many=True).data
        return Response(data)

    @extend_schema(
        request=GenerationJobCreateSerializer,
        responses=GenerationJobSerializer,
    )
    def post(self, request):
        payload = GenerationJobCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        job = dispatch_job(
            user=request.user,
            topic=payload.validated_data["topic"],
            count=payload.validated_data["count"],
            target_score=payload.validated_data.get("target_score"),
        )
        return Response(
            GenerationJobSerializer(job).data,
            status=status.HTTP_201_CREATED,
        )


class JobDetailView(APIView):
    """GET /generation/jobs/{id}/ — snapshot incl. all persisted steps."""

    permission_classes = [IsAuthenticated]
    serializer_class = GenerationJobSerializer

    @extend_schema(responses=GenerationJobSerializer)
    def get(self, request, id: int):
        job = _get_visible_job(request.user, id)
        return Response(GenerationJobSerializer(job).data)


class JobStreamView(APIView):
    """GET /generation/jobs/{id}/stream/ — Server-Sent Events feed.

    Replays the steps already persisted, then subscribes to Redis pub/sub
    until the job reaches a terminal status. The response body never returns
    JSON — it returns ``text/event-stream``.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request, id: int):
        job = _get_visible_job(request.user, id)
        response = StreamingHttpResponse(
            iter_event_stream(job),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # disable nginx buffering
        response["Connection"] = "keep-alive"
        return response


class JobCancelView(APIView):
    """POST /generation/jobs/{id}/cancel/ — mark cancelled; worker bails out."""

    permission_classes = [IsAdminUser]
    serializer_class = GenerationJobSerializer

    @extend_schema(request=None, responses=GenerationJobSerializer)
    def post(self, request, id: int):
        job = _get_visible_job(request.user, id)
        if job.is_terminal:
            return Response(GenerationJobSerializer(job).data)
        # Best-effort: try to revoke the Celery task too. The worker's own
        # per-iteration cancel check is the source of truth — Celery revoke
        # may or may not arrive in time depending on broker state.
        if job.celery_task_id:
            try:
                from conf.celery import app as celery_app  # local: web process doesn't need celery on import
                celery_app.control.revoke(job.celery_task_id, terminate=False)
            except Exception:  # pragma: no cover - defensive
                logger.exception("cancel: revoke failed for %s", job.celery_task_id)
        job.status = GenerationJob.STATUS_CANCELLED
        from django.utils import timezone

        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])
        # Let any open SSE listeners disconnect.
        from .services import publish_event

        publish_event(
            job.pk,
            {"type": "job.cancelled", "job_id": job.pk, "status": job.status},
        )
        return Response(GenerationJobSerializer(job).data)
