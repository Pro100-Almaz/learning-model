from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("web_harvester", "0002_profession_harvest_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="profession",
            name="extracted_claims",
            field=models.JSONField(default=dict, null=True),
        ),
    ]
