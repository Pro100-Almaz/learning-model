"""Serializers for the roadmap surface."""

from __future__ import annotations

from rest_framework import serializers

from apps.assessments.serializers import QuestionPublicSerializer
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


# --- Chapter ladder (07_Chapter_Ladder_Spec.md) --------------------------


class _LadderLessonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    order = serializers.IntegerField()


class LadderTopicPlanSerializer(serializers.Serializer):
    """One topic's placement verdict and its branch payload."""

    tag_id = serializers.IntegerField()
    tag_slug = serializers.CharField()
    tag_name = serializers.CharField()
    verdict = serializers.CharField(allow_null=True)
    degraded = serializers.BooleanField()
    # Populated only for a ``gap`` verdict (soft fail → this topic's lessons).
    lessons = _LadderLessonSerializer(many=True)
    # Populated only for a ``mastered`` verdict (offer the hard problems).
    hard_question_ids = serializers.ListField(child=serializers.IntegerField())


class LadderPlanSerializer(serializers.Serializer):
    module_id = serializers.IntegerField()
    topics = LadderTopicPlanSerializer(many=True)


class LadderStepSerializer(serializers.Serializer):
    """One ladder step: the next question, or the final plan when complete.

    ``question`` is a leak-free ``QuestionPublicSerializer`` payload (no
    correctness), reused from the assessment flow. Exactly one of
    ``question`` / ``plan`` is non-null.
    """

    session_id = serializers.IntegerField()
    is_complete = serializers.BooleanField()
    question = QuestionPublicSerializer(allow_null=True)
    plan = LadderPlanSerializer(allow_null=True)


class LadderNextInputSerializer(serializers.Serializer):
    """One ladder answer: either a chosen option, or an "I don't know" abstention.

    Exactly one of ``option_id`` / ``dont_know`` must be supplied. ``dont_know``
    steps the ladder down like a wrong answer (the verdict falls out of the same
    rung machine) but does not move the student's mastery ``theta``.
    """

    session_id = serializers.IntegerField()
    question_id = serializers.IntegerField()
    # A null/omitted option_id means "don't know" — the student saw the question
    # but did not (or could not) answer. It is graded as wrong (outcome=0) so a
    # forced random guess can't luck the student into a skip verdict.
    option_id = serializers.IntegerField(required=False, allow_null=True)
