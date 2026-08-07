from django.test import SimpleTestCase

from apps.careers.models import (
    AdmissionRoute,
    ApplicantBackground,
    FundingType,
    InstructionLanguage,
    ScoreType,
)
from web_harvester.claim_validation import RejectionReason, validate_claims
from web_harvester.schemas import (
    AdmissionExtraction,
    ClaimEvidence,
    ProfileSubjectRequirementClaim,
    ProgramIdentityClaim,
    ThresholdClaim,
    UniversityOfferingClaim,
)
from web_harvester.search_planning import SearchFact, SearchPage, SearchTarget
from web_harvester.source_policy import FieldType, SourceStrategy

TARGET = SearchTarget(
    profession_name="Doctor",
    program_group_code="B086",
    program_group_name="General medicine",
    year=2026,
)


def page(
    url: str = "https://kaznmu.edu.kz/a",
    content: str = "B086 General medicine minimum score 50 Biology KazNMU",
) -> SearchPage:
    return SearchPage(
        fact=SearchFact.LEGAL_MINIMUM,
        query='"B086" 2026 threshold',
        url=url,
        content=content,
    )


def evidence(
    url: str = "https://kaznmu.edu.kz/a",
    excerpt: str = "minimum score 50",
) -> ClaimEvidence:
    return ClaimEvidence(source_url=url, excerpt=excerpt)


def threshold_claim(**overrides) -> ThresholdClaim:
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


def validate(extraction: AdmissionExtraction, pages: list[SearchPage] | None = None):
    return validate_claims(
        extraction=extraction,
        pages=pages or [page()],
        target=TARGET,
        field_type=FieldType.MEDICINE,
        strategy=SourceStrategy.PRIMARY,
    )


class ClaimValidationTests(SimpleTestCase):
    def test_accepts_supported_threshold_claim(self):
        result = validate(AdmissionExtraction(threshold_claims=[threshold_claim()]))

        self.assertEqual(len(result.accepted.threshold_claims), 1)
        self.assertEqual(result.rejected, ())

    def test_rejects_threshold_with_missing_required_context(self):
        result = validate(
            AdmissionExtraction(
                threshold_claims=[
                    threshold_claim(
                        admission_route=None,
                        funding_type=None,
                        applicant_background=None,
                    )
                ]
            )
        )

        self.assertEqual(result.accepted.threshold_claims, [])
        self.assertEqual(
            result.rejected[0].reason,
            RejectionReason.MISSING_REQUIRED_CONTEXT,
        )

    def test_rejects_25_for_standard_general_secondary_route(self):
        result = validate(
            AdmissionExtraction(
                threshold_claims=[
                    threshold_claim(
                        score=25,
                        evidence=evidence(excerpt="minimum score 25"),
                    )
                ]
            ),
            [page(content="B086 General medicine minimum score 25")],
        )

        self.assertEqual(result.accepted.threshold_claims, [])
        self.assertEqual(
            result.rejected[0].reason,
            RejectionReason.ROUTE_SCORE_REQUIRES_CONTEXT,
        )

    def test_accepts_25_when_shortened_tvet_context_is_explicit(self):
        result = validate(
            AdmissionExtraction(
                threshold_claims=[
                    threshold_claim(
                        score=25,
                        admission_route=AdmissionRoute.SHORTENED_RELATED,
                        applicant_background=ApplicantBackground.TVET_POSTSECONDARY,
                        evidence=evidence(excerpt="minimum score 25"),
                    )
                ]
            ),
            [page(content="B086 shortened TVET minimum score 25")],
        )

        self.assertEqual(len(result.accepted.threshold_claims), 1)
        self.assertEqual(result.rejected, ())

    def test_rejects_wrong_year_and_wrong_program_group(self):
        wrong_year = validate(
            AdmissionExtraction(threshold_claims=[threshold_claim(year=2025)])
        )
        wrong_program = validate(
            AdmissionExtraction(
                threshold_claims=[threshold_claim(program_group_code="B057")]
            )
        )

        self.assertEqual(wrong_year.rejected[0].reason, RejectionReason.WRONG_YEAR)
        self.assertEqual(
            wrong_program.rejected[0].reason,
            RejectionReason.WRONG_PROGRAM_GROUP,
        )

    def test_rejects_unfetched_untrusted_and_unsupported_evidence(self):
        unfetched = validate(
            AdmissionExtraction(
                threshold_claims=[
                    threshold_claim(evidence=evidence("https://kaznmu.edu.kz/missing"))
                ]
            )
        )
        untrusted = validate(
            AdmissionExtraction(
                threshold_claims=[
                    threshold_claim(evidence=evidence("https://kbtu.edu.kz/a"))
                ]
            ),
            [page(url="https://kbtu.edu.kz/a")],
        )
        unsupported = validate(
            AdmissionExtraction(
                threshold_claims=[
                    threshold_claim(evidence=evidence(excerpt="not in page"))
                ]
            )
        )

        self.assertEqual(unfetched.rejected[0].reason, RejectionReason.UNFETCHED_SOURCE)
        self.assertEqual(untrusted.rejected[0].reason, RejectionReason.UNTRUSTED_SOURCE)
        self.assertEqual(
            unsupported.rejected[0].reason,
            RejectionReason.UNSUPPORTED_EVIDENCE,
        )

    def test_non_score_claims_use_program_and_evidence_checks(self):
        extraction = AdmissionExtraction(
            program_identities=[
                ProgramIdentityClaim(
                    program_group_code="B086",
                    program_group_name="General medicine",
                    evidence=evidence(),
                )
            ],
            subject_requirements=[
                ProfileSubjectRequirementClaim(
                    subjects=["Biology"],
                    program_group_code="B086",
                    evidence=evidence(excerpt="Biology"),
                )
            ],
            university_offerings=[
                UniversityOfferingClaim(
                    university_name="KazNMU",
                    program_group_code="B086",
                    evidence=evidence(excerpt="KazNMU"),
                )
            ],
        )

        result = validate(extraction)

        self.assertEqual(len(result.accepted.program_identities), 1)
        self.assertEqual(len(result.accepted.subject_requirements), 1)
        self.assertEqual(len(result.accepted.university_offerings), 1)
