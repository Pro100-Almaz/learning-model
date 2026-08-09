import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("careers", "0002_specialty_name_en_specialty_name_kk_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdmissionSource",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("url", models.URLField(max_length=1000)),
                ("title", models.CharField(blank=True, max_length=500)),
                ("publisher", models.CharField(blank=True, max_length=250)),
                ("publication_date", models.DateField(blank=True, null=True)),
                (
                    "retrieved_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("content_fingerprint", models.CharField(max_length=64)),
                ("original_language", models.CharField(blank=True, max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("url", "content_fingerprint"),
                        name="unique_admission_source_snapshot",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="EducationalProgramGroup",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("code", models.CharField(max_length=20, unique=True)),
                ("name", models.CharField(max_length=200)),
                ("name_en", models.CharField(max_length=200, null=True)),
                ("name_kk", models.CharField(max_length=200, null=True)),
                ("name_ru", models.CharField(max_length=200, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["code"]},
        ),
        migrations.AddField(
            model_name="specialty",
            name="program_group",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="specialties",
                to="careers.educationalprogramgroup",
            ),
        ),
        migrations.CreateModel(
            name="AdmissionThreshold",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "year",
                    models.PositiveSmallIntegerField(
                        validators=[
                            django.core.validators.MinValueValidator(2000),
                            django.core.validators.MaxValueValidator(2100),
                        ]
                    ),
                ),
                (
                    "score",
                    models.PositiveSmallIntegerField(
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(140),
                        ]
                    ),
                ),
                (
                    "score_type",
                    models.CharField(
                        choices=[
                            ("legal_minimum", "Legal admission minimum"),
                            ("university_minimum", "University admission minimum"),
                            (
                                "historical_grant_cutoff",
                                "Historical grant cutoff",
                            ),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "admission_route",
                    models.CharField(
                        choices=[
                            ("standard", "Standard"),
                            ("shortened_related", "Shortened related program"),
                            ("creative", "Creative"),
                            (
                                "military_special",
                                "Military or special institution",
                            ),
                            ("other", "Other"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "admission_route_details",
                    models.CharField(blank=True, max_length=250),
                ),
                (
                    "funding_type",
                    models.CharField(
                        choices=[
                            ("grant", "Grant"),
                            ("paid", "Paid"),
                            ("grant_and_paid", "Grant and paid"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "applicant_background",
                    models.CharField(
                        choices=[
                            (
                                "general_secondary",
                                "General secondary school graduate",
                            ),
                            (
                                "tvet_postsecondary",
                                "TVET or postsecondary graduate",
                            ),
                            (
                                "previous_higher",
                                "Previous higher-education graduate",
                            ),
                            ("other", "Other"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "applicant_background_details",
                    models.CharField(blank=True, max_length=250),
                ),
                ("quota_category", models.CharField(max_length=100)),
                (
                    "instruction_language",
                    models.CharField(
                        choices=[
                            ("kk", "Kazakh"),
                            ("ru", "Russian"),
                            ("en", "English"),
                            ("language_independent", "Language independent"),
                        ],
                        max_length=32,
                    ),
                ),
                ("evidence_excerpt", models.TextField()),
                (
                    "evidence_location",
                    models.CharField(blank=True, max_length=250),
                ),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "program_group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admission_thresholds",
                        to="careers.educationalprogramgroup",
                    ),
                ),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="threshold_claims",
                        to="careers.admissionsource",
                    ),
                ),
                (
                    "specialty",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admission_thresholds",
                        to="careers.specialty",
                    ),
                ),
                (
                    "university",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admission_thresholds",
                        to="careers.university",
                    ),
                ),
            ],
            options={
                "indexes": [
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
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("score__gte", 1), ("score__lte", 140)),
                        name="admission_score_between_1_and_140",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("year__gte", 2000), ("year__lte", 2100)),
                        name="admission_year_between_2000_and_2100",
                    ),
                    models.CheckConstraint(
                        condition=(
                            ~models.Q(("score_type", "university_minimum"))
                            | models.Q(("university__isnull", False))
                        ),
                        name="university_minimum_requires_university",
                    ),
                    models.CheckConstraint(
                        condition=(
                            ~models.Q(("score_type", "historical_grant_cutoff"))
                            | models.Q(("funding_type", "grant"))
                        ),
                        name="grant_cutoff_requires_grant_funding",
                    ),
                    models.UniqueConstraint(
                        fields=(
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
                        ),
                        name="unique_admission_threshold_claim",
                        nulls_distinct=False,
                    ),
                ],
            },
        ),
    ]
