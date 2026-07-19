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


class TopicResultSerializer(serializers.Serializer):
    tag = TagSerializer()
    post_score = serializers.FloatField()
    correct = serializers.IntegerField()
    total = serializers.IntegerField()
    finished_at = serializers.DateTimeField()


class BucketsSerializer(serializers.Serializer):
    weak = TopicResultSerializer(many=True)
    improving = TopicResultSerializer(many=True)
    solid = TopicResultSerializer(many=True)


class PostRecommendationSerializer(serializers.Serializer):
    tag = TagSerializer()
    post_score = serializers.FloatField()
    lessons = LessonSummarySerializer(many=True)


class MathSerializer(serializers.Serializer):
    current_math = serializers.FloatField(allow_null=True, required=False)
    target_math = serializers.IntegerField(allow_null=True, required=False)
    gap = serializers.FloatField(allow_null=True, required=False)


class QualifyingGrantSerializer(serializers.Serializer):
    university_name = serializers.CharField()
    specialty_name = serializers.CharField()
    min_score = serializers.IntegerField()
    margin = serializers.IntegerField()


class NearMissGrantSerializer(serializers.Serializer):
    university_name = serializers.CharField()
    specialty_name = serializers.CharField()
    min_score = serializers.IntegerField()
    points_needed = serializers.IntegerField()


class UniversitiesSerializer(serializers.Serializer):
    qualifying = QualifyingGrantSerializer(many=True)
    near_miss = NearMissGrantSerializer(many=True)


class StudentReportSerializer(serializers.Serializer):
    buckets = BucketsSerializer()
    recommendations = PostRecommendationSerializer(many=True)
    math = MathSerializer()
    universities = UniversitiesSerializer()