from django.db import migrations
from django.db.models import Count, Q

def populate_usernames(apps, schema_editor):
    StudentProfile = apps.get_model("accounts", "StudentProfile")
    database = schema_editor.connection.alias
    profiles = StudentProfile.objects.using(database)


    missing_emails = profiles.filter(Q(user__email__isnull=True) | Q(user__email=""))

    if missing_emails.exists():
        missing_ids = list(missing_emails.values_list("id", flat=True))
        raise RuntimeError(f"Cannot generate usernames. Student Profile without emails: {missing_ids}")


    duplicate_emails = list(
        profiles.values("user__email").annotate(total=Count("id")).filter(total__gt=1).values_list("user__email", flat=True)[:20]
    )

    if duplicate_emails:
        raise RuntimeError(
            f"Cannot generate unique usernames. "
            f"Duplicate emails: {duplicate_emails}"
        )

    for student_profile in profiles.iterator():
        student_profile.username = student_profile.user.email
        student_profile.save(
            using=database,
            update_fields=["username"],
        )

def clear_usernames(apps, schedule_editor):
    StudentProfile = apps.get_model("accounts", "StudentProfile")
    database = schedule_editor.connection.alias

    StudentProfile.objects.using(database).update(username=None)

class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_studentprofile_friends_studentprofile_username"),
    ]

    operations = [
        migrations.RunPython(
            populate_usernames,
            reverse_code=clear_usernames
        )
    ]