from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assessments", "0003_answeroption_misconception"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="content_hash",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=64,
                null=True,
                unique=True,
            ),
        ),
    ]
