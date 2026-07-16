"""Serializers for the generation API surface."""

from __future__ import annotations

from rest_framework import serializers

import config
from apps.generation.models import GenerationJob, GenerationStep
from apps.assessments.serializers import QuestionPublicSerializer


class GenerationJobCreateSerializer(serializers.Serializer):
    """Input shape for ``POST /generation/jobs/``."""

    topic = serializers.CharField(max_length=80)
    count = serializers.IntegerField(min_value=1, max_value=20)
    # Intended score for the profile subject (профильная математика), 0-40 —
    # NOT the ENT total. Higher => harder generated questions.
    target_score = serializers.IntegerField(
        min_value=0, max_value=40, required=False, allow_null=True
    )
    # Output language for the batch. Optional: omitting it falls back to the
    # house default (Russian), so existing clients keep working unchanged.
    language = serializers.ChoiceField(
        choices=config.SUPPORTED_LANGUAGES,
        default=config.DEFAULT_LANGUAGE,
    )


class GenerationStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationStep
        fields = [
            "id",
            "question_index",
            "kind",
            "status",
            "message",
            "data",
            "question",
            "created_at",
        ]
        read_only_fields = fields


class GenerationJobSerializer(serializers.ModelSerializer):
    """Snapshot of a job + its steps (for GET and POST responses)."""

    steps = GenerationStepSerializer(many=True, read_only=True)
    stream_url = serializers.SerializerMethodField()

    class Meta:
        model = GenerationJob
        fields = [
            "id",
            "topic",
            "count",
            "target_score",
            "language",
            "status",
            "created_at",
            "started_at",
            "finished_at",
            "error",
            "created_count",
            "skipped_count",
            "failed_count",
            "celery_task_id",
            "steps",
            "stream_url",
        ]
        read_only_fields = fields

    def get_stream_url(self, obj: GenerationJob) -> str:
        # Convention only — the SSE endpoint lives under the same v1 prefix.
        return f"/api/v1/generation/jobs/{obj.pk}/stream/"


class GenerationJobListSerializer(serializers.ModelSerializer):
    """Compact shape for the list endpoint (no steps)."""

    class Meta:
        model = GenerationJob
        fields = [
            "id",
            "topic",
            "count",
            "target_score",
            "language",
            "status",
            "created_at",
            "started_at",
            "finished_at",
            "created_count",
            "skipped_count",
            "failed_count",
        ]
        read_only_fields = fields

class GenerationJobQuestionsSerializer(serializers.Serializer):
    job_id = serializers.IntegerField()
    status = serializers.CharField()
    count = serializers.IntegerField()
    created_count = serializers.IntegerField()
    skipped_count = serializers.IntegerField()
    failed_count = serializers.IntegerField()
    questions = QuestionPublicSerializer(many=True)
