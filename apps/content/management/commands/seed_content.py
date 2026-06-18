"""Seed the database with baseline content & careers fixtures.

Usage::

    python manage.py seed

Idempotent: re-running just re-applies the same primary-keyed rows
through Django's ``loaddata`` (which performs upserts by pk).

The fixtures live under each app's ``fixtures/`` directory so Django can
locate them automatically via ``FIXTURE_DIRS`` discovery.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


# Order matters: content & tags must exist before lessons/questions
# reference them; careers stands alone.
FIXTURES: tuple[str, ...] = (
    "content.json",
    "lessons.json",
    "careers_sample.json",
)


class Command(BaseCommand):
    help = "Load baseline seed fixtures (modules, lessons, questions, careers)."

    def handle(self, *args, **options) -> None:
        for fixture in FIXTURES:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Loading {fixture}..."))
            call_command("loaddata", fixture, verbosity=options.get("verbosity", 1))
        self.stdout.write(self.style.SUCCESS("Seed complete."))
