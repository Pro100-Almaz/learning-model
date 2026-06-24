from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assessments', '0002_question_solution'),
    ]

    operations = [
        migrations.AddField(
            model_name='answeroption',
            name='misconception',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
    ]
