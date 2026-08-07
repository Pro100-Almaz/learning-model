"""Promote accepted candidate threshold claims into canonical admissions data."""

from django.core.management.base import BaseCommand

from web_harvester import promotion


class Command(BaseCommand):
    help = (
        "Promote accepted candidate threshold claims into canonical "
        "apps.careers admission data. Dry run unless --commit is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            default=None,
            help="Only promote candidate claims harvested for this target year.",
        )
        parser.add_argument(
            "--program-group-code",
            dest="program_group_code",
            default=None,
            help="Only promote candidate claims of this program group code.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Persist canonical rows. Without it the command is a dry run.",
        )

    def handle(self, *args, **options):
        dry_run = not options["commit"]
        result = promotion.promote_candidate_claims(
            year=options["year"],
            program_group_code=options["program_group_code"],
            dry_run=dry_run,
        )

        for message in result.messages:
            self.stdout.write(self.style.WARNING(f"  {message}"))

        mode = "dry run: would promote" if dry_run else "commit: promoted"
        summary = (
            f"{mode} {result.promoted}, "
            f"skipped {result.skipped}, "
            f"failed {result.failed}"
        )
        style = self.style.SUCCESS if result.failed == 0 else self.style.ERROR
        self.stdout.write(style(summary))
