"""Student-facing queries over canonical, verified admission thresholds.

Everything here reads ``apps.careers.AdmissionThreshold`` only. Legacy
``GrantThreshold`` rows are deliberately not consulted: a legacy row carries no
route, funding, applicant background, or language context, so it cannot answer
"what does this number mean for me?".
"""

from __future__ import annotations

from django.db.models import Q

from apps.careers.models import (
    AdmissionRoute,
    AdmissionThreshold,
    ApplicantBackground,
    FundingType,
    InstructionLanguage,
    ScoreType,
)

NATIONAL_UNIVERSITY_LABEL = ""

# The context that makes two thresholds comparable. Two rows sharing this key
# describe the same admission situation, so only the newest year is applicable.
CONTEXT_FIELDS = (
    "program_group_id",
    "university_id",
    "specialty_id",
    "score_type",
    "admission_route",
    "funding_type",
    "applicant_background",
    "quota_category",
    "instruction_language",
)


def _context_key(threshold: AdmissionThreshold) -> tuple:
    return tuple(getattr(threshold, field) for field in CONTEXT_FIELDS)


def _funding_filter(funding_type: str) -> Q:
    """Grant-only and paid-only requests also match combined grant_and_paid rows."""
    if funding_type in (FundingType.GRANT, FundingType.PAID):
        return Q(funding_type=funding_type) | Q(funding_type=FundingType.GRANT_AND_PAID)
    return Q(funding_type=funding_type)


def _language_filter(instruction_language: str | None) -> Q:
    if instruction_language is None:
        return Q()
    return Q(instruction_language=instruction_language) | Q(
        instruction_language=InstructionLanguage.LANGUAGE_INDEPENDENT
    )


def latest_applicable_thresholds(
    *,
    year: int | None = None,
    score_type: str | None = None,
    admission_route: str,
    funding_type: str,
    applicant_background: str,
    instruction_language: str | None = None,
) -> list[AdmissionThreshold]:
    """Return the newest verified threshold per admission context.

    "Latest" is resolved per context, never globally: a 2026 legal minimum for
    one program group must not hide a 2025 grant cutoff for another.
    Conflicting rows from different sources within one context and year are all
    returned, because they are competing claims, not one averaged number.
    """
    queryset = (
        AdmissionThreshold.objects.select_related(
            "program_group",
            "university",
            "specialty",
            "source",
        )
        .filter(
            verified_at__isnull=False,
            admission_route=admission_route,
            applicant_background=applicant_background,
        )
        .filter(_funding_filter(funding_type))
        .filter(_language_filter(instruction_language))
    )

    if score_type is not None:
        queryset = queryset.filter(score_type=score_type)
    if year is not None:
        queryset = queryset.filter(year=year)

    latest_year_by_context: dict[tuple, int] = {}
    for threshold in queryset:
        key = _context_key(threshold)
        known = latest_year_by_context.get(key)
        if known is None or threshold.year > known:
            latest_year_by_context[key] = threshold.year

    return [
        threshold
        for threshold in queryset
        if threshold.year == latest_year_by_context[_context_key(threshold)]
    ]


def _threshold_labels(threshold: AdmissionThreshold) -> tuple[str, str]:
    university_name = (
        threshold.university.name
        if threshold.university_id
        else NATIONAL_UNIVERSITY_LABEL
    )
    specialty_name = (
        threshold.specialty.name
        if threshold.specialty_id
        else threshold.program_group.name
    )
    return university_name, specialty_name


def _entry(threshold: AdmissionThreshold, **extra) -> dict:
    university_name, specialty_name = _threshold_labels(threshold)
    return {
        "university_name": university_name,
        "specialty_name": specialty_name,
        "min_score": int(threshold.score),
        "year": threshold.year,
        "score_type": threshold.score_type,
        "program_group_code": threshold.program_group.code,
        "source_url": threshold.source.url,
        **extra,
    }


def grant_cutoffs_for_standard_graduate(
    *,
    instruction_language: str | None = None,
) -> list[AdmissionThreshold]:
    """Historical grant cutoffs applicable to a standard general-secondary route."""
    return latest_applicable_thresholds(
        score_type=ScoreType.HISTORICAL_GRANT_CUTOFF,
        admission_route=AdmissionRoute.STANDARD,
        funding_type=FundingType.GRANT,
        applicant_background=ApplicantBackground.GENERAL_SECONDARY,
        instruction_language=instruction_language,
    )


def qualifying_grant_cutoffs(
    predicted_score: float,
    *,
    instruction_language: str | None = None,
) -> list[dict]:
    """Cutoffs the predicted score already clears, biggest margin first."""
    result = [
        _entry(
            threshold,
            margin=int(round(predicted_score - threshold.score)),
        )
        for threshold in grant_cutoffs_for_standard_graduate(
            instruction_language=instruction_language,
        )
        if threshold.score <= predicted_score
    ]
    result.sort(key=lambda entry: entry["margin"], reverse=True)
    return result


def near_miss_grant_cutoffs(
    predicted_score: float,
    within: int,
    *,
    instruction_language: str | None = None,
) -> list[dict]:
    """Cutoffs just above the predicted score, closest target first."""
    result = [
        _entry(
            threshold,
            points_needed=int(round(threshold.score - predicted_score)),
        )
        for threshold in grant_cutoffs_for_standard_graduate(
            instruction_language=instruction_language,
        )
        if predicted_score < threshold.score <= predicted_score + within
    ]
    result.sort(key=lambda entry: entry["points_needed"])
    return result


def latest_grant_cutoff_payload(thresholds) -> dict | None:
    """Summarize the newest verified grant cutoff among prefetched rows."""
    verified = [
        threshold
        for threshold in thresholds
        if threshold.verified_at is not None
        and threshold.score_type == ScoreType.HISTORICAL_GRANT_CUTOFF
    ]
    if not verified:
        return None

    latest = max(verified, key=lambda threshold: (threshold.year, threshold.pk))
    return {
        "score": int(latest.score),
        "year": latest.year,
        "score_type": latest.score_type,
        "source_url": latest.source.url,
    }
