"""Serializers for the UBT blueprint engine's API surface.

Deliberately plain Serializers rather than ModelSerializers: nothing here has a
model. The UBT engine owns no tables -- it reads blueprints off disk and writes
into assessments.Question -- so these describe request and response shapes only.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.assessments.serializers import QuestionPublicSerializer
from ubt_question_engine.localize import ENGINE_LANGUAGES

MAX_BATCH = 50


class UbtTopicSerializer(serializers.Serializer):
    """One row of the topic catalogue."""

    topic = serializers.CharField()
    display_name = serializers.CharField()
    curriculum_ref = serializers.CharField(allow_blank=True)
    tag_slug = serializers.CharField(allow_blank=True)
    tag_name = serializers.CharField(allow_blank=True)
    supported_difficulties = serializers.ListField(child=serializers.IntegerField())
    default_difficulty = serializers.IntegerField()
    modes = serializers.ListField(child=serializers.CharField())


class UbtGenerateRequestSerializer(serializers.Serializer):
    """Input for both /preview/ and /questions/."""

    # Not a ChoiceField over all 58 stems: that bloats the OpenAPI schema and
    # would need regenerating whenever a blueprint is added. The view validates
    # against the loader, which is the single source of truth anyway.
    topic = serializers.CharField(max_length=120, required=False, allow_null=True)
    count = serializers.IntegerField(min_value=1, max_value=MAX_BATCH, default=1)
    difficulty = serializers.IntegerField(min_value=1, max_value=3, required=False,
                                          allow_null=True)
    all_difficulties = serializers.BooleanField(default=False)
    # Echoed back on every response. Omitting it draws one and reports it, so any
    # result -- including a bad one a reviewer wants to complain about -- can be
    # reproduced exactly.
    seed = serializers.IntegerField(required=False, allow_null=True)
    languages = serializers.ListField(
        child=serializers.ChoiceField(choices=ENGINE_LANGUAGES),
        required=False,
        allow_empty=False,
        help_text="Default: every language whose translations are complete.",
    )

    def validate(self, attrs):
        if not attrs.get("topic") and not attrs.get("all_difficulties"):
            # `topic` omitted means "every topic", which is a real use for
            # preview and a very expensive one for publish. Say so explicitly
            # rather than letting a client discover it by filling the bank.
            pass
        return attrs


class UbtOptionSerializer(serializers.Serializer):
    """A preview option, misconception included.

    Preview is an authoring tool for staff, so unlike QuestionPublicSerializer it
    deliberately exposes `is_correct` and `misconception` -- the reviewer needs
    exactly the fields a student must never see.
    """

    latex = serializers.CharField()
    is_correct = serializers.BooleanField()
    misconception = serializers.CharField(allow_blank=True)


class UbtPreviewItemSerializer(serializers.Serializer):
    topic = serializers.CharField()
    display_name = serializers.CharField()
    mode = serializers.CharField()
    difficulty = serializers.IntegerField()
    seed = serializers.IntegerField()
    content_hash = serializers.CharField()
    language = serializers.CharField()
    instruction = serializers.CharField()
    latex = serializers.CharField(allow_blank=True)
    text = serializers.CharField()
    answer_latex = serializers.CharField()
    # The diagram this mode would ship with, so a reviewer sees the item exactly
    # as a student will -- including noticing that a geometry mode has no figure
    # yet. Null for every mode without a ModeFigure row.
    figure = serializers.JSONField(allow_null=True)
    options = UbtOptionSerializer(many=True)


class UbtPreviewResponseSerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    generated = serializers.IntegerField()
    seed = serializers.IntegerField(help_text="Base seed; each item's own seed is on it.")
    languages = serializers.ListField(child=serializers.CharField())
    items = UbtPreviewItemSerializer(many=True)
    failures = serializers.ListField(child=serializers.CharField())


class UbtPublishedItemSerializer(serializers.Serializer):
    """One published item: the same mathematics across every language row."""

    topic = serializers.CharField()
    mode = serializers.CharField()
    difficulty = serializers.IntegerField()
    seed = serializers.IntegerField()
    # The language-free hash. Identical across this item's rows, and the value
    # that joins them back together (Question.solution.content_hash).
    content_hash = serializers.CharField()
    question_ids = serializers.DictField(child=serializers.IntegerField())
    was_duplicate = serializers.BooleanField()


class UbtPublishResponseSerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    published = serializers.IntegerField()
    duplicates = serializers.IntegerField()
    failed = serializers.IntegerField()
    seed = serializers.IntegerField()
    languages = serializers.ListField(child=serializers.CharField())
    items = UbtPublishedItemSerializer(many=True)
    failures = serializers.ListField(child=serializers.CharField())
    questions = QuestionPublicSerializer(many=True)


class UbtCoverageSerializer(serializers.Serializer):
    """Whether the bank can currently be served in each language."""

    translatable_strings = serializers.IntegerField()
    coverage = serializers.DictField(child=serializers.ListField(child=serializers.IntegerField()))
    publishable_languages = serializers.ListField(child=serializers.CharField())
    missing_examples = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
