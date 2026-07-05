from django.conf import settings
from django.db import models
from django.db.models import Q

class Question(models.Model):
    text = models.TextField()
    image = models.ImageField(upload_to="questions/", null=True, blank=True)
    explanation = models.TextField()
    # Structured, deterministically-computed worked solution (steps + the rolled
    # numbers). The Tutor reads this to diagnose a student's mistake; see
    # math_engine.build_solution for the shape.
    solution = models.JSONField(default=dict, blank=True)
    # SHA-256 of the problem's mathematical identity (topic + rolled numbers),
    # set by the generation Publisher (see math_engine.compute_content_hash).
    # The unique constraint is what stops a batch run from inserting the same
    # problem twice. NULL for hand-authored / seeded questions, which carry no
    # spec and so don't participate in dedup (multiple NULLs are allowed).
    content_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        editable=False,
    )
    difficulty = models.PositiveSmallIntegerField(default=1)
    lesson = models.ForeignKey(
        "content.Lesson",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="questions",
    )
    tags = models.ManyToManyField(
        "content.Tag",
        related_name="questions",
    )

    def __str__(self) -> str:
        return f"Q{self.pk}: {self.text[:40]}"


class AnswerOption(models.Model):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="options",
    )
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    # For a wrong option, the slug of the misconception that produces it (see the
    # blueprint `distractors`); empty for the correct option and untagged
    # distractors. The Tutor reads this to name the student's error. Never
    # exposed to students (see assessments.serializers).
    misconception = models.CharField(max_length=100, blank=True, default="")

    def __str__(self) -> str:
        marker = "*" if self.is_correct else " "
        return f"[{marker}] {self.text[:40]}"


class Test(models.Model):
    TYPE_CHOICES = [
        ("micro", "Micro"),
        ("mock", "Mock"),
        ("diagnostic", "Diagnostic"),
    ]

    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=200)
    lesson = models.ForeignKey(
        "content.Lesson",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tests",
    )
    time_limit_sec = models.PositiveIntegerField(null=True, blank=True)
    questions = models.ManyToManyField(
        Question,
        through="TestQuestion",
        related_name="tests",
    )

    def __str__(self) -> str:
        return f"{self.type}: {self.title}"


class TestQuestion(models.Model):
    test = models.ForeignKey(Test, on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        unique_together = ("test", "question")

    def __str__(self) -> str:
        return f"{self.test_id}/{self.question_id} @ {self.order}"


class TestAttempt(models.Model):
    # Where this attempt came from. Test-based flows leave the default; the
    # chapter ladder (07_Chapter_Ladder_Spec.md) picks questions dynamically, so
    # its attempts carry source="ladder" with a NULL test (no synthetic Test row).
    SOURCE_CHOICES = [
        ("test", "Test"),
        ("ladder", "Chapter ladder"),
    ]

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    # Nullable so a ladder attempt (dynamic question set) needs no predefined
    # Test. Guarded in services.finish_attempt / _trigger_roadmap_hooks.
    test = models.ForeignKey(
        Test,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="test")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["student", "is_completed"])]

    def __str__(self) -> str:
        return f"Attempt<{self.pk}> student={self.student_id} test={self.test_id}"


class AttemptAnswer(models.Model):
    attempt = models.ForeignKey(
        TestAttempt,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_option = models.ForeignKey(
        AnswerOption,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    is_correct = models.BooleanField(default=False)

    class Meta:
        unique_together = ("attempt", "question")

    def __str__(self) -> str:
        return f"Ans<{self.pk}> attempt={self.attempt_id} q={self.question_id}"


class TutorNote(models.Model):
    """A cached Tutor 'margin note' for one wrong answer.

    The note depends only on the question and which wrong option was chosen — not
    on the individual student — so one row is shared across every student who
    picks that option. This is the durable replacement for the old process-local
    cache: it survives restarts, is shared across workers, and makes the notes
    queryable. Written/read exclusively by ``services.get_tutor_feedback``.
    """

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="tutor_notes",
    )
    selected_option = models.ForeignKey(
        AnswerOption,
        on_delete=models.CASCADE,
        related_name="tutor_notes",
        null = True,
        blank = True,
    )
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("question", "selected_option")
        constraints = [
            models.UniqueConstraint(
                fields = ["question"],
                condition = Q(selected_option__isnull=True),
                name = "unique_tutor_explanation_per_question",
            ),
        ]

    def __str__(self) -> str:
        return f"TutorNote<{self.pk}> q={self.question_id} opt={self.selected_option_id}"
