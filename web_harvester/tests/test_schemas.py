from django.test import SimpleTestCase
from pydantic import ValidationError

from apps.careers.models import (
    AdmissionRoute,
    ApplicantBackground,
    FundingType,
    InstructionLanguage,
    ScoreType,
)
from web_harvester.schemas import (
    AdmissionExtraction,
    ClaimEvidence,
    ProfileSubjectRequirementClaim,
    ThresholdClaim,
    UniversityOfferingClaim,
)


def evidence(url: str = "https://kaznmu.edu.kz/a") -> ClaimEvidence:
    return ClaimEvidence(
        source_url=url,
        excerpt="B086, 2026, minimum score 50",
        location="table 1",
    )


def threshold(**overrides) -> ThresholdClaim:
    values = {
        "score": 50,
        "score_type": ScoreType.LEGAL_MINIMUM,
        "year": 2026,
        "program_group_code": "B086",
        "university_name": None,
        "admission_route": AdmissionRoute.STANDARD,
        "admission_route_details": None,
        "funding_type": FundingType.GRANT_AND_PAID,
        "applicant_background": ApplicantBackground.GENERAL_SECONDARY,
        "applicant_background_details": None,
        "quota_category": "not applicable",
        "instruction_language": InstructionLanguage.LANGUAGE_INDEPENDENT,
        "evidence": evidence(),
    }
    values.update(overrides)
    return ThresholdClaim(**values)


class ExtractionSchemaTests(SimpleTestCase):
    def test_empty_extraction_is_valid(self):
        result = AdmissionExtraction()

        self.assertEqual(result.source_urls(), [])

    def test_complete_threshold_claim_is_valid(self):
        claim = threshold()

        self.assertEqual(claim.score, 50)
        self.assertEqual(claim.evidence.source_url, "https://kaznmu.edu.kz/a")

    def test_score_zero_and_above_scale_are_rejected(self):
        with self.assertRaises(ValidationError):
            threshold(score=0)
        with self.assertRaises(ValidationError):
            threshold(score=141)

    def test_threshold_requires_score_type_and_year(self):
        values = threshold().model_dump()

        values_without_type = dict(values)
        values_without_type.pop("score_type")
        with self.assertRaises(ValidationError):
            ThresholdClaim(**values_without_type)

        values_without_year = dict(values)
        values_without_year.pop("year")
        with self.assertRaises(ValidationError):
            ThresholdClaim(**values_without_year)

    def test_context_fields_can_be_explicitly_unknown(self):
        claim = threshold(
            admission_route=None,
            funding_type=None,
            applicant_background=None,
            quota_category=None,
            instruction_language=None,
        )

        self.assertIsNone(claim.admission_route)
        self.assertIsNone(claim.funding_type)

    def test_blank_evidence_is_rejected(self):
        with self.assertRaises(ValidationError):
            ClaimEvidence(source_url="https://kaznmu.edu.kz/a", excerpt="   ")

    def test_blank_subjects_are_rejected(self):
        with self.assertRaises(ValidationError):
            ProfileSubjectRequirementClaim(
                subjects=["Mathematics", "   "],
                program_group_code="B086",
                evidence=evidence(),
            )

    def test_offering_carries_evidence(self):
        claim = UniversityOfferingClaim(
            university_name="KazNMU",
            program_group_code="B086",
            evidence=evidence(),
        )

        self.assertEqual(claim.evidence.source_url, "https://kaznmu.edu.kz/a")

    def test_contradictory_threshold_claims_coexist(self):
        result = AdmissionExtraction(
            threshold_claims=[
                threshold(score=50, evidence=evidence("https://kaznmu.edu.kz/a")),
                threshold(score=55, evidence=evidence("https://kaznmu.edu.kz/b")),
            ]
        )

        self.assertEqual([claim.score for claim in result.threshold_claims], [50, 55])
        self.assertEqual(
            result.source_urls(),
            ["https://kaznmu.edu.kz/a", "https://kaznmu.edu.kz/b"],
        )

    def test_new_extraction_result_has_no_scalar_ubt_score(self):
        self.assertFalse(hasattr(AdmissionExtraction(), "ubt_score"))
