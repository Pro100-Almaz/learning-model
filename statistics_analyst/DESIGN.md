# Student Analytics Model — Design & Plan

I've traced the whole chain end-to-end. The short version: **~70% of what's described already exists** in `apps/careers` + `apps/analytics` + `apps/roadmap`. The real work is a thin aggregation layer plus one genuinely new piece (the LLM "analyst" that turns numbers into advice), plus one schema change (desired math score at registration).

---

## 1. What already exists (reuse — don't rebuild)

| Need | Already built | Where |
|---|---|---|
| Per-topic accuracy (correct/total/%) | `compute_tag_stats(user)` | `apps/analytics/services.py:39` |
| Weak topics → lessons to fix them | `compute_recommendations(user)` (tags <50%) | `apps/analytics/services.py:108` |
| "Solid vs weak" *classification* — **PRE-module baseline only** (see §1b) | `ChapterLadderSession` verdicts `gap`/`solid`/`mastered` + `StudentTopicMastery.theta`/`p_mastery` | `apps/roadmap/models.py:110`, `mastery.py` |
| Math score → available universities | `calculate_grant(user)` → `predicted_score`, `qualifying_grants[]` (specialties whose latest `GrantThreshold.min_score ≤ predicted`) | `apps/careers/services.py:117` |
| Target/goal + gap + advice | `GoalTracker` in `calculate_grant` (uses `target_score`, weakest tag, RU advice) | `apps/careers/services.py:155` |
| University/specialty/cutoff data | `University` / `Specialty` / `GrantThreshold` models | `apps/careers/models.py` |
| LLM-agent pattern (structured output) | `ChatOpenAI(...).with_structured_output(Schema)` | `web_harvester/agents_web.py:53` |

So the "model" is mostly an **orchestrator** over these, not new math.

---

## 1b. Pre vs Post — what the analytics actually reads

There are **two distinct measurement phases** per topic, and the analytics is about the *second* one. Do not confuse them:

| Signal | Source | Phase |
|---|---|---|
| `theta` / `p_mastery`, ladder `gap/solid/mastered` verdicts | `StudentTopicMastery`, `ChapterLadderSession` | **PRE-module** — placement / baseline, computed *before/during* study |
| Exam `score` + per-question correctness | `TestAttempt` + `AttemptAnswer` | **POST-topic** — the exam sat *after* the module. **This is the analytics input.** |

**The post-topic analytics is built purely from `TestAttempt`/`AttemptAnswer`, keyed by topic — not from mastery/theta.** The pre-stats are not the input; their only role here is an optional **baseline to diff against** (pre→post improvement per topic).

### The post-topic exam → per-topic result path (decided)

- **Which exam:** the **`micro`** `Test` attached to a `RoadmapItem` (`apps/roadmap/models.py:77`) — the post-lesson checkpoint. Filter: `TestAttempt(is_completed=True, source="test", test__type="micro")`.
- **Exam → topic:** via **`TestAttempt.test.lesson.tag`** (`Lesson.tag`, `apps/content/models.py:56`). One micro exam = one topic, so `TestAttempt.score` **is** that topic's post result directly. No answer-level tag aggregation needed.
- **Per topic:** take the student's **latest completed** micro attempt for that topic → `{tag, post_score, correct/total, finished_at}`.
- **Topology reminder:** `Module → Lesson → Lesson.tag (=Tag=topic)`, so a topic's exam, its remedial lessons, and its pre-baseline all key off the same `Tag`.

### New deterministic piece this requires

`compute_recommendations` today reads *lifetime* `compute_tag_stats` (all attempts). For post-only analytics we need a **post-scoped** score source:

```
build_post_topic_results(user) -> dict[tag_id, {tag, post_score, correct, total, finished_at}]
  # latest completed micro TestAttempt per Lesson.tag
classify_topics(post_results) -> {weak[], improving[], solid[]}   # score thresholds, config consts
delta_vs_baseline(post_results, user) -> per-tag {post - pre_baseline → improved|stalled|regressed}  # optional
```

`build_student_report` (§3 Layer 1) then composes these instead of the lifetime tag stats.

---

## 2. The gap between what we want and what exists

Three real gaps, and one conceptual issue to decide on:

**Gap A — no "desired math score" captured at registration.**
`StudentProfile.target_score` is a **total** ҰБТ target (`apps/accounts/models.py:25`), and `ExpectedScore` holds *other* subjects. There is no math-specific target. We need a new field, e.g. `target_math_score` on `StudentProfile`, set in the onboarding/registration serializer.

**Gap B — no single "weak / improving / solid" bucketed view.** The signals exist (verdicts, `p_mastery`, accuracy %) but nothing rolls them into the three buckets the UI wants. This is a ~40-line pure function.

**Gap C — no natural-language analyst.** `statistics_analyst/prompts.py` is empty and the `stats_analyst` branch is fresh — this is clearly meant to be the LLM layer that turns the structured numbers into student-facing prose. Nothing there yet.

**Conceptual issue we MUST resolve — "universities from math score" doesn't map cleanly.**
ҰБТ grant cutoffs (`GrantThreshold.min_score`) are on the **total** score (2 profile subjects + math literacy + reading + history), *not* math alone. So "which universities are available with his math score" can't be a math-only lookup. `calculate_grant` already handles this the correct way: `predicted_total = actual_math + Σ expected_other_subjects → match cutoffs`. We need to decide (see decision #2 below) whether we keep that model or genuinely want a math-only view.

---

## 3. Proposed architecture — two layers

Keep a hard split between **deterministic numbers** (testable, cheap, reused by careers) and the **LLM narrative** (statistics_analyst). Mirrors how `careers` already reuses `analytics`.

```
┌─ Layer 1: deterministic aggregator  (apps/analytics/services.py) ─────────┐
│  build_student_report(user) -> dict                                       │
│    • post_results: per-tag {post_score, correct/total} ← §1b, micro exam  │
│    • topic_breakdown: per-tag {post_score, bucket, (opt) pre→post delta}  │
│    • buckets: weak[] / improving[] / solid[]   ← classify_topics()        │
│    • recommendations: post-scoped weak tags → lessons (see §1b note)      │
│    • math: {current_math, target_math (from profile), gap}               │
│    • universities: reuse careers.calculate_grant() → reachable/near-miss  │
└───────────────────────────────────────────────────────────────────────────┘
                                   │  (plain dict, JSON-serializable)
                                   ▼
┌─ Layer 2: LLM analyst  (statistics_analyst/) ─────────────────────────────┐
│  prompts.py        → ANALYST_SYSTEM_PROMPT + build_analyst_input(report)  │
│  analyst.py        → ChatOpenAI(...).with_structured_output(AnalysisOut)  │
│  schemas.py        → pydantic AnalysisOut {summary, priorities[], ...}    │
│  Produces RU/KZ prose: "Твои слабые темы… сосредоточься на… вузы в         │
│  пределах досягаемости…"                                                   │
└───────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
        API view (apps/analytics/views.py) → GET /api/v1/analytics/report/
```

**Why this split:** Layer 1 is deterministic and unit-testable (no API cost, no flakiness), and other features (roadmap, careers) can consume it. Layer 2 is the only place with LLM cost/latency/nondeterminism, and it consumes a fixed dict — so it can be tested with a canned report and cached/skipped when nothing changed.

---

## 4. Key design decisions (recommendations — these need sign-off)

**Decision 1 — What is a "topic score"? — DECIDED (see §1b).** The post-topic score is the **`score` of the latest completed `micro` exam** for that topic (`test.lesson.tag`). `p_mastery`/verdict are the *pre-module baseline*, not the score — they're only used for the optional pre→post delta. Bucket thresholds on the 0–100 exam score as config constants (e.g. weak <50, improving 50–75, solid >75).

**Decision 2 — How does "math score" drive universities? — DECIDED (keep predicted-total + near-miss).** Reuse `calculate_grant`'s model: actual math (latest completed math mock) + expected other subjects = predicted total → match total-based cutoffs. Add a **"near-miss"** list (cutoffs within N points above predicted) so the student sees "reachable if you gain X." No math-only cutoff dataset — none exists today.

**Decision 3 — Where does the "current math score" come from? — DECIDED (3a: latest math mock).** Use the latest completed math **mock** attempt as the authoritative number (as `careers` already does). Topic→math projection is explicitly deferred: there's no `p_mastery`→ҰБТ calibration in the codebase and no fit data yet; revisit as a labeled "estimate" once mock+topic history exists.

**Decision 4 — Output format. — DECIDED (JSON + prose).** Layer 1 always returns structured JSON (frontend renders charts/tables with no LLM call); Layer 2 adds the narrative on top. Gate the LLM call behind a query param and/or cache it so a plain dashboard load costs no tokens. **Prose is bilingual — RU + KZ** (Decision 5a).

**Decision 5a — Analyst languages — DECIDED (RU + KZ).** The analyst emits both languages (`config.SUPPORTED_LANGUAGES` already lists both). System prompt + output schema carry a language dimension; input report (Layer 1 JSON) is language-neutral. Reuse the existing language plumbing (`config.DEFAULT_LANGUAGE`, the `Question.language` pattern) rather than inventing a new one.

**Decision 6 — Where does the code live?** Layer 1 in `apps/analytics` (reuses its existing services, gets Django test coverage like `test_analytics.py`). Layer 2 in the root `statistics_analyst/` package already started, mirroring `web_harvester`'s agent structure.

---

## 5. Implementation steps (once decisions are locked)

1. **Schema:** add `target_math_score` to `StudentProfile` + migration; capture it in the onboarding/registration serializer (`apps/accounts/serializers.py`).
2. **Post-topic reader + bucketing** in `apps/analytics/services.py`: `build_post_topic_results(user)` (latest completed `micro` attempt per `test.lesson.tag` → score) then `classify_topics(post_results) -> {weak, improving, solid}` on the 0–100 exam score (Decision 1). Optional `delta_vs_baseline`.
3. **Aggregator** `build_student_report(user)`: compose `build_post_topic_results` + `classify_topics` + post-scoped weak-tag recommendations + `careers.calculate_grant` + math gap. Returns one dict. Add near-miss universities.
4. **Serializer + view + URL:** `GET /api/v1/analytics/report/`, `IsAuthenticated`, `drf-spectacular` schema — copy the shape of the existing analytics views.
5. **LLM layer** in `statistics_analyst/`: `schemas.py` (pydantic output), `prompts.py` (RU/KZ system prompt + input builder), `analyst.py` (`with_structured_output`, model id from `config.py` — note it uses OpenAI ids like `gpt-5-mini`; there's also `TUTOR_MODEL = "claude-sonnet-4-6"`).
6. **Tests:** extend `apps/analytics/tests/test_analytics.py` for bucketing + report; a canned-report test for the analyst (mock the LLM).

---

## Open questions

1. ~~Topic score source~~ — **RESOLVED:** post-topic score = latest completed `micro` exam `score`, topic via `test.lesson.tag`; pre-stats used only for optional delta (§1b, Decision 1).
2. ~~Universities~~ — **RESOLVED:** keep the predicted-total model + add near-miss list (Decision 2). No math-only cutoffs.
3. ~~Current math~~ — **RESOLVED:** latest completed math **mock** attempt (Decision 3a). Topic→math projection deferred (no calibration data yet).
4. ~~Desired math score~~ — **RESOLVED:** new `target_math_score` field on `StudentProfile`, separate from total `target_score`, captured at registration.
5. ~~Analyst output~~ — **RESOLVED:** structured JSON (always) + bilingual **RU + KZ** prose narrative on top (Decisions 4, 5a).

**All decisions locked — ready to implement (§5).**
