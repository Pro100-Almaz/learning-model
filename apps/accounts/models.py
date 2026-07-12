from django.conf import settings
from django.db import models


class StudentProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    target_university = models.ForeignKey(
        "careers.University",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="target_profiles",
    )
    target_specialty = models.ForeignKey(
        "careers.Specialty",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="target_profiles",
    )
    target_score = models.PositiveIntegerField(null=True, blank=True)
    subjects = models.ManyToManyField(
        "content.Subject",
        related_name="students",
        blank=True,
    )
    onboarding_completed = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"Profile<{self.user_id}>"


class ExpectedScore(models.Model):
    profile = models.ForeignKey(
        "accounts.StudentProfile",
        on_delete=models.CASCADE,
        related_name="expected_scores",
    )
    subject = models.ForeignKey(
        "content.Subject",
        on_delete=models.CASCADE,
        related_name="expected_scores",
    )
    score = models.PositiveIntegerField()

    class Meta:
        unique_together = ("profile", "subject")

    def __str__(self) -> str:
        return f"{self.subject}={self.score} (profile {self.profile_id})"
