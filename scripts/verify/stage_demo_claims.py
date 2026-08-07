"""Stage realistic accepted candidate claims without calling any external API.

Run with:  python manage.py shell < scripts/verify/stage_demo_claims.py

This mimics exactly what `loader.save` writes after a successful harvest, so the
promotion command and the student-facing endpoints can be verified offline.
Use `harvest_engine` instead when Tavily and OpenAI keys are available.
"""

import hashlib
import json

from apps.careers.models import (
    AdmissionRoute,
    ApplicantBackground,
    EducationalProgramGroup,
    FundingType,
    InstructionLanguage,
    ScoreType,
)
from web_harvester.claim_validation import RejectionReason
from web_harvester.models import CandidateClaim
from web_harvester.source_policy import FieldType, SourceStrategy

YEAR = 2026
GROUP_CODE = "B086"

if not EducationalProgramGroup.objects.filter(code=GROUP_CODE).exists():
    raise SystemExit("Run scripts/verify/seed_identity.py first.")


def fingerprint(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def threshold_payload(
    *,
    score,
    score_type,
    funding,
    url,
    excerpt,
    university=None,
    year=YEAR,
):
    return {
        "score": score,
        "score_type": score_type,
        "year": year,
        "program_group_code": GROUP_CODE,
        "university_name": university,
        "admission_route": AdmissionRoute.STANDARD.value,
        "admission_route_details": None,
        "funding_type": funding,
        "applicant_background": ApplicantBackground.GENERAL_SECONDARY.value,
        "applicant_background_details": None,
        "quota_category": "общий конкурс",
        "instruction_language": InstructionLanguage.LANGUAGE_INDEPENDENT.value,
        "evidence": {"source_url": url, "excerpt": excerpt, "location": "таблица 2"},
    }


accepted = [
    threshold_payload(
        score=50,
        score_type=ScoreType.LEGAL_MINIMUM.value,
        funding=FundingType.GRANT_AND_PAID.value,
        url="https://adilet.zan.kz/rus/docs/V1800017650",
        excerpt="B086 Общая медицина — пороговый балл 50",
    ),
    threshold_payload(
        score=112,
        score_type=ScoreType.HISTORICAL_GRANT_CUTOFF.value,
        funding=FundingType.GRANT.value,
        year=2025,
        url="https://testcenter.kz/grant/2025",
        excerpt="B086 Общая медицина — проходной балл гранта 112",
    ),
    threshold_payload(
        score=118,
        score_type=ScoreType.UNIVERSITY_MINIMUM.value,
        funding=FundingType.GRANT.value,
        university="Казахский национальный медицинский университет",
        url="https://kaznmu.edu.kz/priem-2026",
        excerpt="КазНМУ B086 — минимальный балл 118",
    ),
]

# One rejected claim, so the diagnostics path is visible too.
rejected_payload = threshold_payload(
    score=25,
    score_type=ScoreType.LEGAL_MINIMUM.value,
    funding=FundingType.GRANT_AND_PAID.value,
    url="https://univision.kz/blog",
    excerpt="Минимальный балл 25",
)

created = 0
for payload in accepted:
    evidence = payload["evidence"]
    _row, was_created = CandidateClaim.objects.update_or_create(
        program_group_code=GROUP_CODE,
        target_year=YEAR,
        claim_type="threshold",
        source_url=evidence["source_url"],
        evidence_excerpt=evidence["excerpt"],
        payload_fingerprint=fingerprint(payload),
        defaults={
            "profession_name": "Врач",
            "field_type": FieldType.MEDICINE.value,
            "source_strategy": SourceStrategy.PRIMARY.value,
            "source_tier": 1,
            "confidence": "High",
            "evidence_location": evidence["location"],
            "payload": payload,
            "status": "accepted",
            "rejection_reason": "",
            "rejection_detail": "",
        },
    )
    created += int(was_created)

evidence = rejected_payload["evidence"]
CandidateClaim.objects.update_or_create(
    program_group_code=GROUP_CODE,
    target_year=YEAR,
    claim_type="threshold",
    source_url=evidence["source_url"],
    evidence_excerpt=evidence["excerpt"],
    payload_fingerprint=fingerprint(rejected_payload),
    defaults={
        "profession_name": "Врач",
        "field_type": FieldType.MEDICINE.value,
        "source_strategy": SourceStrategy.PRIMARY.value,
        "source_tier": 1,
        "confidence": "High",
        "evidence_location": evidence["location"],
        "payload": rejected_payload,
        "status": "rejected",
        "rejection_reason": RejectionReason.ROUTE_SCORE_REQUIRES_CONTEXT.value,
        "rejection_detail": (
            "Балл 25 не может быть принят для стандартного приёма после школы."
        ),
    },
)

accepted_rows = CandidateClaim.objects.filter(status="accepted").count()
rejected_rows = CandidateClaim.objects.filter(status="rejected").count()
print("newly created accepted rows :", created)
print("accepted candidate claims   :", accepted_rows)
print("rejected candidate claims   :", rejected_rows)
