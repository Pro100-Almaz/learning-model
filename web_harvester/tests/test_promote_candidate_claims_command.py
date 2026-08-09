from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from web_harvester.promotion import PromotionResult

COMMAND = "promote_candidate_claims"
TARGET = "web_harvester.management.commands.promote_candidate_claims.promotion"


class PromoteCandidateClaimsCommandTests(SimpleTestCase):
    def run_command(self, result: PromotionResult, *args):
        out = StringIO()
        with patch(f"{TARGET}.promote_candidate_claims", return_value=result) as promote:
            call_command(COMMAND, *args, stdout=out)
        return promote, out.getvalue()

    def test_default_run_is_a_dry_run(self):
        promote, output = self.run_command(PromotionResult(12, 3, 0, ()))

        promote.assert_called_once_with(
            year=None,
            program_group_code=None,
            dry_run=True,
        )
        self.assertIn("dry run: would promote 12, skipped 3, failed 0", output)

    def test_commit_disables_dry_run(self):
        promote, output = self.run_command(PromotionResult(12, 3, 0, ()), "--commit")

        promote.assert_called_once_with(
            year=None,
            program_group_code=None,
            dry_run=False,
        )
        self.assertIn("commit: promoted 12, skipped 3, failed 0", output)

    def test_filters_are_passed_through(self):
        promote, _output = self.run_command(
            PromotionResult(0, 0, 0, ()),
            "--year",
            "2026",
            "--program-group-code",
            "B086",
        )

        promote.assert_called_once_with(
            year=2026,
            program_group_code="B086",
            dry_run=True,
        )

    def test_messages_are_reported(self):
        _promote, output = self.run_command(
            PromotionResult(0, 1, 1, ("candidate 1: missing program group B999",)),
        )

        self.assertIn("candidate 1: missing program group B999", output)
        self.assertIn("skipped 1, failed 1", output)
