"""I/O serializers for the analytics endpoints.

These match the openapi.yaml contract exactly (snake_case fields).
Services return plain dicts already in the contract shape, so the
serializers here are thin and primarily exist for schema generation
via drf-spectacular.
"""

from rest_framework import serializers


class TagSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    slug = serializers.CharField()


class LessonSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    order = serializers.IntegerField()
    duration_sec = serializers.IntegerField()
    completed = serializers.BooleanField()


class TagStatSerializer(serializers.Serializer):
    tag = TagSerializer()
    correct = serializers.IntegerField()
    total = serializers.IntegerField()
    percent = serializers.FloatField()


class RecommendationSerializer(serializers.Serializer):
    tag = TagSerializer()
    percent = serializers.FloatField()
    lessons = LessonSummarySerializer(many=True)
