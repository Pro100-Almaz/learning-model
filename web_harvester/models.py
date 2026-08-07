from django.core.exceptions import ValidationError
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
CLAIM_TYPE_CHOICES = [
    ("program_identity", "Program identity"),
    ("threshold", "Threshold"),
    ("subject_requirement", "Subject requirement"),
    ("university_offering", "University offering"),
]
CLAIM_STATUS_CHOICES = [
    ("accepted", "Accepted by validation"),
    ("rejected", "Rejected by validation"),
    ("promoted", "Promoted to canonical data"),
    ("dismissed", "Dismissed after review"),
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
    extracted_claims = models.JSONField(default=dict, null=True)
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

class CandidateClaim(models.Model):
    """Durable staging row for one harvested claim, accepted or rejected."""

    profession_name = models.CharField(max_length=250)
    program_group_code = models.CharField(max_length=32)
    target_year = models.PositiveIntegerField()
    field_type = models.CharField(
        max_length=32,
        choices=FIELD_TYPE_CHOICES,
        blank=True,
        null=True,
    )
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
    claim_type = models.CharField(
        max_length=32,
        choices=CLAIM_TYPE_CHOICES,
    )
    status = models.CharField(
        max_length=16,
        choices=CLAIM_STATUS_CHOICES,
    )
    source_url = models.URLField(max_length=1000)
    evidence_excerpt = models.TextField()
    evidence_location = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict)
    payload_fingerprint = models.CharField(max_length=64)
    rejection_reason = models.TextField(blank=True, default="")
    rejection_detail = models.TextField(blank=True, default="")
    review_note = models.TextField(blank=True, default="")
    harvested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    promoted_at = models.DateTimeField(null=True, blank=True)
    promoted_admission_threshold_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "program_group_code",
                    "target_year",
                    "claim_type",
                    "source_url",
                    "evidence_excerpt",
                    "payload_fingerprint",
                ],
                name="unique_candidate_claim_payload",
            )
        ]
        indexes = [
            models.Index(fields=["status", "target_year"], name="cand_status_year_idx"),
            models.Index(
                fields=["program_group_code", "target_year"],
                name="cand_group_year_idx",
            ),
            models.Index(fields=["claim_type", "status"], name="cand_type_status_idx"),
        ]

    def __str__(self):
        return (
            f"{self.claim_type} {self.status} "
            f"({self.program_group_code}, {self.target_year})"
        )

    def clean(self):
        errors = {}

        # evidence_excerpt must not be blank or whitespace
        if not self.evidence_excerpt or not self.evidence_excerpt.strip():
            errors["evidence_excerpt"] = "Evidence excerpt must not be blank."

        # Accepted/promoted claims cannot have rejection information
        if self.status in ("accepted", "promoted"):
            if self.rejection_reason:
                errors["rejection_reason"] = (
                    "Accepted/promoted claims cannot have a rejection reason."
                )

        # Rejected/dismissed claims must have rejection_reason
        elif self.status in ("rejected", "dismissed"):
            if not self.rejection_reason or not self.rejection_reason.strip():
                errors["rejection_reason"] = (
                    "Rejected/dismissed claims must have a rejection reason."
                )

        # source_strategy -> source_tier/confidence consistency
        if self.source_strategy == SourceStrategy.PRIMARY.value:
            if self.source_tier != 1:
                errors["source_tier"] = (
                    "Primary strategy requires source_tier = 1."
                )

            if self.confidence != "High":
                errors["confidence"] = (
                    "Primary strategy requires confidence = 'High'."
                )

        elif self.source_strategy == SourceStrategy.FALLBACK.value:
            if self.source_tier != 2:
                errors["source_tier"] = (
                    "Fallback strategy requires source_tier = 2."
                )

            if self.confidence != "Low":
                errors["confidence"] = (
                    "Fallback strategy requires confidence = 'Low'."
                )

        if errors:
            raise ValidationError(errors)
