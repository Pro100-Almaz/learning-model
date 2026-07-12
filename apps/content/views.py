"""Views for the content app — T-101.

GET /api/v1/modules/         -> ModuleListView
GET /api/v1/modules/{id}/    -> ModuleDetailView
GET /api/v1/lessons/{id}/    -> LessonDetailView

Business logic lives in services.py; views only shape I/O.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework.generics import (
    RetrieveAPIView,
    ListAPIView,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.content.models import Lesson, Module, Subject, ClassGrade
from apps.content.serializers import (
    LessonSerializer,
    ModuleSerializer, SubjectSerializer, ClassGradeSerializer, LessonBaseSerializer,
)
from apps.content.services import get_micro_test_id_for_lesson


class ModuleListView(ListAPIView):
    """GET /api/v1/modules/?subject=<slug>&class_grade=<grade>

    Returns an array of Module objects with a denormalized lesson_count.
    The onboarding flow is subject -> class grade -> module -> lesson, so the
    list is filterable by the subject slug and the class grade. Both query
    params are optional; omitting them returns the full list.
    """

    serializer_class = ModuleSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None  # contract returns a flat array

    def get_queryset(self):
        class_grade_id = self.kwargs.get("class_grade_id")

        if not class_grade_id:
            return Module.objects.none()

        return (
            Module.objects
            .filter(class_grade__id=class_grade_id)
            .order_by("order", "id")
        )


class LessonsListView(ListAPIView):
    """GET /api/v1/modules/{module_id}/

    Returns ModuleDetail = Module + lessons[] (LessonSummary).
    completion_map is computed once per request and passed via serializer
    context to avoid N+1 queries.
    """

    serializer_class = LessonBaseSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        module_id = self.kwargs.get("module_id")

        if not module_id:
            return Lesson.objects.none()

        return (
            Lesson.objects
            .filter(module_id=module_id)
            .order_by("order", "id")
        )


class LessonDetailView(RetrieveAPIView):
    """GET /api/v1/lessons/{lesson_id}/

    Returns the full lesson plus the linked micro_test_id (or null).
    """

    serializer_class = LessonSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"
    queryset = Lesson.objects.select_related("tag")

    def retrieve(self, request, *args, **kwargs):
        self._cached_object = get_object_or_404(
            Lesson.objects.select_related("tag"), pk=self.kwargs["lesson_id"]
        )
        return super().retrieve(request, *args, **kwargs)

    def get_object(self):
        if getattr(self, "_cached_object", None) is not None:
            return self._cached_object
        return get_object_or_404(
            Lesson.objects.select_related("tag"), pk=self.kwargs["lesson_id"]
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        lesson = self.get_object()
        ctx["micro_test_map"] = {lesson.pk: get_micro_test_id_for_lesson(lesson)}
        return ctx


class SubjectListView(ListAPIView):
    """GET /api/v1/subjects/"""

    serializer_class = SubjectSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        return user.profile.subjects.all()


class SubjectListAllView(ListAPIView):
    """GET /api/v1/subjects/all/"""

    serializer_class = SubjectSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    queryset = Subject.objects.all()


class ClassGradeListView(APIView):
    """GET /api/v1/subjects/<int:subject_id>/classes/"""

    serializer_class = ClassGradeSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request, subject_id):
        queryset = ClassGrade.objects.filter(subject_id=subject_id)

        data = ClassGradeSerializer(queryset, many=True).data
        return Response(data)
