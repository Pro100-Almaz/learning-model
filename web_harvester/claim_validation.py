"""Deterministic validation for harvested admission candidate claims."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from apps.careers.models import (
    AdmissionRoute,
    ApplicantBackground,
    FundingType,
    ScoreType,
)
from web_harvester import trust
from web_harvester.schemas import (
    AdmissionExtraction,
    ClaimEvidence,
    ProfileSubjectRequirementClaim,
    ProgramIdentityClaim,
    ThresholdClaim,
    UniversityOfferingClaim,
)
from web_harvester.search_planning import SearchPage, SearchTarget
from web_harvester.source_policy import FieldType, SourceStrategy


class RejectionReason(StrEnum):
    MISSING_REQUIRED_CONTEXT = "missing_required_context"
    WRONG_PROGRAM_GROUP = "wrong_program_group"
    WRONG_YEAR = "wrong_year"
    UNFETCHED_SOURCE = "unfetched_source"
    UNTRUSTED_SOURCE = "untrusted_source"
    UNSUPPORTED_EVIDENCE = "unsupported_evidence"
    ROUTE_SCORE_REQUIRES_CONTEXT = "route_score_requires_context"


@dataclass(frozen=True, slots=True)
class RejectedClaim:
    claim_type: str
    reason: RejectionReason
    detail: str
    source_url: str
    claim: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ClaimValidationResult:
    accepted: AdmissionExtraction
    rejected: tuple[RejectedClaim, ...]

    @property
    def has_source_rejections(self) -> bool:
        return any(
            rejected.reason
            in {
                RejectionReason.UNFETCHED_SOURCE,
                RejectionReason.UNTRUSTED_SOURCE,
            }
            for rejected in self.rejected
        )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _reject(
    claim_type: str,
    reason: RejectionReason,
    detail: str,
    evidence: ClaimEvidence,
    claim: object,
) -> RejectedClaim:
    return RejectedClaim(
        claim_type=claim_type,
        reason=reason,
        detail=detail,
        source_url=evidence.source_url,
        claim=claim.model_dump(mode="json"),
    )


def _validate_evidence(
    claim_type: str,
    evidence: ClaimEvidence,
    claim: object,
    page_content_by_url: dict[str, str],
    field_type: FieldType,
    strategy: SourceStrategy,
) -> RejectedClaim | None:
    if evidence.source_url not in page_content_by_url:
        return _reject(
            claim_type,
            RejectionReason.UNFETCHED_SOURCE,
            "Evidence URL was not fetched in this attempt.",
            evidence,
            claim,
        )

    if not trust.is_allowed_source(evidence.source_url, field_type, strategy):
        return _reject(
            claim_type,
            RejectionReason.UNTRUSTED_SOURCE,
            "Evidence URL is outside the allowed source policy.",
            evidence,
            claim,
        )

    page_content = _normalize(page_content_by_url[evidence.source_url])
    excerpt = _normalize(evidence.excerpt)
    if excerpt not in page_content:
        return _reject(
            claim_type,
            RejectionReason.UNSUPPORTED_EVIDENCE,
            "Evidence excerpt was not found in the fetched page content.",
            evidence,
            claim,
        )

    return None


def _target_text_is_supported(
    evidence: ClaimEvidence,
    page_content_by_url: dict[str, str],
    target: SearchTarget,
) -> bool:
    content = _normalize(page_content_by_url.get(evidence.source_url, ""))
    return (
        _normalize(target.program_group_code) in content
        or _normalize(target.program_group_name) in content
    )


def _program_code_matches(code: str | None, target: SearchTarget) -> bool:
    return code is not None and _normalize(code) == _normalize(target.program_group_code)


def _program_name_matches(name: str, target: SearchTarget) -> bool:
    normalized_name = _normalize(name)
    normalized_target = _normalize(target.program_group_name)
    return normalized_name in normalized_target or normalized_target in normalized_name


def _validate_threshold(
    claim: ThresholdClaim,
    target: SearchTarget,
) -> tuple[RejectionReason, str] | None:
    if claim.year != target.year:
        return RejectionReason.WRONG_YEAR, "Threshold year does not match target year."

    is_national_legal_minimum = (
        _enum_value(claim.score_type) == ScoreType.LEGAL_MINIMUM
        and claim.program_group_code is None
    )
    if not is_national_legal_minimum and not _program_code_matches(
        claim.program_group_code,
        target,
    ):
        return (
            RejectionReason.WRONG_PROGRAM_GROUP,
            "Threshold program group does not match target program group.",
        )

    required_context = (
        claim.admission_route,
        claim.funding_type,
        claim.applicant_background,
        claim.quota_category,
        claim.instruction_language,
    )
    if any(value is None for value in required_context):
        return (
            RejectionReason.MISSING_REQUIRED_CONTEXT,
            "Threshold is missing route, funding, applicant, quota, or language context.",
        )

    if claim.score == 25 and (
        _enum_value(claim.admission_route) == AdmissionRoute.STANDARD
        or _enum_value(claim.applicant_background)
        == ApplicantBackground.GENERAL_SECONDARY
    ):
        return (
            RejectionReason.ROUTE_SCORE_REQUIRES_CONTEXT,
            "A score of 25 cannot be accepted for standard general-secondary admission.",
        )

    if (
        _enum_value(claim.score_type) == ScoreType.HISTORICAL_GRANT_CUTOFF
        and _enum_value(claim.funding_type) != FundingType.GRANT
    ):
        return (
            RejectionReason.MISSING_REQUIRED_CONTEXT,
            "Historical grant cutoffs must be explicitly grant-funded.",
        )

    return None


def _validate_program_identity(
    claim: ProgramIdentityClaim,
    target: SearchTarget,
) -> tuple[RejectionReason, str] | None:
    if not (
        _program_code_matches(claim.program_group_code, target)
        or _program_name_matches(claim.program_group_name, target)
    ):
        return (
            RejectionReason.WRONG_PROGRAM_GROUP,
            "Program identity does not match target program group.",
        )
    return None


def _validate_subject_requirement(
    claim: ProfileSubjectRequirementClaim,
    target: SearchTarget,
    page_content_by_url: dict[str, str],
) -> tuple[RejectionReason, str] | None:
    if claim.year is not None and claim.year != target.year:
        return RejectionReason.WRONG_YEAR, "Subject requirement year is not the target."

    if not (
        _program_code_matches(claim.program_group_code, target)
        or _target_text_is_supported(claim.evidence, page_content_by_url, target)
    ):
        return (
            RejectionReason.WRONG_PROGRAM_GROUP,
            "Subject requirement does not match target program group.",
        )
    return None


def _validate_university_offering(
    claim: UniversityOfferingClaim,
    target: SearchTarget,
) -> tuple[RejectionReason, str] | None:
    if claim.year is not None and claim.year != target.year:
        return RejectionReason.WRONG_YEAR, "University offering year is not the target."

    if claim.program_group_code is not None and not _program_code_matches(
        claim.program_group_code,
        target,
    ):
        return (
            RejectionReason.WRONG_PROGRAM_GROUP,
            "University offering program group does not match target program group.",
        )
    return None


def validate_claims(
    extraction: AdmissionExtraction,
    pages: list[SearchPage],
    target: SearchTarget,
    field_type: FieldType,
    strategy: SourceStrategy,
) -> ClaimValidationResult:
    """Return accepted claims and explicit rejection diagnostics."""
    page_content_by_url = {page.url: page.content for page in pages}
    rejected: list[RejectedClaim] = []
    program_identities: list[ProgramIdentityClaim] = []
    threshold_claims: list[ThresholdClaim] = []
    subject_requirements: list[ProfileSubjectRequirementClaim] = []
    university_offerings: list[UniversityOfferingClaim] = []

    for claim in extraction.program_identities:
        evidence_error = _validate_evidence(
            "program_identity",
            claim.evidence,
            claim,
            page_content_by_url,
            field_type,
            strategy,
        )
        claim_error = _validate_program_identity(claim, target)
        if evidence_error is not None:
            rejected.append(evidence_error)
        elif claim_error is not None:
            rejected.append(
                _reject("program_identity", *claim_error, claim.evidence, claim)
            )
        else:
            program_identities.append(claim)

    for claim in extraction.threshold_claims:
        evidence_error = _validate_evidence(
            "threshold",
            claim.evidence,
            claim,
            page_content_by_url,
            field_type,
            strategy,
        )
        claim_error = _validate_threshold(claim, target)
        if evidence_error is not None:
            rejected.append(evidence_error)
        elif claim_error is not None:
            rejected.append(_reject("threshold", *claim_error, claim.evidence, claim))
        else:
            threshold_claims.append(claim)

    for claim in extraction.subject_requirements:
        evidence_error = _validate_evidence(
            "subject_requirement",
            claim.evidence,
            claim,
            page_content_by_url,
            field_type,
            strategy,
        )
        claim_error = _validate_subject_requirement(
            claim,
            target,
            page_content_by_url,
        )
        if evidence_error is not None:
            rejected.append(evidence_error)
        elif claim_error is not None:
            rejected.append(
                _reject("subject_requirement", *claim_error, claim.evidence, claim)
            )
        else:
            subject_requirements.append(claim)

    for claim in extraction.university_offerings:
        evidence_error = _validate_evidence(
            "university_offering",
            claim.evidence,
            claim,
            page_content_by_url,
            field_type,
            strategy,
        )
        claim_error = _validate_university_offering(claim, target)
        if evidence_error is not None:
            rejected.append(evidence_error)
        elif claim_error is not None:
            rejected.append(
                _reject("university_offering", *claim_error, claim.evidence, claim)
            )
        else:
            university_offerings.append(claim)

    accepted = AdmissionExtraction(
        program_identities=program_identities,
        threshold_claims=threshold_claims,
        subject_requirements=subject_requirements,
        university_offerings=university_offerings,
    )
    return ClaimValidationResult(accepted=accepted, rejected=tuple(rejected))
