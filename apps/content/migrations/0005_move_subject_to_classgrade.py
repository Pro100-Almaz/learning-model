"""Move subject from Module onto ClassGrade.

New hierarchy: Lesson -> Module -> ClassGrade -> Subject. Each existing
ClassGrade inherits the subject of the modules under it (falling back to the
seeded ``profile_math`` subject), after which ``Module.subject`` is dropped.

Note: a ClassGrade now carries a single subject. If a grade previously mixed
modules of several subjects, this backfill collapses it to the first module's
subject — split such grades manually afterwards if needed.
"""

import django.db.models.deletion
from django.db import migrations, models


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0004_classgrade_module_class_grade'),
    ]

    operations = [
        migrations.AddField(
            model_name='classgrade',
            name='subject',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to='content.subject',
            ),
        ),
        migrations.AlterField(
            model_name='classgrade',
            name='subject',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to='content.subject',
            ),
        ),
        migrations.RemoveField(
            model_name='module',
            name='subject',
        ),
        migrations.AlterField(
            model_name='module',
            name='class_grade',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='modules',
                to='content.classgrade',
            ),
        ),
    ]
