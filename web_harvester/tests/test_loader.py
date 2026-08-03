from django.db import IntegrityError, transaction
from django.test import TestCase

from web_harvester import loader
from web_harvester.models import Profession
from web_harvester.orchestration import (
    AttemptOutcome,
    AttemptStatus,
    HarvestOutcome,
)
from web_harvester.schemas import WebSearch
from web_harvester.source_policy import FieldType, SourceStrategy


class LoaderTests(TestCase):
    def make_success(self, strategy: SourceStrategy, result: WebSearch):
        attempts = []
        if strategy is SourceStrategy.FALLBACK:
            attempts.append(
                AttemptOutcome(
                    SourceStrategy.PRIMARY,
                    AttemptStatus.NO_PAGES,
                    None,
                )
            )
        attempts.append(AttemptOutcome(strategy, AttemptStatus.SUCCESS, result))
        return HarvestOutcome(FieldType.MEDICINE, tuple(attempts))

    def test_primary_result_is_saved_with_high_confidence_metadata(self):
        outcome = self.make_success(
            SourceStrategy.PRIMARY,
            WebSearch(
                ubt_score=0,
                subjects=["Biology"],
                sources=["https://kaznmu.edu.kz/a"],
            ),
        )

        profession = loader.save("Doctor", "6B101", outcome)

        self.assertIsNotNone(profession)
        self.assertEqual(profession.field_type, FieldType.MEDICINE.value)
        self.assertEqual(profession.source_strategy, SourceStrategy.PRIMARY.value)
        self.assertEqual(profession.source_tier, 1)
        self.assertEqual(profession.confidence, "High")
        self.assertIsNotNone(profession.fetched_at)

    def test_fallback_result_is_saved_with_low_confidence_metadata(self):
        outcome = self.make_success(
            SourceStrategy.FALLBACK,
            WebSearch(
                subjects=["Biology"],
                sources=["https://univision.kz/a"],
            ),
        )

        profession = loader.save("Doctor", "6B101", outcome)

        self.assertIsNotNone(profession)
        self.assertEqual(profession.source_strategy, SourceStrategy.FALLBACK.value)
        self.assertEqual(profession.source_tier, 2)
        self.assertEqual(profession.confidence, "Low")

    def test_failed_empty_and_untrusted_outcomes_are_not_saved(self):
        failed = HarvestOutcome(
            FieldType.MEDICINE,
            (
                AttemptOutcome(
                    SourceStrategy.PRIMARY,
                    AttemptStatus.NO_PAGES,
                    None,
                ),
            ),
        )
        empty = self.make_success(
            SourceStrategy.PRIMARY,
            WebSearch(sources=["https://kaznmu.edu.kz/a"]),
        )
        untrusted = self.make_success(
            SourceStrategy.PRIMARY,
            WebSearch(ubt_score=80, sources=["https://kbtu.edu.kz/a"]),
        )

        self.assertIsNone(loader.save("Failed", "1", failed))
        self.assertIsNone(loader.save("Empty", "2", empty))
        self.assertIsNone(loader.save("Untrusted", "3", untrusted))
        self.assertEqual(Profession.objects.count(), 0)

    def test_save_is_idempotent_and_updates_existing_profession(self):
        first = self.make_success(
            SourceStrategy.PRIMARY,
            WebSearch(ubt_score=70, sources=["https://kaznmu.edu.kz/a"]),
        )
        second = self.make_success(
            SourceStrategy.PRIMARY,
            WebSearch(ubt_score=90, sources=["https://kaznmu.edu.kz/b"]),
        )

        loader.save("Doctor", "6B101", first)
        loader.save("Doctor", "6B101", second)

        self.assertEqual(Profession.objects.count(), 1)
        profession = Profession.objects.get()
        self.assertEqual(profession.ubt_score, 90)
        self.assertEqual(profession.sources, ["https://kaznmu.edu.kz/b"])

    def test_database_constraint_rejects_contradictory_new_metadata(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Profession.objects.create(
                name="Invalid",
                national_code="X",
                field_type=FieldType.MEDICINE.value,
                source_strategy=SourceStrategy.PRIMARY.value,
                source_tier=2,
                confidence="Low",
            )

    def test_database_constraint_allows_legacy_null_metadata(self):
        profession = Profession.objects.create(
            name="Legacy",
            national_code="OLD",
            field_type=None,
            source_strategy=None,
            source_tier=1,
            confidence="High",
        )

        self.assertIsNotNone(profession.pk)
