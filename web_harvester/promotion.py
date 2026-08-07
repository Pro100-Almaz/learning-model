"""Controlled promotion of accepted candidate claims into canonical data."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.careers.identity import AmbiguousIdentityError, resolve_program_group
from apps.careers.models import (
    AdmissionSource,
    AdmissionThreshold,
    EducationalProgramGroup,
    ScoreType,
    University,
)
from web_harvester.models import CandidateClaim

REQUIRED_PAYLOAD_KEYS = (
    "year",
    "score",
    "score_type",
    "admission_route",
    "funding_type",
    "applicant_background",
    "quota_category",
    "instruction_language",
)

UNIQUENESS_FIELDS = (
    "program_group",
    "university",
    "specialty",
    "year",
    "score",
    "score_type",
    "admission_route",
    "admission_route_details",
    "funding_type",
    "applicant_background",
    "applicant_background_details",
    "quota_category",
    "instruction_language",
    "source",
)


class PromotionSkipped(Exception):
    """A candidate cannot be promoted because canonical identity is missing."""


@dataclass(frozen=True, slots=True)
class PromotionResult:
    promoted: int
    skipped: int
    failed: int
    messages: tuple[str, ...]


def _source_from_candidate(candidate: CandidateClaim) -> AdmissionSource:
    """Return the canonical source snapshot for one harvested claim."""
    source, _created = AdmissionSource.objects.get_or_create(
        url=candidate.source_url,
        content_fingerprint=candidate.payload_fingerprint,
        defaults={
            "retrieved_at": candidate.harvested_at or timezone.now(),
            "title": "",
            "publisher": "",
            "publication_date": None,
            "original_language": "",
        },
    )
    return source


def _resolve_program_group(
    payload: dict,
    candidate: CandidateClaim,
) -> EducationalProgramGroup:
    """Resolve the canonical program group, or raise PromotionSkipped."""
    code = (payload.get("program_group_code") or "").strip()
    if not code:
        if payload.get("score_type") != ScoreType.LEGAL_MINIMUM:
            raise PromotionSkipped("claim has no program group code")
        code = candidate.program_group_code.strip()

    if not code:
        raise PromotionSkipped("claim has no program group code")

    try:
        group = resolve_program_group(code)
    except AmbiguousIdentityError:
        raise PromotionSkipped(f"ambiguous program group {code}") from None

    if group is None:
        raise PromotionSkipped(f"missing program group {code}")
    return group


def _resolve_university(payload: dict) -> University | None:
    """Resolve an exact university match, or raise PromotionSkipped."""
    name = (payload.get("university_name") or "").strip()
    if not name:
        return None

    matches = list(University.objects.filter(name__iexact=name)[:2])
    if not matches:
        raise PromotionSkipped(f"missing university {name}")
    if len(matches) > 1:
        raise PromotionSkipped(f"ambiguous university {name}")
    return matches[0]


def _threshold_from_candidate(
    candidate: CandidateClaim,
    payload: dict,
    program_group: EducationalProgramGroup,
    university: University | None,
    source: AdmissionSource,
) -> AdmissionThreshold:
    """Map one accepted candidate payload onto an unsaved AdmissionThreshold."""
    missing = [key for key in REQUIRED_PAYLOAD_KEYS if payload.get(key) is None]
    if missing:
        raise PromotionSkipped(f"payload is missing {', '.join(missing)}")

    return AdmissionThreshold(
        program_group=program_group,
        university=university,
        specialty=None,
        source=source,
        year=payload["year"],
        score=payload["score"],
        score_type=payload["score_type"],
        admission_route=payload["admission_route"],
        admission_route_details=payload.get("admission_route_details") or "",
        funding_type=payload["funding_type"],
        applicant_background=payload["applicant_background"],
        applicant_background_details=payload.get("applicant_background_details") or "",
        quota_category=payload["quota_category"],
        instruction_language=payload["instruction_language"],
        evidence_excerpt=candidate.evidence_excerpt,
        evidence_location=candidate.evidence_location,
        verified_at=timezone.now(),
    )


def _persist_threshold(threshold: AdmissionThreshold) -> AdmissionThreshold:
    """Create the threshold, or refresh evidence on the existing identical row."""
    lookup = {field: getattr(threshold, field) for field in UNIQUENESS_FIELDS}
    existing = AdmissionThreshold.objects.filter(**lookup).first()
    if existing is None:
        threshold.save()
        return threshold

    existing.evidence_excerpt = threshold.evidence_excerpt
    existing.evidence_location = threshold.evidence_location
    existing.verified_at = threshold.verified_at
    existing.save(
        update_fields=["evidence_excerpt", "evidence_location", "verified_at"]
    )
    return existing


def _mark_promoted(candidate: CandidateClaim, threshold: AdmissionThreshold) -> None:
    candidate.status = "promoted"
    candidate.promoted_at = timezone.now()
    candidate.promoted_admission_threshold_id = threshold.pk
    candidate.save(
        update_fields=[
            "status",
            "promoted_at",
            "promoted_admission_threshold_id",
            "updated_at",
        ]
    )


def promote_candidate_claims(
    *,
    year: int | None = None,
    program_group_code: str | None = None,
    dry_run: bool = True,
) -> PromotionResult:
    """Promote accepted threshold candidates into canonical admission data."""
    queryset = CandidateClaim.objects.filter(
        status="accepted",
        claim_type="threshold",
    ).order_by("pk")

    if year is not None:
        queryset = queryset.filter(target_year=year)
    if program_group_code is not None:
        queryset = queryset.filter(program_group_code=program_group_code)

    promoted = skipped = failed = 0
    messages: list[str] = []

    for candidate in queryset.iterator():
        payload = candidate.payload or {}
        try:
            with transaction.atomic():
                program_group = _resolve_program_group(payload, candidate)
                university = _resolve_university(payload)
                source = _source_from_candidate(candidate)
                threshold = _threshold_from_candidate(
                    candidate,
                    payload,
                    program_group,
                    university,
                    source,
                )
                # Uniqueness is resolved by _persist_threshold, which must be
                # able to find an identical earlier promotion instead of failing.
                threshold.full_clean(
                    validate_unique=False,
                    validate_constraints=False,
                )

                if dry_run:
                    transaction.set_rollback(True)
                    promoted += 1
                    continue

                saved = _persist_threshold(threshold)
                _mark_promoted(candidate, saved)
                promoted += 1
        except PromotionSkipped as error:
            skipped += 1
            messages.append(f"candidate {candidate.pk}: {error}")
        except ValidationError:
            failed += 1
            messages.append(f"candidate {candidate.pk}: ValidationError")
        except Exception as error:  # noqa: BLE001 - never leak payload text
            failed += 1
            messages.append(f"candidate {candidate.pk}: {type(error).__name__}")

    return PromotionResult(
        promoted=promoted,
        skipped=skipped,
        failed=failed,
        messages=tuple(messages),
    )
