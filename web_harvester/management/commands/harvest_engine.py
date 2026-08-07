from argparse import ArgumentTypeError

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.careers.models import (
    ProfessionIdentifierScheme,
    ProfessionProgramGroup,
    ProgramIdentifierScheme,
)
from web_harvester import loader, orchestration
from web_harvester.agents_web import DEFAULT_MAX_RESULTS
from web_harvester.search_planning import SearchTarget


def positive_int(value: str) -> int:
    """Parse a strictly positive command-line integer."""
    parsed = int(value)
    if parsed < 1:
        raise ArgumentTypeError("value must be at least 1")
    return parsed


def admission_year(value: str) -> int:
    parsed = int(value)
    if not 2000 <= parsed <= 2100:
        raise ArgumentTypeError("year must be between 2000 and 2100")
    return parsed


def build_search_targets(year: int) -> list[SearchTarget]:
    """Build targets only from canonical, evidenced identity relationships."""
    links = (
        ProfessionProgramGroup.objects.select_related("profession", "program_group")
        .prefetch_related("program_group__aliases", "profession__identifiers")
        .filter(profession__is_active=True, program_group__is_active=True)
        .order_by("profession__slug", "program_group__code")
    )

    targets: list[SearchTarget] = []
    for link in links:
        group_aliases = list(link.program_group.aliases.all())
        profession_identifiers = list(link.profession.identifiers.all())
        legacy_codes = tuple(
            alias.value
            for alias in group_aliases
            if alias.scheme == ProgramIdentifierScheme.LEGACY_SPECIALTY_CODE
        )
        alternative_names = tuple(
            [
                alias.value
                for alias in group_aliases
                if alias.scheme == ProgramIdentifierScheme.ALTERNATIVE_NAME
            ]
            + [
                identifier.value
                for identifier in profession_identifiers
                if identifier.scheme == ProfessionIdentifierScheme.ALTERNATIVE_NAME
            ]
        )
        targets.append(
            SearchTarget(
                profession_name=link.profession.name,
                program_group_code=link.program_group.code,
                program_group_name=link.program_group.name,
                year=year,
                legacy_codes=legacy_codes,
                alternative_names=alternative_names,
            )
        )
    return targets


class Command(BaseCommand):
    help = "Harvest evidence-backed admission candidate claims for each profession."

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
            help="Maximum Tavily results for each primary fact query.",
        )
        parser.add_argument(
            "--fallback-max-results",
            type=positive_int,
            default=DEFAULT_MAX_RESULTS,
            help="Maximum Tavily results for each fallback fact query.",
        )
        parser.add_argument(
            "--year",
            type=admission_year,
            default=timezone.now().year,
            help="Admission or completed competition year to search.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        primary_max_results = options["primary_max_results"]
        fallback_max_results = options["fallback_max_results"]
        targets = build_search_targets(options["year"])
        if limit is not None:
            targets = targets[:limit]
        saved = skipped = failed = 0

        for target in targets:
            self.stdout.write(
                "Harvesting: "
                f"{target.profession_name} ({target.program_group_code}, {target.year})"
            )

            try:
                outcome = orchestration.harvest(
                    target=target,
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
                profession = loader.save(
                    target.profession_name,
                    target.program_group_code,
                    outcome,
                )
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
