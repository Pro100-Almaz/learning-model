"""Views for the content app — T-101.

GET /api/v1/modules/         -> ModuleListView
GET /api/v1/modules/{id}/    -> ModuleDetailView
GET /api/v1/lessons/{id}/    -> LessonDetailView

Business logic lives in services.py; views only shape I/O.
"""

from __future__ import annotations

from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .models import Lesson, Module
from .serializers import (
    LessonSerializer,
    ModuleDetailSerializer,
    ModuleSerializer,
)
from .services import compute_lesson_completion, get_micro_test_id_for_lesson


class ModuleListView(generics.ListAPIView):
    """GET /api/v1/modules/

    Returns an array of Module objects with a denormalized lesson_count.
    """

    serializer_class = ModuleSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None  # contract returns a flat array

    def get_queryset(self):
        return (
            Module.objects.all()
            .annotate(lesson_count=Count("lessons"))
            .order_by("order", "id")
        )


class ModuleDetailView(generics.RetrieveAPIView):
    """GET /api/v1/modules/{id}/

    Returns ModuleDetail = Module + lessons[] (LessonSummary).
    completion_map is computed once per request and passed via serializer
    context to avoid N+1 queries.
    """

    serializer_class = ModuleDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        return (
            Module.objects.all()
            .annotate(lesson_count=Count("lessons"))
            .prefetch_related("lessons")
        )

    def retrieve(self, request, *args, **kwargs):
        # Cache the object so get_serializer_context can use it without
        # triggering a second query.
        self._cached_object = self.get_object()
        return super().retrieve(request, *args, **kwargs)

    def get_object(self):
        if getattr(self, "_cached_object", None) is not None:
            return self._cached_object
        return super().get_object()

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        module = self.get_object()
        lesson_ids = list(module.lessons.values_list("id", flat=True))
        ctx["completion_map"] = compute_lesson_completion(
            self.request.user, lesson_ids
        )
        return ctx


class LessonDetailView(generics.RetrieveAPIView):
    """GET /api/v1/lessons/{id}/

    Returns the full lesson plus the linked micro_test_id (or null).
    """

    serializer_class = LessonSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"
    queryset = Lesson.objects.all()

    def retrieve(self, request, *args, **kwargs):
        self._cached_object = get_object_or_404(
            Lesson, pk=self.kwargs[self.lookup_field]
        )
        return super().retrieve(request, *args, **kwargs)

    def get_object(self):
        if getattr(self, "_cached_object", None) is not None:
            return self._cached_object
        return get_object_or_404(Lesson, pk=self.kwargs[self.lookup_field])

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        lesson = self.get_object()
        ctx["micro_test_map"] = {lesson.pk: get_micro_test_id_for_lesson(lesson)}
        return ctx
