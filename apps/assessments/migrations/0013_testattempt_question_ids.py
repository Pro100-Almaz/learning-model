from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0012_modefigure"),
    ]

    operations = [
        migrations.AddField(
            model_name="testattempt",
            name="question_ids",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
