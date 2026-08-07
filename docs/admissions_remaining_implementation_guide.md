# Admissions Harvester Remaining Implementation Guide

This guide starts after Mission 6. At this point the project already has:

- a written admissions data contract in `docs/admission_data_contract.md`;
- canonical admissions models in `apps/careers/models.py`;
- identity models and target building for profession-to-program-group harvesting;
- field-specific source policy and fact-specific query planning;
- evidence-bearing extraction schemas in `web_harvester/schemas.py`;
- deterministic claim validation in `web_harvester/claim_validation.py`;
- legacy compatibility persistence in `web_harvester.Profession.extracted_claims`.

The remaining work is not "make search better" in a vague way. The remaining work
is to make harvested candidate claims reviewable, then promote verified claims
into canonical `apps.careers` data, then update application consumers so students
never see untyped or mismatched scores.

Do the remaining work in this order.

## Mission 7 - Durable Candidate Claim Staging And Diagnostics

### What This Mission Does

Mission 6 validates claims in memory during one harvest attempt. Mission 7 makes
that validation result durable.

Right now accepted claims are stored inside the legacy
`web_harvester.Profession.extracted_claims` JSON field. That is useful as a
snapshot, but it is not enough for a review workflow because:

- rejected claims disappear after the run unless you inspect logs;
- accepted and rejected claims cannot be queried independently;
- there is no lifecycle state like `pending_review`, `accepted`, `rejected`, or
  `promoted`;
- there is no clean place to store rejection reasons and reviewer notes;
- rerunning the harvester can overwrite the legacy snapshot.

Mission 7 introduces explicit candidate claim storage owned by `web_harvester`.
These rows are still not canonical. They are staging records for review.

### Files To Create Or Edit

Create:

- `web_harvester/migrations/0004_candidate_claims.py`
- `web_harvester/tests/test_candidate_claim_models.py`

Edit:

- `web_harvester/models.py`
- `web_harvester/loader.py`
- `web_harvester/tests/test_loader.py`
- `docs/web_harvesting.md`

Do not edit:

- `apps/careers/models.py` in Mission 7;
- recommendation services;
- public serializers or views.

Mission 7 is a staging mission, not a student-facing behavior mission.

### Model Design

In `web_harvester/models.py`, add enum-like choices near the existing constants.
Use simple string choices because this app already uses that pattern.

Add these choice groups:

```python
CLAIM_TYPE_CHOICES = [
    ("program_identity", "Program identity"),
    ("threshold", "Threshold"),
    ("subject_requirement", "Subject requirement"),
    ("university_offering", "University offering"),
]

CLAIM_STATUS_CHOICES = [
    ("accepted", "Accepted by validation"),
    ("rejected", "Rejected by validation"),
    ("promoted", "Promoted to canonical data"),
    ("dismissed", "Dismissed after review"),
]
```

Then add a model named `CandidateClaim`.

Implement fields with this exact intent:

- `profession_name`: the target profession text used for harvest.
- `program_group_code`: the target program-group code used for harvest.
- `target_year`: the requested harvest year.
- `field_type`: copied from the classified `FieldType`.
- `source_strategy`, `source_tier`, `confidence`: copied from trust stamping.
- `claim_type`: one of the four claim types.
- `status`: accepted/rejected/promoted/dismissed.
- `source_url`: exact evidence URL.
- `evidence_excerpt`: exact excerpt from the claim.
- `evidence_location`: optional location from the claim evidence.
- `payload`: the full claim JSON.
- `rejection_reason`: blank for accepted claims, populated for rejected claims.
- `rejection_detail`: blank for accepted claims, populated for rejected claims.
- `review_note`: blank text field for manual reviewer notes.
- `harvested_at`: timestamp set at insert.
- `updated_at`: auto-updated timestamp.

The important chunk is the uniqueness rule. You need idempotency, otherwise
rerunning the same profession will duplicate staging records. Use a uniqueness
constraint on the identifying fields:

```python
models.UniqueConstraint(
    fields=[
        "program_group_code",
        "target_year",
        "claim_type",
        "source_url",
        "evidence_excerpt",
        "payload",
    ],
    name="unique_candidate_claim_payload",
)
```

If PostgreSQL rejects a unique constraint over JSONField in your Django/Postgres
combination, replace `payload` in the constraint with a new `payload_fingerprint`
field:

```python
payload_fingerprint = models.CharField(max_length=64)
```

Then compute it in loader using deterministic JSON:

```python
json.dumps(payload, sort_keys=True, ensure_ascii=False)
hashlib.sha256(serialized.encode("utf-8")).hexdigest()
```

Senior recommendation: use `payload_fingerprint`. It is more stable and indexes
cleaner than a uniqueness check over a whole JSON blob.

### Model Validation Logic

Add a `clean()` method on `CandidateClaim`.

Implement these rules:

- `evidence_excerpt.strip()` must not be blank.
- accepted/promoted claims must not have `rejection_reason`.
- rejected/dismissed claims should have `rejection_reason`.
- `source_tier` and `confidence` must match `source_strategy`:
  - primary means tier `1`, confidence `High`;
  - fallback means tier `2`, confidence `Low`.

This model is staging data, so do not enforce all threshold context here. That
already belongs to `claim_validation.py`. The model only enforces storage
integrity.

### Migration

Create `web_harvester/migrations/0004_candidate_claims.py`.

Dependencies:

```python
dependencies = [
    ("web_harvester", "0003_profession_extracted_claims"),
]
```

Add the `CandidateClaim` model. Include indexes for review queries:

```python
models.Index(fields=["status", "target_year"], name="cand_status_year_idx")
models.Index(fields=["program_group_code", "target_year"], name="cand_group_year_idx")
models.Index(fields=["claim_type", "status"], name="cand_type_status_idx")
```

Why these indexes matter:

- reviewers will filter by status;
- promotion will select accepted threshold claims by program group/year;
- diagnostics will group failures by claim type.

### Loader Changes

Edit `web_harvester/loader.py`.

Keep the current legacy `Profession` update. Add a second persistence step inside
the same transaction after the legacy row is saved.

The loader already receives `HarvestOutcome`. After Mission 6, the successful
attempt contains:

- `attempt.result`: accepted claims only;
- `attempt.validation.rejected`: rejected claim diagnostics.

Add helper functions:

```python
def _claim_rows_from_extraction(result, status):
    # Yield tuples of claim_type, claim, payload, evidence.
```

Build rows from:

- `result.program_identities` as `program_identity`;
- `result.threshold_claims` as `threshold`;
- `result.subject_requirements` as `subject_requirement`;
- `result.university_offerings` as `university_offering`.

For each accepted claim, call `CandidateClaim.objects.update_or_create(...)`.

Use lookup fields:

- `program_group_code`
- `target_year`
- `claim_type`
- `source_url`
- `evidence_excerpt`
- `payload_fingerprint`

Use defaults:

- `profession_name`
- `field_type`
- `source_strategy`
- `source_tier`
- `confidence`
- `evidence_location`
- `payload`
- `status="accepted"`
- `rejection_reason=""`
- `rejection_detail=""`

Then persist rejected claims from `attempt.validation.rejected`.

For rejected claims:

- `claim_type` comes from `RejectedClaim.claim_type`;
- `source_url` comes from `RejectedClaim.source_url`;
- `payload` comes from `RejectedClaim.claim`;
- `status="rejected"`;
- `rejection_reason=rejected.reason.value`;
- `rejection_detail=rejected.detail`.

Important: rejected claims may have untrusted or unfetched URLs. Still store
them, because they explain why search quality was bad. Do not call `trust.stamp`
on each rejected URL. Use the attempt-level metadata from the run.

### Tests

In `web_harvester/tests/test_candidate_claim_models.py`, test:

- accepted claim with evidence can be saved;
- blank evidence is rejected by `full_clean()`;
- accepted claim cannot have a rejection reason;
- rejected claim should carry a rejection reason;
- duplicate payload fingerprint is idempotent or rejected by database constraint.

In `web_harvester/tests/test_loader.py`, add tests:

- saving a successful harvest creates accepted `CandidateClaim` rows;
- validation rejected claims are also stored as rejected rows;
- rerunning the same harvest does not duplicate candidate rows;
- legacy `Profession.extracted_claims` still stores only accepted claims.

Expected implementation shape in loader:

```python
with transaction.atomic():
    profession = update_legacy_profession(...)
    save_accepted_candidate_claims(...)
    save_rejected_candidate_claims(...)
return profession
```

### Acceptance Criteria

Mission 7 is done when:

- every accepted claim from Mission 6 has a durable staging row;
- every rejected claim from Mission 6 has a durable diagnostic row;
- reruns are idempotent;
- public app behavior has not changed yet;
- `uv run ruff check web_harvester docs\web_harvesting.md` passes;
- Docker test command passes when Docker is available:

```sh
docker compose exec backend pytest web_harvester/tests
```

## Mission 8 - Promotion From Candidate Claims To Canonical Admissions Data

### What This Mission Does

Mission 8 creates the controlled bridge from `web_harvester` staging data into
canonical `apps.careers` models.

The goal is not to auto-trust the web. The goal is to promote accepted candidate
claims only when all canonical foreign keys and context can be resolved.

Canonical data lives in:

- `apps.careers.AdmissionSource`
- `apps.careers.AdmissionThreshold`
- existing identity models such as `EducationalProgramGroup`, `University`, and
  optionally `Specialty`.

### Files To Create Or Edit

Create:

- `web_harvester/promotion.py`
- `web_harvester/management/commands/promote_candidate_claims.py`
- `web_harvester/tests/test_promotion.py`
- `web_harvester/tests/test_promote_candidate_claims_command.py`

Edit:

- `web_harvester/models.py` only if you need fields such as `promoted_at` or
  `promoted_object_id`;
- `web_harvester/migrations/0005_candidate_claim_promotion_metadata.py` if you
  add those fields;
- `docs/web_harvesting.md`.

Do not edit:

- recommendation logic in Mission 8 unless a test proves a serializer needs a
  small helper for promoted data.

### Promotion Service API

Create `web_harvester/promotion.py`.

Add a dataclass result:

```python
@dataclass(frozen=True, slots=True)
class PromotionResult:
    promoted: int
    skipped: int
    failed: int
    messages: tuple[str, ...]
```

Add the main function:

```python
def promote_candidate_claims(
    *,
    year: int | None = None,
    program_group_code: str | None = None,
    dry_run: bool = True,
) -> PromotionResult:
```

This function should:

1. Query `CandidateClaim.objects.filter(status="accepted", claim_type="threshold")`.
2. Optionally filter by `target_year` and `program_group_code`.
3. For each row, resolve canonical objects.
4. Validate canonical model with `full_clean()`.
5. Save only when `dry_run=False`.
6. Mark the candidate row as promoted only after canonical save succeeds.

### Source Creation

For every promoted threshold, create or reuse `AdmissionSource`.

Implement helper:

```python
def _source_from_candidate(candidate: CandidateClaim) -> AdmissionSource:
```

Use:

- `url = candidate.source_url`
- `retrieved_at = candidate.harvested_at`
- `content_fingerprint = candidate.payload_fingerprint`
- `title = ""`
- `publisher = ""`
- `publication_date = None`
- `original_language = ""`

Use `get_or_create` with:

```python
url=candidate.source_url
content_fingerprint=candidate.payload_fingerprint
```

Why this matters: canonical thresholds need an exact source snapshot. We do not
yet have downloaded PDFs or page snapshots, so the payload fingerprint is the
best available stable fingerprint. Later, you can replace it with a fetched page
content hash.

### Program Group Resolution

Implement helper:

```python
def _resolve_program_group(payload: dict, candidate: CandidateClaim):
```

Logic:

1. Read `payload["program_group_code"]`.
2. If it is blank/null, use `candidate.program_group_code` only for legal
   minimums where the candidate target is the relevant group.
3. Query `EducationalProgramGroup.objects.get(code__iexact=code)`.
4. If no row exists, skip promotion and add a message:
   `missing program group B086`.

Do not create program groups during promotion. Program group identity must
already exist from Mission 2/3 data.

### University Resolution

Implement helper:

```python
def _resolve_university(payload: dict):
```

Logic:

1. Read `payload["university_name"]`.
2. If blank/null, return `None`.
3. Try exact case-insensitive match on `University.name`.
4. If exactly one match, return it.
5. If none or multiple, skip promotion and add a message.

Do not fuzzy-match universities in Mission 8. Fuzzy matching can corrupt data.
If the extractor outputs `KazNMU` but canonical DB has full Russian name, create
a future alias model or manual mapping. Do not guess.

### Specialty Resolution

Initial implementation should set `specialty=None`.

Reason: the current extracted `ThresholdClaim` does not carry a canonical
specialty ID, and matching by free-text program name is risky. The canonical
`AdmissionThreshold` model allows `specialty` to be null. Use program group plus
optional university first.

Later, when `UniversityOfferingClaim` can be reconciled into canonical
`Specialty`, add specialty resolution.

### Threshold Field Mapping

Map candidate payload to `AdmissionThreshold`:

- `program_group`: resolved object;
- `university`: resolved object or `None`;
- `specialty`: `None`;
- `source`: source object;
- `year`: `payload["year"]`;
- `score`: `payload["score"]`;
- `score_type`: `payload["score_type"]`;
- `admission_route`: `payload["admission_route"]`;
- `admission_route_details`: `payload.get("admission_route_details") or ""`;
- `funding_type`: `payload["funding_type"]`;
- `applicant_background`: `payload["applicant_background"]`;
- `applicant_background_details`:
  `payload.get("applicant_background_details") or ""`;
- `quota_category`: `payload["quota_category"]`;
- `instruction_language`: `payload["instruction_language"]`;
- `evidence_excerpt`: `candidate.evidence_excerpt`;
- `evidence_location`: `candidate.evidence_location`;
- `verified_at`: `timezone.now()` if you consider promotion verification.

Important: because Mission 6 rejects null threshold context, these fields should
be present for accepted threshold candidates. Still code defensively. If any
mandatory payload key is missing, skip and report it.

### Idempotent Save

Use `update_or_create` only if the lookup exactly mirrors the canonical
uniqueness definition. Safer first implementation:

1. Build an unsaved `AdmissionThreshold`.
2. Call `full_clean()`.
3. Try `AdmissionThreshold.objects.get(...)` using all uniqueness fields.
4. If found, update evidence fields if needed.
5. If not found, create it.

The lookup fields must match the canonical unique constraint:

- program group
- university
- specialty
- year
- score
- score type
- admission route
- admission route details
- funding type
- applicant background
- applicant background details
- quota category
- instruction language
- source

Why this matters: conflicting scores from different sources must coexist. A
promotion run must not overwrite score `50` with score `55`.

### Candidate Status After Promotion

If you add fields:

- `promoted_at = models.DateTimeField(null=True, blank=True)`
- `promoted_admission_threshold_id = models.PositiveBigIntegerField(null=True, blank=True)`

Then after successful canonical save:

```python
candidate.status = "promoted"
candidate.promoted_at = timezone.now()
candidate.promoted_admission_threshold_id = threshold.pk
candidate.save(update_fields=[...])
```

Do not use a foreign key from `web_harvester` to `apps.careers.AdmissionThreshold`
unless you are comfortable with app-level migration dependencies. A plain integer
is enough for audit metadata.

### Management Command

Create `web_harvester/management/commands/promote_candidate_claims.py`.

Arguments:

- `--year`
- `--program-group-code`
- `--commit`

Default must be dry run. Only save when `--commit` is passed.

Command behavior:

```text
dry run: would promote 12, skipped 3, failed 0
commit: promoted 12, skipped 3, failed 0
```

Do not print raw exception details if they might contain payload data. Print
exception class and a short safe message.

### Tests

In `web_harvester/tests/test_promotion.py`, test:

- dry run does not create `AdmissionThreshold`;
- commit creates `AdmissionSource` and `AdmissionThreshold`;
- rerun is idempotent;
- missing program group skips;
- unknown university skips only for university-specific threshold;
- conflicting scores from different candidate/source rows both persist;
- invalid candidate payload is skipped and reported.

In command tests:

- default command is dry run;
- `--commit` calls promotion with `dry_run=False`;
- filters are passed through;
- command summarizes result.

### Acceptance Criteria

Mission 8 is done when:

- accepted threshold candidates can be promoted into canonical
  `AdmissionThreshold`;
- rejected candidates never promote;
- promotion is dry-run by default;
- conflicting claims are preserved;
- promotion never creates guessed program groups or universities;
- canonical model `full_clean()` runs before save;
- tests cover idempotency and missing identity cases.

## Mission 9 - Replace Student-Facing Consumers With Canonical Admission Claims

### What This Mission Does

Mission 9 makes the app stop relying on legacy `GrantThreshold` as the only score
source. The student-facing API should read verified canonical
`AdmissionThreshold` claims with context.

This is where the search repair finally reaches users.

### Files To Create Or Edit

Edit:

- `apps/careers/services.py`
- `apps/careers/serializers.py`
- `apps/careers/views.py`
- `apps/accounts/serializers.py` if onboarding still exposes legacy
  `latest_threshold`;
- `apps/analytics/services.py` if analytics recommendations still read legacy
  thresholds;
- relevant tests under `apps/careers/tests/`, `apps/accounts/tests/`, and
  `apps/analytics/tests/`.

Potentially create:

- `apps/careers/admission_services.py`
- `apps/careers/tests/test_admission_services.py`

Senior recommendation: create `apps/careers/admission_services.py`. Do not grow
`services.py` further; it already mixes calculator logic and near-miss behavior.

### Core Query Service

Create a function:

```python
def latest_applicable_thresholds(
    *,
    year: int | None = None,
    score_type: str | None = None,
    admission_route: str,
    funding_type: str,
    applicant_background: str,
    instruction_language: str | None = None,
):
```

The function should return canonical `AdmissionThreshold` rows only.

Base queryset:

```python
AdmissionThreshold.objects.select_related(
    "program_group",
    "university",
    "specialty",
    "source",
)
```

Filters:

- `verified_at__isnull=False`
- `admission_route=...`
- `funding_type` matching requested funding, plus optionally
  `grant_and_paid` when requested funding is grant or paid.
- `applicant_background=...`
- if `instruction_language` is provided:
  - exact language;
  - plus `language_independent`.
- if `score_type` provided, filter by it.
- if `year` provided, filter by year.

For "latest" behavior, do not simply use newest row globally. Latest must be per
context. Implement a grouping key in Python first because correctness matters
more than clever ORM:

```python
key = (
    threshold.program_group_id,
    threshold.university_id,
    threshold.specialty_id,
    threshold.score_type,
    threshold.admission_route,
    threshold.funding_type,
    threshold.applicant_background,
    threshold.quota_category,
    threshold.instruction_language,
)
```

Keep the row with the highest `year` for each key.

### Replace `_qualifying_grants`

In `apps/careers/services.py`, `_qualifying_grants` currently reads
`Specialty.thresholds`, which is the old `GrantThreshold` model. Replace or wrap
it with canonical thresholds.

Suggested implementation:

1. Use `AdmissionThreshold` rows with:
   - `score_type=historical_grant_cutoff`;
   - `funding_type=grant`;
   - `admission_route=standard`;
   - `applicant_background=general_secondary`.
2. For each threshold where `threshold.score <= predicted_score`, return:
   - `university_name`: threshold university name if present, otherwise empty or
     national label;
   - `specialty_name`: threshold specialty name if present, otherwise
     `threshold.program_group.name`;
   - `min_score`: threshold score;
   - `margin`: predicted minus threshold score;
   - optionally `year`, `score_type`, and `source_url` after serializer update.

Do not mix legal minimum and historical grant cutoff in the same list. A legal
minimum says "eligible to apply"; a historical grant cutoff says "this was a
past winning score." They answer different questions.

If no canonical thresholds exist yet, decide explicitly:

- either return an empty list;
- or fall back to legacy `GrantThreshold` with a clearly named fallback function.

Senior recommendation: return empty list initially. Silent fallback to legacy
data can reintroduce the exact ambiguity we are removing.

### Update Serializers

Current `QualifyingGrantSerializer` has:

- `university_name`
- `specialty_name`
- `min_score`
- `margin`

Add fields if frontend can handle them:

- `year`
- `score_type`
- `program_group_code`
- `source_url`

If API compatibility is sensitive, add fields as optional/read-only without
removing existing fields.

Example serializer additions:

```python
year = serializers.IntegerField(required=False)
score_type = serializers.CharField(required=False)
program_group_code = serializers.CharField(required=False)
source_url = serializers.URLField(required=False)
```

### University List Endpoint

`UniversitySerializer.get_specialties()` currently reports legacy
`latest_threshold` from `GrantThreshold`.

Replace the meaning carefully. Options:

1. Keep `latest_threshold` as legacy until frontend is updated.
2. Add `latest_admission_threshold` with typed context.
3. Remove the field in a versioned API later.

Recommended Mission 9 implementation:

- keep `latest_threshold` for compatibility;
- add `latest_grant_cutoff` object:

```json
{
  "score": 105,
  "year": 2025,
  "score_type": "historical_grant_cutoff",
  "source_url": "https://..."
}
```

Do this in `SpecialtySerializer`. Use prefetched canonical thresholds to avoid
N+1 queries.

Update `UniversityListView` to prefetch:

```python
Prefetch(
    "specialties__admission_thresholds",
    queryset=AdmissionThreshold.objects.select_related("source").filter(
        verified_at__isnull=False,
        score_type=ScoreType.HISTORICAL_GRANT_CUTOFF,
    ),
)
```

Check the actual related name. In `AdmissionThreshold`, `specialty` has
`related_name="admission_thresholds"`, so from `Specialty` it is
`specialty.admission_thresholds`.

### Analytics And Accounts Consumers

Search for old consumers:

```sh
rg -n "GrantThreshold|thresholds|min_score|latest_threshold" apps
```

For each result:

- if it is import/backward compatibility code, leave it;
- if it drives student recommendations, switch to canonical
  `AdmissionThreshold`;
- if it is only displaying legacy sample data, mark it clearly or add a typed
  field.

Known current areas:

- `apps/careers/services.py`
- `apps/careers/serializers.py`
- `apps/accounts/serializers.py`
- `apps/analytics/services.py`

### Tests

Add tests in `apps/careers/tests/test_admission_services.py`:

- legal minimum does not appear as grant cutoff;
- shortened TVET score 25 does not qualify a standard school graduate;
- historical grant cutoff qualifies when predicted score is high enough;
- latest threshold is selected per context, not globally;
- language-independent threshold can match a requested language;
- conflicting source rows with same context do not get averaged.

Update existing tests:

- `apps/careers/tests/test_calculator.py`
- `apps/accounts/tests/test_profile.py`
- `apps/analytics/tests/test_analytics.py`

When old tests expect `latest_threshold=110`, decide whether they are testing
legacy sample fixtures or student-facing canonical behavior. If student-facing,
rewrite the fixture to create `AdmissionThreshold` with full context and source.

### Acceptance Criteria

Mission 9 is done when:

- public recommendation/calculation code reads canonical `AdmissionThreshold`;
- no student-facing code treats `25` as a normal standard-route score;
- score responses include enough type/year/context for a user to understand
  what the number means;
- legacy `GrantThreshold` can remain in the database, but it is no longer the
  primary recommendation source;
- all affected tests pass in Docker.

## Final Verification Checklist

Run formatting and lint:

```sh
uv run ruff format --check web_harvester apps docs
uv run ruff check web_harvester apps docs
```

Run Django tests in Docker:

```sh
docker compose exec backend pytest web_harvester/tests apps/careers/tests
docker compose exec backend pytest apps/accounts/tests apps/analytics/tests
```

Run migrations:

```sh
docker compose exec backend python manage.py migrate
```

Run a dry promotion:

```sh
docker compose exec backend python manage.py promote_candidate_claims --year 2026
```

Run a committed promotion only after reviewing dry-run output:

```sh
docker compose exec backend python manage.py promote_candidate_claims --year 2026 --commit
```

Run one small harvest after Docker and API keys are available:

```sh
docker compose exec backend python manage.py harvest_engine --year 2026 --limit 1
```

Then inspect:

- candidate rows were created;
- rejected rows have useful reasons;
- accepted threshold claims can promote;
- promoted canonical thresholds have source and evidence;
- frontend/API output shows typed threshold information.

## Common Mistakes To Avoid

Do not auto-promote directly from the LLM extraction result. Always go through
Mission 6 validation and Mission 7 candidate staging.

Do not create program groups or universities during promotion. Missing identity
means skipped promotion, not guessed identity.

Do not silently fall back to legacy `GrantThreshold` in student-facing logic.
That can bring back the same false score behavior.

Do not average conflicting scores. Store both claims and let context/source
review decide.

Do not turn null context into defaults. Unknown route is not standard route.
Unknown funding is not grant. Unknown applicant background is not general
secondary.

Do not promote rejected claims. Rejected rows exist for diagnostics and manual
review, not for recommendations.

## Suggested Scoreboard For The Remaining Work

Mission 7:

- candidate model and migration: 25 XP
- accepted/rejected durable persistence: 35 XP
- idempotent reruns: 20 XP
- tests and docs: 20 XP

Mission 8:

- promotion service: 30 XP
- exact source creation: 15 XP
- canonical threshold mapping: 25 XP
- dry-run command: 10 XP
- idempotency and conflict tests: 20 XP

Mission 9:

- canonical query service: 25 XP
- grant calculator migration: 25 XP
- serializers/API context: 20 XP
- consumer cleanup across accounts/analytics: 15 XP
- regression tests for misleading scores: 15 XP

The project is complete when a harvested `25` score for a shortened TVET route
can be stored and reviewed, but cannot appear to a standard school graduate as
the technical profession score.
