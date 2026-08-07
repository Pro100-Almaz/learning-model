# Admissions web harvesting

The harvester searches canonical profession-to-program-group relationships from
`apps.careers`. It does not accept a hard-coded profession name and an untyped
code. A target exists only when an active profession has an evidenced mapping to
an active educational-program group.

For every target, the query planner searches these facts independently:

- program identity;
- legal admission minimum;
- university admission minimum;
- historical grant cutoff;
- UNT profile subjects;
- universities offering the program.

Every query includes an explicit admission or competition year and one exact
identity anchor. Current program-group codes are searched as current codes.
Legacy `5B...` codes are used only as typed aliases for historical grant queries.
Alternative Kazakh and Russian names are separate query anchors rather than
being combined into one over-constrained query.

## Prerequisites

Before running the command, canonical careers data must contain:

1. an active `Profession`;
2. an active `EducationalProgramGroup`;
3. a source-backed `ProfessionProgramGroup` relationship;
4. any verified legacy codes or alternative names as typed identity records.

If no canonical relationships exist, the command completes without making web
search requests. This is intentional: the harvester must not invent mappings.

## Command

Run the harvester inside Docker:

```sh
docker compose exec backend python manage.py harvest_engine --year 2026
```

Useful bounds:

```sh
docker compose exec backend python manage.py harvest_engine \
  --year 2026 \
  --limit 5 \
  --primary-max-results 4 \
  --fallback-max-results 2
```

The result limits apply to each fact-specific query, not to the complete target.
One failed external query is logged and does not cancel the remaining fact
queries.

## Student-facing consumers

`apps/careers/admission_services.py` is the only read path student-facing code
uses for scores. `latest_applicable_thresholds()` filters verified canonical
thresholds by route, funding, applicant background, and optional language, then
keeps the newest year *per admission context* rather than the newest row
globally. Conflicting claims from different sources stay separate; nothing is
averaged.

The grant calculator (`apps/careers/services.py`) and the analytics report read
`qualifying_grant_cutoffs()` / `near_miss_grant_cutoffs()`, which return only
historical grant cutoffs for a standard general-secondary grant route. A legal
minimum never appears there, and a shortened-route or TVET score cannot qualify
a school graduate. When no canonical data exists the lists are empty — there is
no silent fallback to legacy `GrantThreshold`.

API responses now carry `year`, `score_type`, `program_group_code`, and
`source_url` next to every score, and specialties expose a typed
`latest_grant_cutoff` object alongside the legacy `latest_threshold` number.

## Current boundary

Extraction now returns evidence-bearing candidate claims for program identity,
thresholds, profile subjects, and university offerings. Threshold claims keep
their score type, year, route, funding, applicant background, quota, language,
source URL, and excerpt together.

After extraction, deterministic validation splits raw claims into accepted and
rejected claims. Accepted claims must be backed by a fetched trusted URL, an
evidence excerpt found in the fetched page text, the requested program group or
valid national scope, the requested year, and complete threshold context.
Rejected claims keep an explicit reason for diagnostics. This is the gate that
prevents a special-route score such as 25 from being accepted as a standard
profession score.

## Candidate claim staging

Validation results are durable, not only in-memory. Every accepted and every
rejected claim of a successful harvest is written to `web_harvester.
CandidateClaim` in the same transaction as the legacy `Profession` row.

Each staging row keeps the harvest target (`profession_name`,
`program_group_code`, `target_year`), the trust stamp (`field_type`,
`source_strategy`, `source_tier`, `confidence`), the evidence
(`source_url`, `evidence_excerpt`, `evidence_location`), the full claim
`payload`, and a lifecycle `status` of `accepted`, `rejected`, `promoted`, or
`dismissed`. Rejected rows also keep `rejection_reason` and `rejection_detail`,
so failed search quality is queryable instead of log-only. `review_note` is free
text for manual review.

Idempotency comes from a unique constraint over
`program_group_code`, `target_year`, `claim_type`, `source_url`,
`evidence_excerpt`, and `payload_fingerprint`, where the fingerprint is a
SHA-256 of the deterministically serialized payload. Rerunning the same harvest
updates existing rows instead of duplicating them.

Candidate claims are staging data. They are not canonical admissions data and
must not be read by student-facing code.

## Promotion into canonical data

Staged claims become canonical only through `web_harvester/promotion.py`.
`promote_candidate_claims()` reads accepted threshold candidates, resolves every
canonical foreign key, validates the canonical row, and only then writes
`apps.careers.AdmissionThreshold` plus its `apps.careers.AdmissionSource`
snapshot.

Resolution never guesses:

- the program group is resolved through `apps.careers.identity.
  resolve_program_group`, which honors verified aliases; an unknown or ambiguous
  code skips the candidate;
- a university is matched only by exact case-insensitive name; unknown or
  ambiguous names skip the candidate;
- `specialty` is always `None` for now, because threshold claims carry no
  canonical specialty identity;
- missing payload context skips the candidate instead of inventing defaults.

Promotion is dry run by default and reports counts plus safe skip messages that
never echo payload text. Conflicting scores are preserved: the canonical
uniqueness key includes the source, so two sources claiming different scores for
the same group and year both persist. Rerunning promotion reuses the existing
canonical row instead of creating a duplicate, and a promoted candidate row is
stamped with `status="promoted"`, `promoted_at`, and
`promoted_admission_threshold_id`.

Run it inside Docker:

```sh
docker compose exec backend python manage.py promote_candidate_claims --year 2026
docker compose exec backend python manage.py promote_candidate_claims \
  --year 2026 --program-group-code B086 --commit
```

The harvester still updates the legacy `web_harvester.Profession` row as a
compatibility snapshot, but it no longer writes a scalar `ubt_score` from the
new extraction result. The accepted claim payload is preserved in
`extracted_claims`. Harvested scores remain candidate data and must not be
treated as canonical admissions data until a later verification step promotes
them into `apps.careers`.
