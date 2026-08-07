from argparse import ArgumentTypeError
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from apps.careers.models import (
    AdmissionRoute,
    ApplicantBackground,
    FundingType,
    InstructionLanguage,
    ScoreType,
)
from web_harvester.management.commands import harvest_engine
from web_harvester.orchestration import (
    AttemptOutcome,
    AttemptStatus,
    HarvestOutcome,
)
from web_harvester.schemas import (
    AdmissionExtraction,
    ClaimEvidence,
    ProfileSubjectRequirementClaim,
    ThresholdClaim,
)
from web_harvester.search_planning import SearchTarget
from web_harvester.source_policy import FieldType, SourceStrategy


def targets(count):
    return [
        SearchTarget(
            profession_name=f"Profession {index}",
            program_group_code=f"B{index:03d}",
            program_group_name=f"Program group {index}",
            year=2026,
        )
        for index in range(1, count + 1)
    ]


def evidence(url="https://testcenter.kz/a"):
    return ClaimEvidence(source_url=url, excerpt="minimum score 50")


def threshold_claim(url="https://testcenter.kz/a"):
    return ThresholdClaim(
        score=50,
        score_type=ScoreType.LEGAL_MINIMUM,
        year=2026,
        program_group_code="B001",
        university_name=None,
        admission_route=AdmissionRoute.STANDARD,
        admission_route_details=None,
        funding_type=FundingType.GRANT_AND_PAID,
        applicant_background=ApplicantBackground.GENERAL_SECONDARY,
        applicant_background_details=None,
        quota_category="not applicable",
        instruction_language=InstructionLanguage.LANGUAGE_INDEPENDENT,
        evidence=evidence(url),
    )


def subject_claim(url="https://univision.kz/a"):
    return ProfileSubjectRequirementClaim(
        subjects=["Biology"],
        program_group_code="B001",
        evidence=evidence(url),
    )


class HarvestCommandTests(SimpleTestCase):
    def test_positive_integer_validation(self):
        self.assertEqual(harvest_engine.positive_int("3"), 3)
        with self.assertRaises(ArgumentTypeError):
            harvest_engine.positive_int("0")
        self.assertEqual(harvest_engine.admission_year("2026"), 2026)
        with self.assertRaises(ArgumentTypeError):
            harvest_engine.admission_year("1999")

    def test_command_reports_outcomes_forwards_limits_and_counts_results(self):
        classification_failure = HarvestOutcome(None, ())
        fallback_data = AdmissionExtraction(
            subject_requirements=[subject_claim()],
        )
        fallback_success = HarvestOutcome(
            FieldType.MEDICINE,
            (
                AttemptOutcome(
                    SourceStrategy.PRIMARY,
                    AttemptStatus.NO_PAGES,
                    None,
                ),
                AttemptOutcome(
                    SourceStrategy.FALLBACK,
                    AttemptStatus.SUCCESS,
                    fallback_data,
                ),
            ),
        )
        total_failure = HarvestOutcome(
            FieldType.TECHNICAL,
            (
                AttemptOutcome(
                    SourceStrategy.PRIMARY,
                    AttemptStatus.INVALID_SOURCES,
                    None,
                ),
                AttemptOutcome(
                    SourceStrategy.FALLBACK,
                    AttemptStatus.NO_PAGES,
                    None,
                ),
            ),
        )
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(harvest_engine, "build_search_targets", return_value=targets(3)),
            patch.object(
                harvest_engine.orchestration,
                "harvest",
                side_effect=[
                    classification_failure,
                    fallback_success,
                    total_failure,
                ],
            ) as harvest,
            patch.object(
                harvest_engine.loader,
                "save",
                return_value=SimpleNamespace(source_tier=2, confidence="Low"),
            ) as save,
        ):
            call_command(
                "harvest_engine",
                limit=3,
                primary_max_results=8,
                fallback_max_results=4,
                year=2026,
                stdout=stdout,
                stderr=stderr,
                no_color=True,
            )

        output = stdout.getvalue()
        self.assertIn("classification failed", output)
        self.assertIn("primary: no_pages", output)
        self.assertIn("fallback: success", output)
        self.assertIn("primary: invalid_sources", output)
        self.assertIn("Done. Saved 1, skipped 2, failed 0.", output)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(save.call_count, 1)
        self.assertIs(save.call_args.args[2], fallback_success)
        for call in harvest.call_args_list:
            self.assertIsInstance(call.kwargs["target"], SearchTarget)
            self.assertEqual(call.kwargs["primary_max_results"], 8)
            self.assertEqual(call.kwargs["fallback_max_results"], 4)

    def test_command_continues_after_harvest_and_persistence_errors(self):
        primary_data = AdmissionExtraction(
            threshold_claims=[threshold_claim()],
        )
        primary_success = HarvestOutcome(
            FieldType.EDUCATION,
            (
                AttemptOutcome(
                    SourceStrategy.PRIMARY,
                    AttemptStatus.SUCCESS,
                    primary_data,
                ),
            ),
        )
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(harvest_engine, "build_search_targets", return_value=targets(2)),
            patch.object(
                harvest_engine.orchestration,
                "harvest",
                side_effect=[RuntimeError("secret"), primary_success],
            ),
            patch.object(
                harvest_engine.loader,
                "save",
                side_effect=RuntimeError("database detail"),
            ),
        ):
            call_command(
                "harvest_engine",
                limit=2,
                year=2026,
                stdout=stdout,
                stderr=stderr,
                no_color=True,
            )

        self.assertIn("Done. Saved 0, skipped 0, failed 2.", stdout.getvalue())
        error_output = stderr.getvalue()
        self.assertIn("harvest failed (RuntimeError)", error_output)
        self.assertIn("persistence failed (RuntimeError)", error_output)
        self.assertNotIn("secret", error_output)
        self.assertNotIn("database detail", error_output)

    def test_persistence_rejection_is_counted_as_skipped(self):
        data = AdmissionExtraction(
            threshold_claims=[threshold_claim()],
        )
        outcome = HarvestOutcome(
            FieldType.EDUCATION,
            (
                AttemptOutcome(
                    SourceStrategy.PRIMARY,
                    AttemptStatus.SUCCESS,
                    data,
                ),
            ),
        )
        stdout = StringIO()

        with (
            patch.object(harvest_engine, "build_search_targets", return_value=targets(1)),
            patch.object(
                harvest_engine.orchestration,
                "harvest",
                return_value=outcome,
            ),
            patch.object(harvest_engine.loader, "save", return_value=None),
        ):
            call_command(
                "harvest_engine",
                limit=1,
                year=2026,
                stdout=stdout,
                no_color=True,
            )

        self.assertIn("persistence rejected result", stdout.getvalue())
        self.assertIn("Done. Saved 0, skipped 1, failed 0.", stdout.getvalue())
