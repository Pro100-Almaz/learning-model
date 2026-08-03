from django.db import models

from web_harvester.source_policy import FieldType, SourceStrategy

CONFIDENCE_CHOICES = [
    ("Low", "Low"),
    ("High", "High"),
]
FIELD_TYPE_CHOICES = [
    (field_type.value, field_type.name.replace("_", " ").title())
    for field_type in FieldType
]
SOURCE_STRATEGY_CHOICES = [
    (strategy.value, strategy.name.replace("_", " ").title())
    for strategy in SourceStrategy
]


class Profession(models.Model):
    name = models.CharField(max_length=250)
    national_code = models.CharField(max_length=100)
    field_type = models.CharField(
        max_length=32,
        choices=FIELD_TYPE_CHOICES,
        blank=True,
        null=True,
    )
    ubt_score = models.IntegerField(blank=True, null=True)
    subjects = models.JSONField(default=list, null=True)
    universities = models.JSONField(default=list, null=True)
    sources = models.JSONField(default=list, null=True)
    source_strategy = models.CharField(
        max_length=16,
        choices=SOURCE_STRATEGY_CHOICES,
        blank=True,
        null=True,
    )
    source_tier = models.IntegerField(blank=True, null=True)
    confidence = models.CharField(
        max_length=100,
        choices=CONFIDENCE_CHOICES,
    )
    fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["national_code", "name"],
                name="unique_name_national_code",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        field_type__isnull=True,
                        source_strategy__isnull=True,
                    )
                    | models.Q(
                        field_type__isnull=False,
                        source_strategy=SourceStrategy.PRIMARY.value,
                        source_tier=1,
                        confidence="High",
                    )
                    | models.Q(
                        field_type__isnull=False,
                        source_strategy=SourceStrategy.FALLBACK.value,
                        source_tier=2,
                        confidence="Low",
                    )
                ),
                name="valid_harvest_source_metadata",
            ),
        ]

    def __str__(self):
        return f"{self.name}, ({self.national_code})"
