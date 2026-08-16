from rest_framework import serializers

from apps.assessments import figures
from apps.assessments.models import (
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
    figure = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = ["id", "text", "image", "figure", "options"]

    def get_image(self, obj: Question) -> str | None:
        if not obj.image:
            return None
        try:
            return obj.image.url
        except ValueError:
            return None

    def get_figure(self, obj: Question) -> dict | None:
        """Inline SVG diagram for generated questions, or None.

        Separate from `image`, which is an uploaded file on a hand-authored
        question. This one is shared by every question of the same blueprint
        mode and is served as markup, so there is no URL to hand back. The
        lookup is cached table-wide -- see assessments.figures.
        """
        return figures.figure_for(obj.solution, language=obj.language)


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
    mistake_reason = serializers.CharField(allow_blank=True)
    # Same diagram the student saw while answering. Without it the review shows
    # "find x" with no picture, which is unreadable for exactly the geometry
    # questions people most need to review.
    figure = serializers.JSONField(allow_null=True)
    options = _ReviewOptionSerializer(many=True)


class AttemptReviewSerializer(serializers.Serializer):
    attempt_id = serializers.IntegerField()
    score = serializers.FloatField(allow_null=True)
    items = AttemptReviewItemSerializer(many=True)


# Input-only serializers ---------------------------------------------------


class AttemptCreateInputSerializer(serializers.Serializer):
    lesson_id = serializers.IntegerField()


class AttemptAnswerInputSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    option_id = serializers.IntegerField()


class TutorRequestInputSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()


class TutorFeedbackSerializer(serializers.Serializer):
    """Tutor output — only the note text.

    In diagnosis mode (wrong answer) the note never exposes the misconception or
    answer; in explanation mode (skipped question) it intentionally reveals and
    explains the answer.
    """

    feedback = serializers.CharField()


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
    "TutorRequestInputSerializer",
    "TutorFeedbackSerializer",
    "AttemptAnswer",
    "TestAttempt",
]
