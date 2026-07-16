"""Serializers for the content app.

Shape only — all business logic lives in services.py. Field names and types
must match plan/openapi.yaml exactly (snake_case JSON).
"""

from __future__ import annotations

from rest_framework import serializers

from apps.content.models import Lesson, Module, Tag, Subject, ClassGrade


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name", "slug", "description"]


class ModuleSerializer(serializers.ModelSerializer):
    """List shape: includes a denormalized lesson_count."""

    lessons = serializers.SerializerMethodField()
    done = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    class Meta:
        model = Module
        fields = [
            "id",
            "title",
            "slug",
            "order",
            "class_grade_id",
            "lessons",
            "description",
            "done",
            "progress"
        ]

    def get_lessons(self, obj: Module):
        return obj.lessons.count()

    def get_done(self, obj: Module):
        return 1

    def get_progress(self, obj: Module):
        return 43


class LessonSummarySerializer(serializers.ModelSerializer):
    """Lesson card embedded in ModuleDetail.

    `completed` is computed per-request via context["completion_map"] to
    avoid N+1 queries.
    """

    completed = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = ["id", "title", "order", "duration_sec", "completed"]

    def get_completed(self, obj: Lesson) -> bool:
        completion_map = self.context.get("completion_map") or {}
        return bool(completion_map.get(obj.pk, False))


class ModuleDetailSerializer(serializers.ModelSerializer):
    """Module + nested lesson summaries."""

    lesson_count = serializers.IntegerField(read_only=True)
    lessons = serializers.SerializerMethodField()
    subject = serializers.SlugRelatedField(
        slug_field="slug", source="class_grade.subject", read_only=True
    )
    class_grade = serializers.SlugRelatedField(slug_field="grade", read_only=True)

    class Meta:
        model = Module
        fields = [
            "id",
            "title",
            "slug",
            "order",
            "subject",
            "class_grade",
            "lesson_count",
            "lessons",
        ]

    def get_lessons(self, obj: Module) -> list[dict]:
        lessons = list(obj.lessons.all().order_by("order", "id"))
        return LessonSummarySerializer(
            lessons,
            many=True,
            context=self.context,
        ).data


class LessonBaseSerializer(serializers.ModelSerializer):
    """Minimal lesson shape: id, title, duration_sec.

    Use this wherever a lightweight lesson reference is needed. Fuller
    serializers extend it by appending to Meta.fields.
    """

    progress = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "duration_sec",
            "module_id",
            "status",
            "progress",
        ]

    def get_progress(self, obj: Lesson):
        return 43

    def get_status(self, obj: Lesson):
        return "progress"


class LessonSerializer(LessonBaseSerializer):
    """Full lesson detail. micro_test_id resolved via services."""

    micro_test_id = serializers.SerializerMethodField()
    tag = TagSerializer(read_only=True)

    class Meta(LessonBaseSerializer.Meta):
        fields = LessonBaseSerializer.Meta.fields + [
            "description",
            "video_url",
            "video_provider",
            "micro_test_id",
            "tag",
        ]

    def get_micro_test_id(self, obj: Lesson) -> int | None:
        # Resolved upstream by the view and passed via context to keep the
        # serializer free of DB calls when called in lists.
        ctx_map = self.context.get("micro_test_map")
        if ctx_map is not None:
            return ctx_map.get(obj.pk)
        from apps.content.services import get_micro_test_id_for_lesson

        return get_micro_test_id_for_lesson(obj)


class SubjectSerializer(serializers.ModelSerializer):
    """List shape: includes a denormalized lesson_count."""

    class_count = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    class Meta:
        model = Subject
        fields = [
            "id",
            "name",
            "slug",
            "class_count",
            "progress",
        ]

    def get_class_count(self, obj: Subject) -> int:
        return obj.classes.count()

    def get_progress(self, obj: Subject) -> int:
        # static for now, depends on how the lesson is counted as passed
        return 43


class ClassGradeSerializer(serializers.ModelSerializer):
    """List shape: includes a denormalized lesson_count."""

    title = serializers.SerializerMethodField()
    lessons = serializers.SerializerMethodField()
    modules = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    class Meta:
        model = ClassGrade
        fields = [
            "id",
            "grade",
            "title",
            "subject_id",
            "lessons",
            "modules",
            "progress",
        ]

    def get_lessons(self, obj: ClassGrade) -> int:
        return Lesson.objects.filter(module__class_grade=obj).count()

    def get_modules(self, obj: ClassGrade) -> int:
        return Module.objects.filter(class_grade=obj).count()

    def get_progress(self, obj: ClassGrade) -> int:
        # static for now
        return 43

    def get_title(self, obj: ClassGrade) -> str:
        # for now
        return f"{obj.grade}-сынып"
