from unittest.mock import patch

from django.test import SimpleTestCase

from web_harvester import orchestration
from web_harvester.orchestration import (
    AttemptOutcome,
    AttemptStatus,
    HarvestOutcome,
)
from web_harvester.schemas import WebSearch
from web_harvester.source_policy import FieldType, SourceStrategy


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
                "Medicine",
                "6B101",
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
        pages = [("https://kaznmu.edu.kz/a", "page")]

        failed, _extract = self.run_attempt(pages, None)
        empty, _extract = self.run_attempt(pages, WebSearch())

        self.assertIs(failed.status, AttemptStatus.EXTRACTION_FAILED)
        self.assertIs(empty.status, AttemptStatus.NO_USEFUL_FACTS)

    def test_invalid_cross_field_and_unfetched_sources_are_rejected(self):
        pages = [("https://kaznmu.edu.kz/a", "page")]
        cross_field = WebSearch(
            ubt_score=80,
            sources=["https://kbtu.edu.kz/a"],
        )
        unfetched = WebSearch(
            ubt_score=80,
            sources=["https://kaznmu.edu.kz/not-fetched"],
        )

        cross_outcome, _extract = self.run_attempt(pages, cross_field)
        unfetched_outcome, _extract = self.run_attempt(pages, unfetched)

        self.assertIs(cross_outcome.status, AttemptStatus.INVALID_SOURCES)
        self.assertIs(unfetched_outcome.status, AttemptStatus.INVALID_SOURCES)

    def test_primary_and_fallback_success_preserve_extracted_result(self):
        primary = WebSearch(
            ubt_score=0,
            sources=["https://kaznmu.edu.kz/a"],
        )
        fallback = WebSearch(
            subjects=["Biology"],
            sources=["https://univision.kz/a"],
        )

        primary_outcome, _extract = self.run_attempt(
            [("https://kaznmu.edu.kz/a", "page")],
            primary,
        )
        fallback_outcome, _extract = self.run_attempt(
            [("https://univision.kz/a", "page")],
            fallback,
            SourceStrategy.FALLBACK,
        )

        self.assertTrue(primary_outcome.succeeded)
        self.assertIs(primary_outcome.result, primary)
        self.assertTrue(fallback_outcome.succeeded)
        self.assertIs(fallback_outcome.result, fallback)


class HarvestTests(SimpleTestCase):
    def test_classification_failure_performs_no_attempt(self):
        with (
            patch.object(orchestration.agents_web, "classify", return_value=None),
            patch.object(orchestration, "_run_attempt") as run_attempt,
        ):
            outcome = orchestration.harvest("Unknown", "X")

        self.assertEqual(outcome, HarvestOutcome(None, ()))
        run_attempt.assert_not_called()

    def test_primary_success_stops_before_fallback(self):
        data = WebSearch(
            ubt_score=80,
            sources=["https://kaznmu.edu.kz/a"],
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
            outcome = orchestration.harvest("Medicine", "6B101", 8, 3)

        self.assertEqual(outcome.attempts, (primary,))
        self.assertIs(outcome.result, data)
        classify.assert_called_once_with("Medicine", "6B101")
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
                outcome = orchestration.harvest("Medicine", "6B101", 8, 3)

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
        data = WebSearch(
            subjects=["Biology"],
            sources=["https://univision.kz/a"],
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
            success = orchestration.harvest("Medicine", "6B101")

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
            failure = orchestration.harvest("Medicine", "6B101")

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
            orchestration.harvest("Medicine", "6B101")
