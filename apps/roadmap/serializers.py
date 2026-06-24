"""Serializers for the roadmap surface."""

from __future__ import annotations

from rest_framework import serializers

from apps.content.models import Tag


class _RoadmapLessonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    module_id = serializers.IntegerField()
    module_title = serializers.CharField()
    order = serializers.IntegerField()
    duration_sec = serializers.IntegerField()


class _RoadmapMicroTestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()


class _RoadmapTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name", "slug"]


class RoadmapItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    order = serializers.IntegerField()
    status = serializers.CharField()
    rationale = serializers.CharField()
    completed_at = serializers.DateTimeField(allow_null=True)
    lesson = _RoadmapLessonSerializer()
    micro_test = _RoadmapMicroTestSerializer(allow_null=True)
    weak_tag = _RoadmapTagSerializer(allow_null=True)


class RoadmapStatsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    completed = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    pending = serializers.IntegerField()


class RoadmapSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    source = serializers.CharField()
    created_at = serializers.DateTimeField()
    items = RoadmapItemSerializer(many=True)
    stats = RoadmapStatsSerializer()


class DiagnosticInfoSerializer(serializers.Serializer):
    """Shape returned by GET /roadmap/diagnostic/.

    ``test_id`` is null only if no diagnostic test has been seeded yet.
    """

    test_id = serializers.IntegerField(allow_null=True)
    test_title = serializers.CharField(allow_null=True)
    question_count = serializers.IntegerField()
    taken = serializers.BooleanField()
    attempt_id = serializers.IntegerField(allow_null=True)
    completed = serializers.BooleanField()
    score = serializers.FloatField(allow_null=True)
