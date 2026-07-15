from django.db import models

CONFIDENCE_CHOICES = [
        ('Low', 'Low'),
        ('High', 'High'),
    ]

class Profession(models.Model):
    name = models.CharField(max_length = 250)
    national_code = models.CharField(max_length = 100)
    ubt_score = models.IntegerField(blank = True, null = True)
    subjects = models.JSONField(default = list, null = True)
    universities = models.JSONField(default = list, null = True)
    sources = models.JSONField(default = list, null = True)
    source_tier = models.IntegerField(blank = True, null = True)
    confidence = models.CharField(
        max_length = 100,
        choices = CONFIDENCE_CHOICES,
    )

    fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields = ["national_code", "name"],
                name = "unique_name_national_code"
            )
        ]

    def __str__(self):
        return f"{self.name}, ({self.national_code})"

