from django.db import models


class Module(models.Model):
    SUBJECT_CHOICES = [
        ("math_literacy", "Мат. грамотность"),
        ("profile_math", "Профильная математика"),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    order = models.PositiveIntegerField(default=0)
    subject = models.CharField(max_length=20, choices=SUBJECT_CHOICES)

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
    # The topic this lesson teaches. Generated questions are linked to a lesson
    # by matching their tag to this field (see assessments.services), and it
    # gives the roadmap an explicit lesson<->topic link. Nullable so existing
    # lessons keep working until they're tagged.
    tag = models.ForeignKey(
        "content.Tag",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lessons",
    )
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

    def __str__(self) -> str:
        return self.name
