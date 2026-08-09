from unittest.mock import patch

from django.test import SimpleTestCase

from apps.careers.models import (
    AdmissionRoute,
    ApplicantBackground,
    FundingType,
    InstructionLanguage,
    ScoreType,
)
from web_harvester import orchestration
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
from web_harvester.search_planning import SearchFact, SearchPage, SearchTarget
from web_harvester.source_policy import FieldType, SourceStrategy

TARGET = SearchTarget(
    profession_name="Doctor",
    program_group_code="B086",
    program_group_name="General medicine",
    year=2026,
)


def page(url="https://kaznmu.edu.kz/a"):
    return SearchPage(
        fact=SearchFact.LEGAL_MINIMUM,
        query='"B086" 2026 threshold',
        url=url,
        content="B086 General medicine minimum score 50 Biology KazNMU",
    )


def evidence(url="https://kaznmu.edu.kz/a"):
    return ClaimEvidence(source_url=url, excerpt="minimum score 50")


def threshold_claim(url="https://kaznmu.edu.kz/a"):
    return ThresholdClaim(
        score=50,
        score_type=ScoreType.LEGAL_MINIMUM,
        year=2026,
        program_group_code="B086",
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
        program_group_code="B086",
        evidence=evidence(url),
    )


class AttemptTests(SimpleTestCase):
    def run_attempt(self, pages, extracted, strategy=SourceStrategy.PRIMARY):
        with (
            patch.object(orchestration.agents_web, "search", return_value=pages),
            patch.object(
                orchestration.agents_web,
                "extract",
                return_value=extracted,
            ) as extract,
        ):
            outcome = orchestration._run_attempt(
                TARGET,
                FieldType.MEDICINE,
                strategy,
                6,
            )
        return outcome, extract

    def test_no_pages_skips_extraction(self):
        outcome, extract = self.run_attempt([], None)

        self.assertIs(outcome.status, AttemptStatus.NO_PAGES)
        self.assertIsNone(outcome.result)
        extract.assert_not_called()

    def test_extraction_failure_and_empty_facts_have_distinct_statuses(self):
        pages = [page()]

        failed, _extract = self.run_attempt(pages, None)
        empty, _extract = self.run_attempt(pages, AdmissionExtraction())

        self.assertIs(failed.status, AttemptStatus.EXTRACTION_FAILED)
        self.assertIs(empty.status, AttemptStatus.NO_USEFUL_FACTS)

    def test_invalid_cross_field_and_unfetched_sources_are_rejected(self):
        pages = [page()]
        cross_field = AdmissionExtraction(
            threshold_claims=[threshold_claim("https://kbtu.edu.kz/a")],
        )
        unfetched = AdmissionExtraction(
            threshold_claims=[threshold_claim("https://kaznmu.edu.kz/not-fetched")],
        )

        cross_outcome, _extract = self.run_attempt(pages, cross_field)
        unfetched_outcome, _extract = self.run_attempt(pages, unfetched)

        self.assertIs(cross_outcome.status, AttemptStatus.INVALID_SOURCES)
        self.assertIs(unfetched_outcome.status, AttemptStatus.INVALID_SOURCES)

    def test_primary_and_fallback_success_preserve_extracted_result(self):
        primary = AdmissionExtraction(
            threshold_claims=[threshold_claim()],
        )
        fallback = AdmissionExtraction(
            subject_requirements=[subject_claim()],
        )

        primary_outcome, _extract = self.run_attempt(
            [page()],
            primary,
        )
        fallback_outcome, _extract = self.run_attempt(
            [page("https://univision.kz/a")],
            fallback,
            SourceStrategy.FALLBACK,
        )

        self.assertTrue(primary_outcome.succeeded)
        self.assertEqual(primary_outcome.result, primary)
        self.assertTrue(fallback_outcome.succeeded)
        self.assertEqual(fallback_outcome.result, fallback)


class HarvestTests(SimpleTestCase):
    def test_classification_failure_performs_no_attempt(self):
        with (
            patch.object(orchestration.agents_web, "classify", return_value=None),
            patch.object(orchestration, "_run_attempt") as run_attempt,
        ):
            outcome = orchestration.harvest(TARGET)

        self.assertEqual(outcome, HarvestOutcome(None, (), TARGET.year))
        run_attempt.assert_not_called()

    def test_primary_success_stops_before_fallback(self):
        data = AdmissionExtraction(
            threshold_claims=[threshold_claim()],
        )
        primary = AttemptOutcome(
            SourceStrategy.PRIMARY,
            AttemptStatus.SUCCESS,
            data,
        )
        with (
            patch.object(
                orchestration.agents_web,
                "classify",
                return_value=FieldType.MEDICINE,
            ) as classify,
            patch.object(orchestration, "_run_attempt", return_value=primary) as run,
        ):
            outcome = orchestration.harvest(TARGET, 8, 3)

        self.assertEqual(outcome.attempts, (primary,))
        self.assertIs(outcome.result, data)
        classify.assert_called_once_with(TARGET)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["max_results"], 8)

    def test_every_primary_failure_triggers_fallback(self):
        failure_statuses = [
            AttemptStatus.NO_PAGES,
            AttemptStatus.EXTRACTION_FAILED,
            AttemptStatus.NO_USEFUL_FACTS,
            AttemptStatus.INVALID_SOURCES,
        ]
        fallback = AttemptOutcome(
            SourceStrategy.FALLBACK,
            AttemptStatus.NO_PAGES,
            None,
        )

        for status in failure_statuses:
            primary = AttemptOutcome(SourceStrategy.PRIMARY, status, None)
            with (
                self.subTest(status=status),
                patch.object(
                    orchestration.agents_web,
                    "classify",
                    return_value=FieldType.MEDICINE,
                ),
                patch.object(
                    orchestration,
                    "_run_attempt",
                    side_effect=[primary, fallback],
                ) as run,
            ):
                outcome = orchestration.harvest(TARGET, 8, 3)

            self.assertEqual(outcome.attempts, (primary, fallback))
            self.assertEqual(run.call_count, 2)
            self.assertIs(
                run.call_args_list[0].kwargs["strategy"], SourceStrategy.PRIMARY
            )
            self.assertIs(
                run.call_args_list[1].kwargs["strategy"],
                SourceStrategy.FALLBACK,
            )
            self.assertEqual(run.call_args_list[1].kwargs["max_results"], 3)

    def test_fallback_success_and_total_failure_are_derived_from_attempts(self):
        primary = AttemptOutcome(
            SourceStrategy.PRIMARY,
            AttemptStatus.NO_PAGES,
            None,
        )
        data = AdmissionExtraction(
            subject_requirements=[subject_claim()],
        )
        fallback_success = AttemptOutcome(
            SourceStrategy.FALLBACK,
            AttemptStatus.SUCCESS,
            data,
        )
        fallback_failure = AttemptOutcome(
            SourceStrategy.FALLBACK,
            AttemptStatus.NO_PAGES,
            None,
        )

        with (
            patch.object(
                orchestration.agents_web,
                "classify",
                return_value=FieldType.MEDICINE,
            ),
            patch.object(
                orchestration,
                "_run_attempt",
                side_effect=[primary, fallback_success],
            ),
        ):
            success = orchestration.harvest(TARGET)

        with (
            patch.object(
                orchestration.agents_web,
                "classify",
                return_value=FieldType.MEDICINE,
            ),
            patch.object(
                orchestration,
                "_run_attempt",
                side_effect=[primary, fallback_failure],
            ),
        ):
            failure = orchestration.harvest(TARGET)

        self.assertTrue(success.succeeded)
        self.assertIs(success.result, data)
        self.assertIs(success.strategy, SourceStrategy.FALLBACK)
        self.assertFalse(failure.succeeded)
        self.assertIsNone(failure.result)

    def test_programming_errors_propagate(self):
        with (
            patch.object(
                orchestration.agents_web,
                "classify",
                return_value=FieldType.MEDICINE,
            ),
            patch.object(
                orchestration,
                "_run_attempt",
                side_effect=ValueError("configuration error"),
            ),
            self.assertRaises(ValueError),
        ):
            orchestration.harvest(TARGET)
