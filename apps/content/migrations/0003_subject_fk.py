"""Introduce content.Subject and turn Module.subject into a FK to it.

The old ``Module.subject`` was a choices CharField whose values
("profile_math" / "math_literacy") are already slug-shaped, so existing rows
remap cleanly onto the seeded Subject rows. Any Module whose old value has no
matching Subject is deleted so the resulting non-null FK never dangles.
"""

import django.db.models.deletion
from django.db import migrations, models


def remap_module_subjects(apps, schema_editor):
    Module = apps.get_model("content", "Module")
    Subject = apps.get_model("content", "Subject")
    by_slug = {s.slug: s for s in Subject.objects.all()}
    for module in Module.objects.all():
        subject = by_slug.get(module.subject_old)
        if subject is None:
            # Unrecognised legacy value — drop the row to avoid a dangling FK.
            module.delete()
        else:
            module.subject_fk = subject
            module.save(update_fields=["subject_fk"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0002_lesson_tag'),
    ]

    operations = [
        migrations.CreateModel(
            name='Subject',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50)),
                ('slug', models.SlugField(max_length=50, unique=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.RenameField(
            model_name='module',
            old_name='subject',
            new_name='subject_old',
        ),
        migrations.AddField(
            model_name='module',
            name='subject_fk',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='modules',
                to='content.subject',
            ),
        ),
        migrations.RunPython(remap_module_subjects, noop),
        migrations.RemoveField(model_name='module', name='subject_old'),
        migrations.RenameField(
            model_name='module',
            old_name='subject_fk',
            new_name='subject',
        ),
        migrations.AlterField(
            model_name='module',
            name='subject',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='modules',
                to='content.subject',
            ),
        ),
    ]
