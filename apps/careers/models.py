from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.careers.identity import normalize_identifier


class ScoreType(models.TextChoices):
    LEGAL_MINIMUM = "legal_minimum", "Legal admission minimum"
    UNIVERSITY_MINIMUM = "university_minimum", "University admission minimum"
    HISTORICAL_GRANT_CUTOFF = (
        "historical_grant_cutoff",
        "Historical grant cutoff",
    )


class AdmissionRoute(models.TextChoices):
    STANDARD = "standard", "Standard"
    SHORTENED_RELATED = "shortened_related", "Shortened related program"
    CREATIVE = "creative", "Creative"
    MILITARY_SPECIAL = "military_special", "Military or special institution"
    OTHER = "other", "Other"


class FundingType(models.TextChoices):
    GRANT = "grant", "Grant"
    PAID = "paid", "Paid"
    GRANT_AND_PAID = "grant_and_paid", "Grant and paid"


class ApplicantBackground(models.TextChoices):
    GENERAL_SECONDARY = "general_secondary", "General secondary school graduate"
    TVET_POSTSECONDARY = "tvet_postsecondary", "TVET or postsecondary graduate"
    PREVIOUS_HIGHER = "previous_higher", "Previous higher-education graduate"
    OTHER = "other", "Other"


class InstructionLanguage(models.TextChoices):
    KAZAKH = "kk", "Kazakh"
    RUSSIAN = "ru", "Russian"
    ENGLISH = "en", "English"
    LANGUAGE_INDEPENDENT = "language_independent", "Language independent"


class AliasLanguage(models.TextChoices):
    KAZAKH = "kk", "Kazakh"
    RUSSIAN = "ru", "Russian"
    ENGLISH = "en", "English"


class ProfessionIdentifierScheme(models.TextChoices):
    NATIONAL_OCCUPATION_CLASSIFIER = (
        "national_occupation_classifier",
        "National occupation classifier",
    )
    LEGACY_OCCUPATION_CLASSIFIER = (
        "legacy_occupation_classifier",
        "Legacy occupation classifier",
    )
    ALTERNATIVE_NAME = "alternative_name", "Alternative profession name"


class ProgramIdentifierScheme(models.TextChoices):
    CURRENT_GROUP_CODE = "current_group_code", "Current program-group code"
    LEGACY_SPECIALTY_CODE = "legacy_specialty_code", "Legacy specialty code"
    ALTERNATIVE_CODE = "alternative_code", "Alternative program-group code"
    ALTERNATIVE_NAME = "alternative_name", "Alternative program-group name"


class ProfessionProgramRelationship(models.TextChoices):
    DIRECT = "direct", "Direct educational route"
    RELATED = "related", "Related educational route"


class University(models.Model):
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)

    def __str__(self) -> str:
        return self.name


class EducationalProgramGroup(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class Profession(models.Model):
    slug = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    program_groups = models.ManyToManyField(
        EducationalProgramGroup,
        through="ProfessionProgramGroup",
        related_name="professions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "slug"]

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
    program_group = models.ForeignKey(
        EducationalProgramGroup,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="specialties",
    )

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


class AdmissionSource(models.Model):
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=500, blank=True)
    publisher = models.CharField(max_length=250, blank=True)
    publication_date = models.DateField(null=True, blank=True)
    retrieved_at = models.DateTimeField(default=timezone.now)
    content_fingerprint = models.CharField(max_length=64)
    original_language = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["url", "content_fingerprint"],
                name="unique_admission_source_snapshot",
            )
        ]

    def __str__(self) -> str:
        label = self.publisher or self.title or "Admission source"
        return f"{label}: {self.url}"


class ProfessionIdentifier(models.Model):
    profession = models.ForeignKey(
        Profession,
        on_delete=models.PROTECT,
        related_name="identifiers",
    )
    scheme = models.CharField(max_length=40, choices=ProfessionIdentifierScheme.choices)
    value = models.CharField(max_length=250)
    normalized_value = models.CharField(max_length=250, editable=False)
    language = models.CharField(
        max_length=2,
        choices=AliasLanguage.choices,
        blank=True,
    )
    source = models.ForeignKey(
        AdmissionSource,
        on_delete=models.PROTECT,
        related_name="profession_identifiers",
    )
    evidence_excerpt = models.TextField()
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scheme", "normalized_value"],
                name="unique_profession_identifier",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_from__isnull=True)
                    | models.Q(valid_to__isnull=True)
                    | models.Q(valid_to__gte=models.F("valid_from"))
                ),
                name="profession_identifier_dates_ordered",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors = self._identity_errors()
        if errors:
            raise ValidationError(errors)

    def _identity_errors(self) -> dict[str, list[str]]:
        errors: dict[str, list[str]] = {}
        self.normalized_value = normalize_identifier(self.value)
        if not self.normalized_value:
            errors.setdefault("value", []).append("Identifier must not be blank.")
        if not self.evidence_excerpt.strip():
            errors.setdefault("evidence_excerpt", []).append(
                "Evidence must not be blank."
            )
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            errors.setdefault("valid_to", []).append(
                "The end of the validity period precedes its start."
            )
        if self.scheme == ProfessionIdentifierScheme.ALTERNATIVE_NAME:
            if not self.language:
                errors.setdefault("language", []).append(
                    "Alternative names require a language."
                )
        elif self.language:
            errors.setdefault("language", []).append(
                "Classifier identifiers must not have a language."
            )
        return errors

    def save(self, *args, **kwargs) -> None:
        self.normalized_value = normalize_identifier(self.value)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.get_scheme_display()}: {self.value}"


class ProgramGroupAlias(models.Model):
    program_group = models.ForeignKey(
        EducationalProgramGroup,
        on_delete=models.PROTECT,
        related_name="aliases",
    )
    scheme = models.CharField(
        max_length=32,
        choices=[
            choice
            for choice in ProgramIdentifierScheme.choices
            if choice[0] != ProgramIdentifierScheme.CURRENT_GROUP_CODE
        ],
    )
    value = models.CharField(max_length=250)
    normalized_value = models.CharField(max_length=250, editable=False)
    language = models.CharField(
        max_length=2,
        choices=AliasLanguage.choices,
        blank=True,
    )
    source = models.ForeignKey(
        AdmissionSource,
        on_delete=models.PROTECT,
        related_name="program_group_aliases",
    )
    evidence_excerpt = models.TextField()
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scheme", "normalized_value"],
                name="unique_program_group_alias",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_from__isnull=True)
                    | models.Q(valid_to__isnull=True)
                    | models.Q(valid_to__gte=models.F("valid_from"))
                ),
                name="program_group_alias_dates_ordered",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list[str]] = {}
        self.normalized_value = normalize_identifier(self.value)
        if not self.normalized_value:
            errors.setdefault("value", []).append("Alias must not be blank.")
        if not self.evidence_excerpt.strip():
            errors.setdefault("evidence_excerpt", []).append(
                "Evidence must not be blank."
            )
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            errors.setdefault("valid_to", []).append(
                "The end of the validity period precedes its start."
            )
        if self.scheme == ProgramIdentifierScheme.ALTERNATIVE_NAME:
            if not self.language:
                errors.setdefault("language", []).append(
                    "Alternative names require a language."
                )
        elif self.language:
            errors.setdefault("language", []).append(
                "Code aliases must not have a language."
            )
        conflicting_group = (
            EducationalProgramGroup.objects.filter(code__iexact=self.normalized_value)
            .exclude(pk=self.program_group_id)
            .exists()
        )
        if conflicting_group:
            errors.setdefault("value", []).append(
                "This alias conflicts with another program group's current code."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.normalized_value = normalize_identifier(self.value)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.get_scheme_display()}: {self.value} -> {self.program_group.code}"


class ProfessionProgramGroup(models.Model):
    profession = models.ForeignKey(
        Profession,
        on_delete=models.PROTECT,
        related_name="program_group_links",
    )
    program_group = models.ForeignKey(
        EducationalProgramGroup,
        on_delete=models.PROTECT,
        related_name="profession_links",
    )
    relationship_type = models.CharField(
        max_length=16,
        choices=ProfessionProgramRelationship.choices,
    )
    source = models.ForeignKey(
        AdmissionSource,
        on_delete=models.PROTECT,
        related_name="profession_program_links",
    )
    evidence_excerpt = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profession", "program_group"],
                name="unique_profession_program_group",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if not self.evidence_excerpt.strip():
            raise ValidationError({"evidence_excerpt": "Evidence must not be blank."})

    def __str__(self) -> str:
        return (
            f"{self.profession.name} -> {self.program_group.code} "
            f"({self.get_relationship_type_display()})"
        )


class AdmissionThreshold(models.Model):
    program_group = models.ForeignKey(
        EducationalProgramGroup,
        on_delete=models.PROTECT,
        related_name="admission_thresholds",
    )
    university = models.ForeignKey(
        University,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="admission_thresholds",
    )
    specialty = models.ForeignKey(
        Specialty,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="admission_thresholds",
    )
    source = models.ForeignKey(
        AdmissionSource,
        on_delete=models.PROTECT,
        related_name="threshold_claims",
    )
    year = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(2000), MaxValueValidator(2100)]
    )
    score = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(140)]
    )
    score_type = models.CharField(max_length=32, choices=ScoreType.choices)
    admission_route = models.CharField(max_length=32, choices=AdmissionRoute.choices)
    admission_route_details = models.CharField(max_length=250, blank=True)
    funding_type = models.CharField(max_length=32, choices=FundingType.choices)
    applicant_background = models.CharField(
        max_length=32,
        choices=ApplicantBackground.choices,
    )
    applicant_background_details = models.CharField(max_length=250, blank=True)
    quota_category = models.CharField(max_length=100)
    instruction_language = models.CharField(
        max_length=32,
        choices=InstructionLanguage.choices,
    )
    evidence_excerpt = models.TextField()
    evidence_location = models.CharField(max_length=250, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(score__gte=1, score__lte=140),
                name="admission_score_between_1_and_140",
            ),
            models.CheckConstraint(
                condition=models.Q(year__gte=2000, year__lte=2100),
                name="admission_year_between_2000_and_2100",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(score_type=ScoreType.UNIVERSITY_MINIMUM)
                    | models.Q(university__isnull=False)
                ),
                name="university_minimum_requires_university",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(score_type=ScoreType.HISTORICAL_GRANT_CUTOFF)
                    | models.Q(funding_type=FundingType.GRANT)
                ),
                name="grant_cutoff_requires_grant_funding",
            ),
            models.UniqueConstraint(
                fields=[
                    "program_group",
                    "university",
                    "specialty",
                    "year",
                    "score",
                    "score_type",
                    "admission_route",
                    "admission_route_details",
                    "funding_type",
                    "applicant_background",
                    "applicant_background_details",
                    "quota_category",
                    "instruction_language",
                    "source",
                ],
                name="unique_admission_threshold_claim",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(
                fields=["program_group", "year", "score_type"],
                name="adm_group_year_type_idx",
            ),
            models.Index(
                fields=["university", "year", "score_type"],
                name="adm_uni_year_type_idx",
            ),
            models.Index(
                fields=["verified_at", "year"],
                name="adm_verified_year_idx",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, list[str]] = {}

        def add_error(field: str, message: str) -> None:
            errors.setdefault(field, []).append(message)

        if self.score_type == ScoreType.UNIVERSITY_MINIMUM and not self.university_id:
            add_error("university", "A university minimum requires a university.")

        if (
            self.score_type == ScoreType.HISTORICAL_GRANT_CUTOFF
            and self.funding_type != FundingType.GRANT
        ):
            add_error("funding_type", "A historical grant cutoff requires grant funding.")

        if (
            self.admission_route == AdmissionRoute.OTHER
            and not self.admission_route_details.strip()
        ):
            add_error(
                "admission_route_details",
                "Details are required for an 'other' admission route.",
            )

        if (
            self.applicant_background == ApplicantBackground.OTHER
            and not self.applicant_background_details.strip()
        ):
            add_error(
                "applicant_background_details",
                "Details are required for an 'other' applicant background.",
            )

        if self.specialty_id:
            if not self.university_id:
                add_error("university", "A specialty requires a university.")
            elif self.specialty.university_id != self.university_id:
                add_error(
                    "specialty",
                    "The specialty does not belong to the selected university.",
                )

            if (
                self.specialty.program_group_id
                and self.specialty.program_group_id != self.program_group_id
            ):
                add_error(
                    "specialty",
                    "The specialty does not belong to the selected program group.",
                )

        if not self.evidence_excerpt.strip():
            add_error("evidence_excerpt", "Evidence must not be blank.")

        if not self.quota_category.strip():
            add_error("quota_category", "Quota category must not be blank.")

        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return (
            f"{self.program_group.code} {self.year} "
            f"{self.get_score_type_display()} — {self.score}"
        )
