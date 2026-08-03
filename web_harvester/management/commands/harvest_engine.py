from argparse import ArgumentTypeError

from django.core.management.base import BaseCommand

from web_harvester import loader, orchestration
from web_harvester.agents_web import DEFAULT_MAX_RESULTS

PROFESSIONS = [
    ("Математика", "5B010900"),
    ("История", "5B011400"),
    ("Основы права и экономики", "5B011500"),
    ("Международное право", "5B030200"),
    ("Архитектура", "5B042000"),
]

# PROFESSIONS = [
#     ("Общая медицина", "B086"),
#     ("Информационные технологии", "B057"),
#     ("Агрономия", "B077"),
#     ("Право", "B049"),
#     ("Дизайн", "B031"),
# ]

def positive_int(value: str) -> int:
    """Parse a strictly positive command-line integer."""
    parsed = int(value)
    if parsed < 1:
        raise ArgumentTypeError("value must be at least 1")
    return parsed


class Command(BaseCommand):
    help = "Harvest UNT score, subjects, and universities for each profession."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=positive_int,
            default=None,
            help="Only process the first N professions.",
        )
        parser.add_argument(
            "--primary-max-results",
            type=positive_int,
            default=DEFAULT_MAX_RESULTS,
            help="Maximum Tavily results for each primary-source attempt.",
        )
        parser.add_argument(
            "--fallback-max-results",
            type=positive_int,
            default=DEFAULT_MAX_RESULTS,
            help="Maximum Tavily results for each fallback-source attempt.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        primary_max_results = options["primary_max_results"]
        fallback_max_results = options["fallback_max_results"]
        professions = PROFESSIONS[:limit]
        saved = skipped = failed = 0

        for name, national_code in professions:
            self.stdout.write(f"Harvesting: {name} ({national_code})")

            try:
                outcome = orchestration.harvest(
                    name=name,
                    national_code=national_code,
                    primary_max_results=primary_max_results,
                    fallback_max_results=fallback_max_results,
                )
            except Exception as error:
                self.stderr.write(
                    self.style.ERROR(f"  harvest failed ({type(error).__name__})")
                )
                failed += 1
                continue

            if outcome.field_type is None:
                self.stdout.write(self.style.WARNING("  classification failed - skipped"))
                skipped += 1
                continue

            self.stdout.write(f"  field: {outcome.field_type.value}")
            for attempt in outcome.attempts:
                message = f"  {attempt.strategy.value}: {attempt.status.value}"
                style = self.style.SUCCESS if attempt.succeeded else self.style.WARNING
                self.stdout.write(style(message))

            if not outcome.succeeded:
                self.stdout.write(self.style.WARNING("  no usable result - skipped"))
                skipped += 1
                continue

            try:
                profession = loader.save(name, national_code, outcome)
            except Exception as error:
                self.stderr.write(
                    self.style.ERROR(f"  persistence failed ({type(error).__name__})")
                )
                failed += 1
                continue

            if profession is None:
                self.stdout.write(
                    self.style.WARNING("  persistence rejected result - skipped")
                )
                skipped += 1
                continue

            self.stdout.write(
                self.style.SUCCESS(
                    f"  saved (tier {profession.source_tier}, {profession.confidence})"
                )
            )
            saved += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Saved {saved}, skipped {skipped}, failed {failed}."
            )
        )
