from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("web_harvester", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="profession",
            name="field_type",
            field=models.CharField(
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
        migrations.AddField(
            model_name="profession",
            name="source_strategy",
            field=models.CharField(
                blank=True,
                choices=[
                    ("primary", "Primary"),
                    ("fallback", "Fallback"),
                ],
                max_length=16,
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="profession",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        field_type__isnull=True,
                        source_strategy__isnull=True,
                    )
                    | models.Q(
                        confidence="High",
                        field_type__isnull=False,
                        source_strategy="primary",
                        source_tier=1,
                    )
                    | models.Q(
                        confidence="Low",
                        field_type__isnull=False,
                        source_strategy="fallback",
                        source_tier=2,
                    )
                ),
                name="valid_harvest_source_metadata",
            ),
        ),
    ]
