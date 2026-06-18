from django.conf import settings
from django.db import models


class XPEvent(models.Model):
    REASON_CHOICES = [
        ("video", "Video watched"),
        ("correct_answer", "Correct answer"),
    ]

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="xp_events",
    )
    amount = models.PositiveIntegerField()
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"XP<{self.pk}> +{self.amount} {self.reason} u={self.student_id}"


class StudentProgress(models.Model):
    student = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="progress",
    )
    total_xp = models.PositiveIntegerField(default=0)
    level_code = models.CharField(max_length=20, default="novice")

    def __str__(self) -> str:
        return f"Progress<{self.student_id}> {self.total_xp}xp {self.level_code}"


class Streak(models.Model):
    student = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="streak",
    )
    current_streak = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_active_date = models.DateField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Streak<{self.student_id}> cur={self.current_streak} max={self.longest_streak}"
