from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("web_harvester", "0003_profession_extracted_claims"),
    ]

    operations = [
        migrations.CreateModel(
            name="CandidateClaim",
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
                ("profession_name", models.CharField(max_length=250)),
                ("program_group_code", models.CharField(max_length=32)),
                ("target_year", models.PositiveIntegerField()),
                (
                    "field_type",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("medicine", "Medicine"),
                            ("education", "Education"),
                            ("technical", "Technical"),
                            ("creative", "Creative"),
                            ("business_economics", "Business And Economics"),
                            ("humanities_law", "Humanities And Law"),
                            ("agriculture", "Agriculture"),
                            ("sport_tourism", "Sport And Tourism"),
                            ("military_security", "Military And Security"),
                        ],
                        max_length=32,
                        null=True,
                    ),
                ),
                (
                    "source_strategy",
                    models.CharField(
                        blank=True,
                        choices=[("primary", "Primary"), ("fallback", "Fallback")],
                        max_length=16,
                        null=True,
                    ),
                ),
                ("source_tier", models.IntegerField(blank=True, null=True)),
                (
                    "confidence",
                    models.CharField(
                        choices=[("Low", "Low"), ("High", "High")],
                        max_length=100,
                    ),
                ),
                (
                    "claim_type",
                    models.CharField(
                        choices=[
                            ("program_identity", "Program identity"),
                            ("threshold", "Threshold"),
                            ("subject_requirement", "Subject requirement"),
                            ("university_offering", "University offering"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("accepted", "Accepted by validation"),
                            ("rejected", "Rejected by validation"),
                            ("promoted", "Promoted to canonical data"),
                            ("dismissed", "Dismissed after review"),
                        ],
                        max_length=16,
                    ),
                ),
                ("source_url", models.URLField(max_length=1000)),
                ("evidence_excerpt", models.TextField()),
                ("evidence_location", models.TextField(blank=True, default="")),
                ("payload", models.JSONField(default=dict)),
                ("payload_fingerprint", models.CharField(max_length=64)),
                ("rejection_reason", models.TextField(blank=True, default="")),
                ("rejection_detail", models.TextField(blank=True, default="")),
                ("review_note", models.TextField(blank=True, default="")),
                ("harvested_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name="candidateclaim",
            constraint=models.UniqueConstraint(
                fields=(
                    "program_group_code",
                    "target_year",
                    "claim_type",
                    "source_url",
                    "evidence_excerpt",
                    "payload_fingerprint",
                ),
                name="unique_candidate_claim_payload",
            ),
        ),
        migrations.AddIndex(
            model_name="candidateclaim",
            index=models.Index(
                fields=["status", "target_year"],
                name="cand_status_year_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="candidateclaim",
            index=models.Index(
                fields=["program_group_code", "target_year"],
                name="cand_group_year_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="candidateclaim",
            index=models.Index(
                fields=["claim_type", "status"],
                name="cand_type_status_idx",
            ),
        ),
    ]
