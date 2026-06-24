from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assessments', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='question',
            name='solution',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
