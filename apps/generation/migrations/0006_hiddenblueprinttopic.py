from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0005_alter_generationjob_language"),
    ]

    operations = [
        migrations.CreateModel(
            name="HiddenBlueprintTopic",
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
                ("topic", models.CharField(max_length=100, unique=True)),
            ],
        ),
    ]
