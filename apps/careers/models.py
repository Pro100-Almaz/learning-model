from django.db import models


class University(models.Model):
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)

    def __str__(self) -> str:
        return self.name


class Specialty(models.Model):
    university = models.ForeignKey(
        University,
        on_delete=models.CASCADE,
        related_name="specialties",
    )
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20)
    required_subjects = models.JSONField(default=list)

    class Meta:
        unique_together = ("university", "code")

    def __str__(self) -> str:
        return f"{self.name} ({self.university.code})"


class GrantThreshold(models.Model):
    specialty = models.ForeignKey(
        Specialty,
        on_delete=models.CASCADE,
        related_name="thresholds",
    )
    year = models.PositiveIntegerField()
    min_score = models.PositiveIntegerField()

    class Meta:
        unique_together = ("specialty", "year")
        indexes = [models.Index(fields=["year"])]

    def __str__(self) -> str:
        return f"{self.specialty_id} {self.year} -> {self.min_score}"
