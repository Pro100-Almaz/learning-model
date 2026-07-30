from django.db import models

from apps.content.services import calculate_student_progress, invalidate_cache_for_student_and_lesson
from apps.users.models import CustomUser


class Subject(models.Model):
    name = models.CharField(max_length=50)
    slug = models.SlugField(max_length=50, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def student_progress(self, student: CustomUser) -> int:
        lessons = Lesson.objects.filter(module__class_grade__subject=self)
        cache_key = f"student_{student.id}:subject_{self.id}"
        return calculate_student_progress(lessons, student, cache_key)


class ClassGrade(models.Model):
    grade = models.PositiveIntegerField()
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="classes")

    def __str__(self) -> str:
        return str(self.grade)

    def student_progress(self, student: CustomUser) -> int:
        lessons = Lesson.objects.filter(module__class_grade=self)
        cache_key = f"student_{student.id}:class_grade_{self.id}"
        result = calculate_student_progress(lessons, student, cache_key)
        return result


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

    def student_progress(self, student: CustomUser) -> int:
        lessons = Lesson.objects.filter(module=self)
        cache_key = f"student_{student.id}:module_{self.id}"
        return calculate_student_progress(lessons, student, cache_key)


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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        invalidate_cache_for_student_and_lesson(self.id)


class Tag(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
