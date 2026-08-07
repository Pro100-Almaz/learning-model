from django.test import TestCase

from apps.careers.models import (
    AdmissionRoute,
    AdmissionSource,
    AdmissionThreshold,
    ApplicantBackground,
    EducationalProgramGroup,
    FundingType,
    InstructionLanguage,
    ScoreType,
    University,
)
from web_harvester import promotion
from web_harvester.models import CandidateClaim
from web_harvester.source_policy import FieldType, SourceStrategy

EXCERPT = "B086, minimum score 50"


def payload(**overrides) -> dict:
    data = {
        "score": 50,
        "score_type": ScoreType.LEGAL_MINIMUM.value,
        "year": 2026,
        "program_group_code": "B086",
        "university_name": None,
        "admission_route": AdmissionRoute.STANDARD.value,
        "admission_route_details": None,
        "funding_type": FundingType.GRANT_AND_PAID.value,
        "applicant_background": ApplicantBackground.GENERAL_SECONDARY.value,
        "applicant_background_details": None,
        "quota_category": "not applicable",
        "instruction_language": InstructionLanguage.LANGUAGE_INDEPENDENT.value,
        "evidence": {
            "source_url": "https://kaznmu.edu.kz/a",
            "excerpt": EXCERPT,
            "location": "table 2",
        },
    }
    data.update(overrides)
    return data


def candidate(**overrides) -> CandidateClaim:
    claim_payload = overrides.pop("payload", None) or payload()
    fields = {
        "profession_name": "Doctor",
        "program_group_code": "B086",
        "target_year": 2026,
        "field_type": FieldType.MEDICINE.value,
        "source_strategy": SourceStrategy.PRIMARY.value,
        "source_tier": 1,
        "confidence": "High",
        "claim_type": "threshold",
        "status": "accepted",
        "source_url": "https://kaznmu.edu.kz/a",
        "evidence_excerpt": EXCERPT,
        "evidence_location": "table 2",
        "payload": claim_payload,
        "payload_fingerprint": "a" * 64,
    }
    fields.update(overrides)
    return CandidateClaim.objects.create(**fields)


class PromotionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group = EducationalProgramGroup.objects.create(
            code="B086",
            name="General medicine",
        )
        cls.university = University.objects.create(
            name="KazNMU",
            city="Almaty",
            code="KAZNMU",
        )

    def test_dry_run_creates_no_canonical_rows(self):
        candidate()

        result = promotion.promote_candidate_claims(dry_run=True)

        self.assertEqual(result.promoted, 1)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.failed, 0)
        self.assertEqual(AdmissionThreshold.objects.count(), 0)
        self.assertEqual(AdmissionSource.objects.count(), 0)
        self.assertEqual(CandidateClaim.objects.get().status, "accepted")

    def test_commit_creates_source_threshold_and_marks_candidate(self):
        staged = candidate()

        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual((result.promoted, result.skipped, result.failed), (1, 0, 0))
        source = AdmissionSource.objects.get()
        self.assertEqual(source.url, "https://kaznmu.edu.kz/a")
        self.assertEqual(source.content_fingerprint, "a" * 64)

        threshold = AdmissionThreshold.objects.get()
        self.assertEqual(threshold.program_group, self.group)
        self.assertIsNone(threshold.university)
        self.assertIsNone(threshold.specialty)
        self.assertEqual(threshold.source, source)
        self.assertEqual(threshold.score, 50)
        self.assertEqual(threshold.year, 2026)
        self.assertEqual(threshold.evidence_excerpt, EXCERPT)
        self.assertEqual(threshold.evidence_location, "table 2")
        self.assertIsNotNone(threshold.verified_at)

        staged.refresh_from_db()
        self.assertEqual(staged.status, "promoted")
        self.assertIsNotNone(staged.promoted_at)
        self.assertEqual(staged.promoted_admission_threshold_id, threshold.pk)

    def test_rerun_is_idempotent(self):
        staged = candidate()

        promotion.promote_candidate_claims(dry_run=False)
        staged.status = "accepted"
        staged.save(update_fields=["status"])
        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual(result.promoted, 1)
        self.assertEqual(AdmissionThreshold.objects.count(), 1)
        self.assertEqual(AdmissionSource.objects.count(), 1)

    def test_already_promoted_candidates_are_not_reprocessed(self):
        candidate()
        promotion.promote_candidate_claims(dry_run=False)

        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual((result.promoted, result.skipped, result.failed), (0, 0, 0))
        self.assertEqual(AdmissionThreshold.objects.count(), 1)

    def test_rejected_candidates_never_promote(self):
        candidate(
            status="rejected",
            rejection_reason="wrong_year",
            rejection_detail="Threshold year does not match target year.",
        )

        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual((result.promoted, result.skipped, result.failed), (0, 0, 0))
        self.assertEqual(AdmissionThreshold.objects.count(), 0)

    def test_missing_program_group_is_skipped(self):
        candidate(
            program_group_code="B999",
            payload=payload(program_group_code="B999"),
        )

        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual((result.promoted, result.skipped, result.failed), (0, 1, 0))
        self.assertIn("missing program group B999", result.messages[0])
        self.assertEqual(AdmissionThreshold.objects.count(), 0)

    def test_unknown_university_is_skipped_only_for_university_claims(self):
        candidate(
            payload=payload(
                score_type=ScoreType.UNIVERSITY_MINIMUM.value,
                university_name="Unknown University",
            ),
        )

        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual((result.promoted, result.skipped, result.failed), (0, 1, 0))
        self.assertIn("missing university", result.messages[0])

    def test_known_university_claim_is_promoted(self):
        candidate(
            payload=payload(
                score_type=ScoreType.UNIVERSITY_MINIMUM.value,
                university_name="kaznmu",
            ),
        )

        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual(result.promoted, 1)
        self.assertEqual(AdmissionThreshold.objects.get().university, self.university)

    def test_conflicting_scores_from_different_sources_both_persist(self):
        candidate()
        candidate(
            source_url="https://kaznmu.edu.kz/b",
            payload_fingerprint="b" * 64,
            payload=payload(score=55),
        )

        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual(result.promoted, 2)
        self.assertEqual(AdmissionSource.objects.count(), 2)
        self.assertEqual(
            sorted(AdmissionThreshold.objects.values_list("score", flat=True)),
            [50, 55],
        )

    def test_invalid_payload_is_skipped_and_reported(self):
        candidate(payload=payload(quota_category=None))

        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual((result.promoted, result.skipped, result.failed), (0, 1, 0))
        self.assertIn("payload is missing quota_category", result.messages[0])

    def test_out_of_range_payload_fails_validation(self):
        candidate(payload=payload(year=1990))

        result = promotion.promote_candidate_claims(dry_run=False)

        self.assertEqual((result.promoted, result.skipped, result.failed), (0, 0, 1))
        self.assertIn("ValidationError", result.messages[0])
        self.assertNotIn("1990", result.messages[0])

    def test_filters_restrict_the_promoted_set(self):
        candidate()
        candidate(
            target_year=2025,
            source_url="https://kaznmu.edu.kz/c",
            payload_fingerprint="c" * 64,
            payload=payload(year=2025),
        )

        result = promotion.promote_candidate_claims(
            year=2026,
            program_group_code="B086",
            dry_run=False,
        )

        self.assertEqual(result.promoted, 1)
        self.assertEqual(AdmissionThreshold.objects.get().year, 2026)
