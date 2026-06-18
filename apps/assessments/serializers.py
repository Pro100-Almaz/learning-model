from rest_framework import serializers

from .models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestAttempt,
)


class TestSerializer(serializers.ModelSerializer):
    """Test metadata; never reveals questions or answers."""

    question_count = serializers.SerializerMethodField()

    class Meta:
        model = Test
        fields = ["id", "type", "title", "time_limit_sec", "question_count"]

    def get_question_count(self, obj: Test) -> int:
        # Use prefetched count when available to avoid extra queries.
        cached = getattr(obj, "_prefetched_question_count", None)
        if cached is not None:
            return cached
        return obj.questions.count()


class AnswerOptionPublicSerializer(serializers.ModelSerializer):
    """Option shape during a test — never reveals correctness."""

    class Meta:
        model = AnswerOption
        fields = ["id", "text"]


class QuestionPublicSerializer(serializers.ModelSerializer):
    options = AnswerOptionPublicSerializer(many=True, read_only=True)
    image = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = ["id", "text", "image", "options"]

    def get_image(self, obj: Question) -> str | None:
        if not obj.image:
            return None
        try:
            return obj.image.url
        except ValueError:
            return None


class AttemptStartSerializer(serializers.Serializer):
    attempt_id = serializers.IntegerField()
    test = TestSerializer()
    started_at = serializers.DateTimeField()
    questions = QuestionPublicSerializer(many=True)


class AttemptResultSerializer(serializers.Serializer):
    attempt_id = serializers.IntegerField()
    score = serializers.FloatField()
    correct_count = serializers.IntegerField()
    total_count = serializers.IntegerField()
    finished_at = serializers.DateTimeField()


class _ReviewOptionSerializer(serializers.ModelSerializer):
    """Review-time option payload INCLUDES is_correct."""

    class Meta:
        model = AnswerOption
        fields = ["id", "text", "is_correct"]


class AttemptReviewItemSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    question_text = serializers.CharField()
    selected_option_id = serializers.IntegerField(allow_null=True)
    correct_option_id = serializers.IntegerField()
    is_correct = serializers.BooleanField()
    explanation = serializers.CharField()
    options = _ReviewOptionSerializer(many=True)


class AttemptReviewSerializer(serializers.Serializer):
    attempt_id = serializers.IntegerField()
    score = serializers.FloatField(allow_null=True)
    items = AttemptReviewItemSerializer(many=True)


# Input-only serializers ---------------------------------------------------


class AttemptCreateInputSerializer(serializers.Serializer):
    test_id = serializers.IntegerField()


class AttemptAnswerInputSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    option_id = serializers.IntegerField()


# Helper exports so tests / other code can use models cleanly.
__all__ = [
    "TestSerializer",
    "AnswerOptionPublicSerializer",
    "QuestionPublicSerializer",
    "AttemptStartSerializer",
    "AttemptResultSerializer",
    "AttemptReviewSerializer",
    "AttemptReviewItemSerializer",
    "AttemptCreateInputSerializer",
    "AttemptAnswerInputSerializer",
    "AttemptAnswer",
    "TestAttempt",
]
