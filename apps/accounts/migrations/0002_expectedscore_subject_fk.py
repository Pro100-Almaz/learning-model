"""Turn ExpectedScore.subject into a FK to content.Subject.

The old ``ExpectedScore.subject`` was a free-form CharField holding subject
names (e.g. "История Казахстана"). Existing rows are remapped onto the
matching Subject (by name, then slug); any row whose value has no matching
Subject is deleted so the resulting non-null FK never dangles.
"""

import django.db.models.deletion
from django.db import migrations, models


def remap_expected_score_subjects(apps, schema_editor):
    ExpectedScore = apps.get_model("accounts", "ExpectedScore")
    Subject = apps.get_model("content", "Subject")
    subjects = list(Subject.objects.all())
    by_name = {s.name: s for s in subjects}
    by_slug = {s.slug: s for s in subjects}
    for score in ExpectedScore.objects.all():
        subject = by_name.get(score.subject_old) or by_slug.get(score.subject_old)
        if subject is None:
            score.delete()
        else:
            score.subject_fk = subject
            score.save(update_fields=["subject_fk"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('content', '0003_subject_fk'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='expectedscore',
            unique_together=set(),
        ),
        migrations.RenameField(
            model_name='expectedscore',
            old_name='subject',
            new_name='subject_old',
        ),
        migrations.AddField(
            model_name='expectedscore',
            name='subject_fk',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='expected_scores',
                to='content.subject',
            ),
        ),
        migrations.RunPython(remap_expected_score_subjects, noop),
        migrations.RemoveField(model_name='expectedscore', name='subject_old'),
        migrations.RenameField(
            model_name='expectedscore',
            old_name='subject_fk',
            new_name='subject',
        ),
        migrations.AlterField(
            model_name='expectedscore',
            name='subject',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='expected_scores',
                to='content.subject',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='expectedscore',
            unique_together={('profile', 'subject')},
        ),
    ]
