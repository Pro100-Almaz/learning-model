"""Add ClassGrade and a non-null Module.class_grade FK.

Existing modules predate class grades, so a default grade (11 — the ENT exam
year) is seeded and backfilled onto every existing Module before the FK is
tightened to non-null.
"""

import django.db.models.deletion
from django.db import migrations, models

def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0003_subject_fk'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClassGrade',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('grade', models.PositiveIntegerField()),
            ],
        ),
        migrations.AddField(
            model_name='module',
            name='class_grade',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='modules',
                to='content.classgrade',
            ),
        ),
        migrations.AlterField(
            model_name='module',
            name='class_grade',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='modules',
                to='content.classgrade',
            ),
        ),
    ]
