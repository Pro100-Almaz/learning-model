from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("web_harvester", "0004_candidate_claims"),
    ]

    operations = [
        migrations.AddField(
            model_name="candidateclaim",
            name="promoted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="candidateclaim",
            name="promoted_admission_threshold_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
