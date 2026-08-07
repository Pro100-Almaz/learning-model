"""Persistence boundary for completed profession harvests."""

import hashlib
import json
from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone

from web_harvester import trust
from web_harvester.models import CandidateClaim, Profession
from web_harvester.orchestration import HarvestOutcome, has_useful_facts
from web_harvester.schemas import AdmissionExtraction


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value.strip()))


def _summarize_subjects(result: AdmissionExtraction) -> list[str]:
    return _unique(
        subject for claim in result.subject_requirements for subject in claim.subjects
    )


def _summarize_universities(result: AdmissionExtraction) -> list[str]:
    return _unique([claim.university_name for claim in result.university_offerings])


def _payload_fingerprint(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _claim_rows_from_extraction(result: AdmissionExtraction):
    """Yield claim_type, payload, and evidence fields for every accepted claim."""
    claim_groups = (
        ("program_identity", result.program_identities),
        ("threshold", result.threshold_claims),
        ("subject_requirement", result.subject_requirements),
        ("university_offering", result.university_offerings),
    )
    for claim_type, claims in claim_groups:
        for claim in claims:
            yield (
                claim_type,
                claim.model_dump(mode="json"),
                claim.evidence.source_url,
                claim.evidence.excerpt,
                claim.evidence.location or "",
            )


def _claim_rows_from_rejections(rejected_claims):
    """Yield claim_type, payload, and evidence fields for every rejected claim."""
    for rejected in rejected_claims:
        evidence = rejected.claim.get("evidence") or {}
        yield (
            rejected.claim_type,
            rejected.claim,
            rejected.source_url,
            evidence.get("excerpt", ""),
            evidence.get("location") or "",
            rejected.reason.value,
            rejected.detail,
        )


def _save_candidate_claim(
    *,
    program_group_code: str,
    target_year: int,
    claim_type: str,
    source_url: str,
    evidence_excerpt: str,
    evidence_location: str,
    payload: dict,
    status: str,
    rejection_reason: str,
    rejection_detail: str,
    profession_name: str,
    field_type: str,
    strategy: str,
    tier: int,
    confidence: str,
) -> None:
    CandidateClaim.objects.update_or_create(
        program_group_code=program_group_code,
        target_year=target_year,
        claim_type=claim_type,
        source_url=source_url,
        evidence_excerpt=evidence_excerpt,
        payload_fingerprint=_payload_fingerprint(payload),
        defaults={
            "profession_name": profession_name,
            "field_type": field_type,
            "source_strategy": strategy,
            "source_tier": tier,
            "confidence": confidence,
            "evidence_location": evidence_location,
            "payload": payload,
            "status": status,
            "rejection_reason": rejection_reason,
            "rejection_detail": rejection_detail,
        },
    )

def save(
    name: str,
    national_code: str,
    outcome: HarvestOutcome,
) -> Profession | None:
    """Idempotently save one successful, useful, and trusted harvest outcome."""
    if not outcome.succeeded or outcome.field_type is None:
        return None

    attempt = outcome.successful_attempt
    if attempt is None or attempt.result is None:
        return None

    result = attempt.result
    if not has_useful_facts(result):
        return None

    if outcome.target_year is None:
        raise ValueError("HarvestOutcome.target_year is required to stage claims")

    source_urls = result.source_urls()
    tier, confidence = trust.stamp(
        source_urls,
        outcome.field_type,
        attempt.strategy,
    )
    if tier is None or confidence is None:
        return None

    with transaction.atomic():
        profession, _created = Profession.objects.update_or_create(
            national_code=national_code,
            name=name,
            defaults={
                "field_type": outcome.field_type.value,
                "ubt_score": None,
                "subjects": _summarize_subjects(result),
                "universities": _summarize_universities(result),
                "sources": source_urls,
                "extracted_claims": result.model_dump(mode="json"),
                "source_strategy": attempt.strategy.value,
                "source_tier": tier,
                "confidence": confidence,
                "fetched_at": timezone.now(),
            },
        )
        stamp = {
            "profession_name": name,
            "field_type": outcome.field_type.value,
            "strategy": attempt.strategy.value,
            "tier": tier,
            "confidence": confidence,
            "program_group_code": national_code,
            "target_year": outcome.target_year,
        }

        for (
            claim_type,
            payload,
            source_url,
            excerpt,
            location,
        ) in _claim_rows_from_extraction(result):
            _save_candidate_claim(
                claim_type=claim_type,
                source_url=source_url,
                evidence_excerpt=excerpt,
                evidence_location=location,
                payload=payload,
                status="accepted",
                rejection_reason="",
                rejection_detail="",
                **stamp,
            )

        validation = attempt.validation
        rejected_claims = () if validation is None else validation.rejected
        for (
            claim_type,
            payload,
            source_url,
            excerpt,
            location,
            reason,
            detail,
        ) in _claim_rows_from_rejections(rejected_claims):
            _save_candidate_claim(
                claim_type=claim_type,
                source_url=source_url,
                evidence_excerpt=excerpt,
                evidence_location=location,
                payload=payload,
                status="rejected",
                rejection_reason=reason,
                rejection_detail=detail,
                **stamp,
            )

    return profession
