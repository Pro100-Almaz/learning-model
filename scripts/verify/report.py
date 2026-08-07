"""End-to-end health report for the admissions harvesting pipeline.

Run with:  python manage.py shell < scripts/verify/report.py

Read-only: the API smoke test runs inside a transaction that is always rolled
back, so nothing it creates survives.
"""

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.assessments.models import Test, TestAttempt
from apps.careers.admission_services import (
    near_miss_grant_cutoffs,
    qualifying_grant_cutoffs,
)
from apps.careers.models import (
    AdmissionSource,
    AdmissionThreshold,
    EducationalProgramGroup,
    ProfessionProgramGroup,
)
from web_harvester.management.commands.harvest_engine import build_search_targets
from web_harvester.models import CandidateClaim

YEAR = 2026
PREDICTED_SCORE = 120


def section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


section("1. CANONICAL IDENTITY (harvest targets)")
print("professions linked to groups :", ProfessionProgramGroup.objects.count())
print("program groups               :", EducationalProgramGroup.objects.count())
targets = build_search_targets(YEAR)
print("search targets built         :", len(targets))
for target in targets:
    print(
        f"  - {target.profession_name} / {target.program_group_code} "
        f"{target.program_group_name} ({target.year}) "
        f"legacy={list(target.legacy_codes)}"
    )
if not targets:
    print("  !! no targets: harvest_engine would do nothing. Seed identity first.")

section("2. CANDIDATE CLAIM STAGING (web_harvester.CandidateClaim)")
print("total    :", CandidateClaim.objects.count())
for status in ("accepted", "rejected", "promoted", "dismissed"):
    print(f"{status:9}:", CandidateClaim.objects.filter(status=status).count())
print()
for claim in CandidateClaim.objects.order_by("status", "pk")[:20]:
    payload = claim.payload or {}
    line = (
        f"  [{claim.status}] {claim.claim_type} "
        f"{claim.program_group_code}/{claim.target_year} "
        f"score={payload.get('score')} type={payload.get('score_type')} "
        f"tier={claim.source_tier} {claim.confidence}"
    )
    print(line)
    print(f"      url      : {claim.source_url}")
    print(f"      evidence : {claim.evidence_excerpt[:70]}")
    if claim.rejection_reason:
        print(f"      rejected : {claim.rejection_reason} — {claim.rejection_detail}")

blank_evidence = CandidateClaim.objects.filter(evidence_excerpt="").count()
print()
print("rows without evidence (must be 0):", blank_evidence)

section("3. CANONICAL ADMISSION DATA (apps.careers.AdmissionThreshold)")
print("sources    :", AdmissionSource.objects.count())
print("thresholds :", AdmissionThreshold.objects.count())
unverified = AdmissionThreshold.objects.filter(verified_at__isnull=True).count()
print("unverified (invisible to students):", unverified)
print()
for threshold in AdmissionThreshold.objects.select_related(
    "program_group", "university", "specialty", "source"
).order_by("program_group__code", "-year"):
    print(
        f"  {threshold.program_group.code} {threshold.year} "
        f"score={threshold.score} type={threshold.score_type} "
        f"route={threshold.admission_route} funding={threshold.funding_type} "
        f"background={threshold.applicant_background} "
        f"lang={threshold.instruction_language}"
    )
    print(f"      university : {threshold.university or '— (national rule)'}")
    print(f"      source     : {threshold.source.url}")
    print(f"      evidence   : {threshold.evidence_excerpt[:70]}")

section(f"4. STUDENT-FACING SERVICE OUTPUT (predicted score {PREDICTED_SCORE})")
qualifying = qualifying_grant_cutoffs(PREDICTED_SCORE)
near_miss = near_miss_grant_cutoffs(PREDICTED_SCORE, within=15)
print("qualifying grant cutoffs :", len(qualifying))
for entry in qualifying:
    print("  ", json.dumps(entry, ensure_ascii=False))
print("near-miss grant cutoffs  :", len(near_miss))
for entry in near_miss:
    print("  ", json.dumps(entry, ensure_ascii=False))
shown = qualifying + near_miss
print()
print("Sanity rules:")
print(
    "  only grant cutoffs are shown   :",
    all(entry["score_type"] == "historical_grant_cutoff" for entry in shown),
)
print(
    "  every entry carries year+source:",
    all(entry.get("year") and entry.get("source_url") for entry in shown),
)

section("5. LIVE API SMOKE TEST (rolled back afterwards)")
original_hosts = list(settings.ALLOWED_HOSTS)
settings.ALLOWED_HOSTS = original_hosts + ["testserver"]
try:
    with transaction.atomic():
        user = get_user_model().objects.create_user(
            email="verify-probe@example.com",
            password="verify-probe-pass-1234",
        )
        test = Test.objects.create(type="mock", title="verify probe mock")
        TestAttempt.objects.create(
            student=user,
            test=test,
            score=PREDICTED_SCORE,
            is_completed=True,
            finished_at=timezone.now(),
        )
        client = APIClient()
        client.force_authenticate(user)

        universities = client.get("/api/v1/careers/universities/")
        print("GET /careers/universities/ ->", universities.status_code)
        print(json.dumps(universities.json(), ensure_ascii=False, indent=2)[:1200])

        calculate = client.post("/api/v1/careers/calculate/")
        print()
        print("POST /careers/calculate/ ->", calculate.status_code)
        print(json.dumps(calculate.json(), ensure_ascii=False, indent=2)[:1600])

        transaction.set_rollback(True)
except Exception as error:
    print("API smoke test failed:", type(error).__name__, error)
finally:
    settings.ALLOWED_HOSTS = original_hosts

print()
print(
    "Note: `latest_grant_cutoff` is null until a cutoff is tied to a Specialty.\n"
    "Promotion sets specialty=None on purpose — threshold claims carry no\n"
    "canonical specialty identity yet, so program-group level is the honest level."
)

section("DONE")
