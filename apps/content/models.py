from django.db import models


class Subject(models.Model):
    name = models.CharField(max_length=50)
    slug = models.SlugField(max_length=50, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ClassGrade(models.Model):
    grade = models.PositiveIntegerField()
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="classes")

    def __str__(self) -> str:
        return str(self.grade)


class Module(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    order = models.PositiveIntegerField(default=0)
    description = models.TextField(null=True, blank=True)
    class_grade = models.ForeignKey(
        "content.ClassGrade",
        on_delete=models.CASCADE,
        related_name="modules",
    )

    class Meta:
        ordering = ["order"]

    def __str__(self) -> str:
        return self.title


class Lesson(models.Model):
    PROVIDER_CHOICES = [
        ("youtube", "YouTube"),
        ("vimeo", "Vimeo"),
    ]

    module = models.ForeignKey(
        Module,
        on_delete=models.CASCADE,
        related_name="lessons",
    )
    # The subject area this lesson belongs to. Coarse on purpose: analytics and
    # the roadmap ladder group by tag, and the UBT engine ships 16 tags across
    # 58 topics ("planimetriya" covers eight of them). Used for grouping and as
    # the question<->lesson fallback for MAIQE questions, which carry no topic.
    tag = models.ForeignKey(
        "content.Tag",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lessons",
    )
    # The generator topic this lesson teaches, e.g. "ubt_triangle_properties".
    # This is the precise question<->lesson link and it is what the UBT publisher
    # matches on (see assessments.services._resolve_lesson). Tag alone cannot do
    # the job: eight planimetry topics share one tag, so matching on tag would
    # pile all eight topics' questions onto whichever lesson sorted first.
    # Blank for hand-authored lessons and for MAIQE, which fall back to tag.
    topic = models.CharField(max_length=100, blank=True, db_index=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    video_url = models.URLField()
    video_provider = models.CharField(
        max_length=10,
        choices=PROVIDER_CHOICES,
        default="youtube",
    )
    duration_sec = models.PositiveIntegerField(default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self) -> str:
        return self.title


class Tag(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
