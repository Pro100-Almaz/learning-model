from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.careers.models import (
    AdmissionRoute,
    ApplicantBackground,
    FundingType,
    InstructionLanguage,
    ScoreType,
)
from web_harvester import loader
from web_harvester.claim_validation import (
    ClaimValidationResult,
    RejectedClaim,
    RejectionReason,
)
from web_harvester.models import CandidateClaim, Profession
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
    UniversityOfferingClaim,
)
from web_harvester.source_policy import FieldType, SourceStrategy


def evidence(url: str = "https://kaznmu.edu.kz/a") -> ClaimEvidence:
    return ClaimEvidence(source_url=url, excerpt="B086, minimum score 50")


def threshold_claim(url: str = "https://kaznmu.edu.kz/a", score: int = 50):
    return ThresholdClaim(
        score=score,
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


def subject_claim(url: str = "https://kaznmu.edu.kz/a"):
    return ProfileSubjectRequirementClaim(
        subjects=["Biology"],
        program_group_code="B086",
        evidence=evidence(url),
    )


def offering_claim(url: str = "https://kaznmu.edu.kz/a"):
    return UniversityOfferingClaim(
        university_name="KazNMU",
        program_group_code="B086",
        evidence=evidence(url),
    )


def rejected_claim(
    reason: RejectionReason = RejectionReason.WRONG_YEAR,
) -> RejectedClaim:
    claim = threshold_claim().model_copy(update={"year": 2019})
    return RejectedClaim(
        claim_type="threshold",
        reason=reason,
        detail="Threshold year does not match target year.",
        source_url="https://kaznmu.edu.kz/a",
        claim=claim.model_dump(mode="json"),
    )


class LoaderTests(TestCase):
    def make_success(
        self,
        strategy: SourceStrategy,
        result: AdmissionExtraction,
        rejected: tuple[RejectedClaim, ...] = (),
    ):
        attempts = []
        if strategy is SourceStrategy.FALLBACK:
            attempts.append(
                AttemptOutcome(
                    SourceStrategy.PRIMARY,
                    AttemptStatus.NO_PAGES,
                    None,
                )
            )
        validation = ClaimValidationResult(accepted=result, rejected=rejected)
        attempts.append(
            AttemptOutcome(strategy, AttemptStatus.SUCCESS, result, validation)
        )
        return HarvestOutcome(FieldType.MEDICINE, tuple(attempts), 2026)

    def test_primary_result_is_saved_with_high_confidence_metadata(self):
        outcome = self.make_success(
            SourceStrategy.PRIMARY,
            AdmissionExtraction(
                threshold_claims=[threshold_claim()],
                subject_requirements=[subject_claim()],
                university_offerings=[offering_claim()],
            ),
        )

        profession = loader.save("Doctor", "6B101", outcome)

        self.assertIsNotNone(profession)
        self.assertEqual(profession.field_type, FieldType.MEDICINE.value)
        self.assertEqual(profession.source_strategy, SourceStrategy.PRIMARY.value)
        self.assertEqual(profession.source_tier, 1)
        self.assertEqual(profession.confidence, "High")
        self.assertIsNone(profession.ubt_score)
        self.assertEqual(profession.subjects, ["Biology"])
        self.assertEqual(profession.universities, ["KazNMU"])
        self.assertIn("threshold_claims", profession.extracted_claims)
        self.assertIsNotNone(profession.fetched_at)

    def test_fallback_result_is_saved_with_low_confidence_metadata(self):
        outcome = self.make_success(
            SourceStrategy.FALLBACK,
            AdmissionExtraction(
                subject_requirements=[subject_claim("https://univision.kz/a")],
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
            AdmissionExtraction(),
        )
        untrusted = self.make_success(
            SourceStrategy.PRIMARY,
            AdmissionExtraction(
                threshold_claims=[threshold_claim("https://kbtu.edu.kz/a")]
            ),
        )

        self.assertIsNone(loader.save("Failed", "1", failed))
        self.assertIsNone(loader.save("Empty", "2", empty))
        self.assertIsNone(loader.save("Untrusted", "3", untrusted))
        self.assertEqual(Profession.objects.count(), 0)

    def test_save_is_idempotent_and_updates_existing_profession(self):
        first = self.make_success(
            SourceStrategy.PRIMARY,
            AdmissionExtraction(
                threshold_claims=[threshold_claim("https://kaznmu.edu.kz/a", 70)]
            ),
        )
        second = self.make_success(
            SourceStrategy.PRIMARY,
            AdmissionExtraction(
                threshold_claims=[threshold_claim("https://kaznmu.edu.kz/b", 90)]
            ),
        )

        loader.save("Doctor", "6B101", first)
        loader.save("Doctor", "6B101", second)

        self.assertEqual(Profession.objects.count(), 1)
        profession = Profession.objects.get()
        self.assertIsNone(profession.ubt_score)
        self.assertEqual(profession.sources, ["https://kaznmu.edu.kz/b"])
        self.assertEqual(profession.extracted_claims["threshold_claims"][0]["score"], 90)

    def test_accepted_claims_are_staged_as_candidate_claims(self):
        outcome = self.make_success(
            SourceStrategy.PRIMARY,
            AdmissionExtraction(
                threshold_claims=[threshold_claim()],
                subject_requirements=[subject_claim()],
                university_offerings=[offering_claim()],
            ),
        )

        loader.save("Doctor", "B086", outcome)

        claims = CandidateClaim.objects.all()
        self.assertEqual(claims.count(), 3)
        self.assertEqual(
            set(claims.values_list("claim_type", flat=True)),
            {"threshold", "subject_requirement", "university_offering"},
        )
        for claim in claims:
            self.assertEqual(claim.status, "accepted")
            self.assertEqual(claim.program_group_code, "B086")
            self.assertEqual(claim.target_year, 2026)
            self.assertEqual(claim.profession_name, "Doctor")
            self.assertEqual(claim.field_type, FieldType.MEDICINE.value)
            self.assertEqual(claim.source_strategy, SourceStrategy.PRIMARY.value)
            self.assertEqual(claim.source_tier, 1)
            self.assertEqual(claim.confidence, "High")
            self.assertEqual(claim.source_url, "https://kaznmu.edu.kz/a")
            self.assertEqual(claim.evidence_excerpt, "B086, minimum score 50")
            self.assertEqual(claim.rejection_reason, "")
            self.assertTrue(claim.payload)

    def test_rejected_claims_are_staged_with_diagnostics(self):
        outcome = self.make_success(
            SourceStrategy.PRIMARY,
            AdmissionExtraction(threshold_claims=[threshold_claim()]),
            rejected=(rejected_claim(),),
        )

        loader.save("Doctor", "B086", outcome)

        rejected = CandidateClaim.objects.get(status="rejected")
        self.assertEqual(rejected.claim_type, "threshold")
        self.assertEqual(rejected.rejection_reason, RejectionReason.WRONG_YEAR.value)
        self.assertEqual(
            rejected.rejection_detail,
            "Threshold year does not match target year.",
        )
        self.assertEqual(rejected.payload["year"], 2019)
        self.assertEqual(rejected.target_year, 2026)
        self.assertEqual(CandidateClaim.objects.filter(status="accepted").count(), 1)

    def test_rerunning_the_same_harvest_does_not_duplicate_candidate_claims(self):
        outcome = self.make_success(
            SourceStrategy.PRIMARY,
            AdmissionExtraction(threshold_claims=[threshold_claim()]),
            rejected=(rejected_claim(),),
        )

        loader.save("Doctor", "B086", outcome)
        loader.save("Doctor", "B086", outcome)

        self.assertEqual(CandidateClaim.objects.count(), 2)

    def test_legacy_snapshot_stores_only_accepted_claims(self):
        outcome = self.make_success(
            SourceStrategy.PRIMARY,
            AdmissionExtraction(threshold_claims=[threshold_claim()]),
            rejected=(rejected_claim(),),
        )

        profession = loader.save("Doctor", "B086", outcome)

        stored = profession.extracted_claims["threshold_claims"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["year"], 2026)

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
