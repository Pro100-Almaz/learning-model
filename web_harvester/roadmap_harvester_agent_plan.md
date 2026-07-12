# Roadmap Web Harvester — 4-Part Agent Plan

Agent-based build of `roadmap_web_harvester_plan.md`. Three agents do the
intelligent work (web search → extract → validate); deterministic code does the
DB gate. This plan is **one part per agent + one part for the DB/orchestration**.

**System shape:**
```
Orchestrator (no LLM)
  └─ pipeline over specialty codes:
        Agent 1 Harvester ─▶ Agent 2 Extractor ─▶ Agent 3 Validator
                                                        │ (advisory verdict)
                                                        ▼
                                 Loader (no LLM) ─▶ Postgres (JSONB)
```

**Rule that keeps it trustworthy:** agents *propose*, deterministic code
*decides* what hits the DB. Agent 3's verdict is advisory; the drift gate and
the commit/abort decision are plain code.

**Suggested build order:** Part 4 (DB) first so the other parts have something to
write against, then Part 1 → 2 → 3. But numbered by component below.

---

## Part 1 — Agent 1: Harvester (web search)
*Finds authoritative pages for one specialty, restricted to the allowlist.*

- **Input:** one `specialty_code` + the allowlist (Tier-1/Tier-2 domains, §2).
- **Actions:**
  - Give the agent WebSearch / WebFetch, scoped so it only ingests allowlisted
    hosts — reject any off-allowlist URL *before* fetching.
  - Search for the current-cycle ҰБТ subject-combo, threshold score, grant count,
    and the universities offering the specialty (passing score, tuition).
  - Capture raw page text (HTML and PDF) per source with its URL and fetch time.
  - Record a **`reachable` flag** per source — reachable-but-empty vs unreachable.
    Part 3's carry-forward guardrail depends on this distinction.
- **Output (schema-forced):**
  `{ specialty_code, sources: [{ url, raw_text, fetched_at, reachable }] }`.
- **Test:** run against a couple of known specialty codes; confirm off-allowlist
  hosts are never fetched and `reachable` is set correctly on a dead link.

**Deliverable:** raw, allowlisted source text per specialty.

---

## Part 2 — Agent 2: Extractor
*Turns raw pages into the §3 JSON document.*

- **Input:** Agent 1's raw sources.
- **Actions:**
  - Parse tables/PDF text into the §3 shape (specialty, field, subject_combination,
    threshold, grants, universities[], professions[]).
  - Wrap **every** value in the provenance envelope
    `{ value, as_of: <year>, carried_forward: false }`.
  - Bilingual rule: Kazakh canonical, Russian in parentheses; if only one language
    is present, store it and flag the missing side — **never machine-translate**.
  - Year-tag from the document's own title/headers; set
    `source_year_confidence: "low"` when the year had to be inferred.
- **Output (schema-forced):** one §3 specialty document.
- **Test:** run on saved sample fixtures (downloaded HTML/PDF) so it's repeatable;
  check the envelope wrapping and bilingual flagging on a single-language source.

**Deliverable:** validated-shape (not yet quality-gated) JSON docs.

---

## Part 3 — Agent 3: Validator
*Judges each doc against the quality rules; output is advisory to the loader.*

- **Input:** Agent 2's doc + last year's snapshot for that code (`priorSnapshots`).
- **Actions (§6):**
  - **Schema** — matches §3 shape.
  - **Range** — scores 0–140, grants ≥ 0, tuition in a sane band.
  - **Referential** — `specialty_code` exists in the classifier; ≥ 1 university.
  - **Corroboration** — Tier-1 vs Tier-2 overlapping numbers agree within tolerance.
  - Report **missing fields** (carry-forward candidates) and counts of
    **freshly-scraped vs total** fields (the drift alarm keys off fresh coverage).
- **Output (schema-forced):**
  `{ verdict: "accept" | "quarantine", reasons[], carry_forward_fields[],
     freshly_scraped_field_count, total_field_count }`.
- **Note:** the agent **never writes to the DB**. Its verdict feeds the
  deterministic loader in Part 4.
- **Test:** feed crafted docs — a clean one (accept), an out-of-range score
  (quarantine), a missing field (carry-forward candidate).

**Deliverable:** a per-specialty verdict the loader can act on.

---

## Part 4 — DB + Orchestration (no LLM)
*The Django side: schema, the deterministic gate, and the entry point that runs
the three agents. Build this first.*

- **Schema / models (§4):**
  - `SpecialtySnapshot` — promoted `year`, `specialty_code`, `source_url`,
    `data` (JSONB); **unique `(year, specialty_code)`**.
  - `HarvestRun` — `run_id`, timings, `records_committed`,
    `fields_carried_forward`, `carry_forward_pct`, `status`, `abort_reason`.
  - `QuarantineRecord` — bad payload + reason + FK to run.
  - Migration; verify round-trip insert + a JSONB query.
- **Seed data:** allowlist constants; classifier loader (`6B…` master code list);
  config `DRIFT_ABORT_PCT=10`, `MAX_CARRY_CYCLES=2`.
- **Orchestrator (management command):**
  - Open a `HarvestRun`; pre-load `priorSnapshots` from last year's rows.
  - Run the workflow (`pipeline(codes, harvest, extract, validate)`).
- **Loader (deterministic gate — the real decision maker):**
  - `accept` → merge + carry-forward missing fields (three guardrails:
    reachable-but-absent only, max carry age, never resurrect a dropped
    specialty) → stage for insert.
  - `quarantine` → `QuarantineRecord`.
  - **Drift gate:** if carried/total ≥ `DRIFT_ABORT_PCT` → **ABORT**: commit
    nothing, leave last year's snapshot untouched, mark the run aborted.
  - Otherwise **COMMIT** transactionally as a new annual snapshot; never overwrite.
  - Emit the run report (committed/aborted, carry-forward % always logged).
- **Schedule:** run the command on the 6–12 month cadence.

**Deliverable:** `python manage.py <harvest_command>` runs the full pipeline and
either commits a new snapshot or aborts cleanly.

---

## Decide before Part 3 (open items, §8)
- Do volatile numeric fields (thresholds / passing scores) carry forward at all,
  or go straight to `null` when missing?
- Final `MAX_CARRY_CYCLES` (default 2).
