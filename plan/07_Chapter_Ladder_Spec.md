# Chapter Ladder — Implementation Plan

This is the implementation plan for a **per-chapter, per-topic placement ladder**.
It is an alternative entry-point to the global roadmap described in
`06_Roadmap_Engine_Spec.md`: instead of one global diagnostic that tries to cover
every topic at once, the student is assessed *as they enter each chapter*, on the
3–5 topics that chapter actually teaches.

It is written against the current code: a "chapter" is a
`content.Module`; its topics are the distinct `content.Tag`s of its
`content.Lesson`s (`Lesson.tag`, `Lesson.order`); questions carry a graded
`Question.difficulty` (`PositiveSmallIntegerField`) and link to topics via
`Question.tags`; every completed attempt already flows through
`apps/assessments/services.py::_trigger_roadmap_hooks`.

## Guiding principle

Scope the diagnosis to one chapter at a time. The two problems that make the
global single-shot diagnostic break (single-MCQ noise and the
100-topics-most-untested hole — see `06_Roadmap_Engine_Spec.md` §"Why the current
approach breaks at scale") are solved here by **locality**, not by adaptivity or
heavy ML:

- **Noise** is averaged out because each topic gets *several* questions, laddered
  across difficulty.
- **Scaling** is a non-issue because we never assess 100 topics at once — only the
  3–5 in front of the student.

The unit of assessment is a **topic ladder**: for each topic in the chapter, walk
a short difficulty ladder (easy → medium → hard) with early-stop, and record where
the student's success crosses ~50%. That crossing point *is* the per-topic verdict
**and** a `theta` update — so this design and Phase 2 of the global spec share one
student model rather than competing.

### How this relates to `06_Roadmap_Engine_Spec.md`

| Global spec | Here |
|---|---|
| Phase 1 — global topological roadmap | **Demoted** to chapter *ordering* only (which module next). Optional. |
| Phase 2 — `StudentTopicMastery` + Elo update | **Reused as-is.** The ladder is a fast, scoped way to seed and refresh it. |
| Phase 3 — global adaptive diagnostic | **Replaced** by a trivial per-topic ladder (adaptivity within one topic, not across a graph). |

The only hard dependency on the global spec is the **`roadmap.StudentTopicMastery`**
table and the **`difficulty_to_logit`** helper from its Phase 2. Everything else
here is self-contained.

---

## Locked design decisions

1. **Soft fail.** A topic that shows a gap sends the student into *that topic's
   lessons only* (located by `Lesson.tag` + `Lesson.order`), never a whole-chapter
   restart. Other topics in the chapter that tested solid are skipped.
2. **3 rungs.** The ladder is `easy (d=1) → medium (d=2) → hard (d=3)`. The bank is
   largely `{1, 2}` today; see §"Bank coverage" for the graceful-degradation rule
   when a rung is missing.
3. **Asymmetric confirm.** `LADDER_CONFIRM=True` (one global flag, no per-module
   knob) confirms **only the verdict-deciding *correct* answer** — the one that
   grants a skip — with a second question at that rung. A deciding *wrong* answer is
   accepted on a single attempt (straight to lessons). Rationale in §"The ladder".
4. **Skip-on-prior with staleness re-probe.** A returning student skips a topic's
   ladder when `p_mastery ≥ 0.85 AND n_observations ≥ 4 AND last_seen_at` is recent;
   if the p/n bar is met but the row is stale, a single hard confirming question
   decides. Detail in §"Services".
5. **Logit anchors `{1:-1.0, 2:0.0, 3:+1.0}`** to start, tuned from the analytics
   verdict distribution. The 4-option MCQ guessing floor (~0.25) is the known
   calibration risk; see §"Mapping the verdict to the student model".
6. **Storage = modified reuse.** Ladder answers are `TestAttempt`/`AttemptAnswer`
   rows, but `TestAttempt.test` becomes nullable + a `source` discriminator — no
   synthetic `Test` rows. Mastery is updated **inline** per answer, not via the
   generic hook. Detail in §"New / reused data models".

---

## The ladder

For one topic, starting at the **medium** rung (d=2):

```
                                    ┌─────────────┐
                                    │  medium (2) │
                                    └──────┬──────┘
                                   correct │ wrong
                                    ┌──────┴───────┐
                                    ▼              ▼
                              ┌──────────┐   ┌──────────┐
                              │ hard (3) │   │ easy (1) │
                              └────┬─────┘   └────┬─────┘
                            correct│wrong  correct│wrong
                                  ▼ ▼            ▼ ▼
                          MASTERED | SOLID  SOLID | GAP
```

- **Start medium**, not easy — most signal per question, and a strong student
  finishes in 2 (medium✓ → hard).
- **Right → step up a rung. Wrong → step down a rung.**
- **Early-stop** as soon as a rung is decided. A topic resolves in **2–3
  questions**; the flat "several mediums" approach needed more for the same
  confidence because repeated questions at one difficulty are less informative than
  one question at an adjacent difficulty.
- To damp single-MCQ noise *at the deciding rung*, confirmation is **asymmetric**
  (`LADDER_CONFIRM=True`, one global flag). The two errors don't cost the same:
  - **lucky guess → "correct" → skip lessons** is dangerous — the student skips a
    topic they don't know and fails it later, and on 4-option MCQ a lucky guess is
    ~25% likely;
  - **slip → "wrong" → sent to lessons** is mild and self-correcting — they relearn
    something they knew.

  So confirm **only the verdict-deciding *correct* answer** (the skip-granting one)
  with a second question at that rung; accept a deciding *wrong* answer on a single
  attempt. This protects against lucky-guess skips *and* shortens weak students'
  ladders (fail → no confirm → straight to lessons). When the bank can't supply a
  second question at that rung, accept the single answer and log it.

### Per-topic verdict

The verdict is the highest rung the student reliably clears:

| Outcome | Verdict | Branch |
|---|---|---|
| misses easy (d=1) | **gap** | assign this topic's lessons from `Lesson.order` start |
| clears easy, misses medium | **gap** | assign this topic's lessons from `Lesson.order` start |
| clears medium, misses hard | **solid** | no remediation; topic counts as known |
| clears hard | **mastered** | offer the chapter's hard ("complex") problems for this topic |

`gap` is the soft-fail branch. `solid` and `mastered` skip the topic's lessons.

### Mapping the verdict to the student model

Each ladder answer is a `(difficulty, outcome)` pair — exactly the input
`update_mastery_from_attempt` already takes (`06_Roadmap_Engine_Spec.md` §Phase 2).
So the ladder does **not** invent a parallel scoring scheme:

```
for each ladder answer:
    p_pred = sigmoid(theta - difficulty_to_logit(d))
    theta += K(n) * (outcome - p_pred)
    n_observations += 1
```

The verdict (`gap`/`solid`/`mastered`) is a UI-facing label derived from the
resulting `theta` against two thresholds; `StudentTopicMastery` remains the single
source of truth.

**Logit anchors** start at `{1: -1.0, 2: 0.0, 3: +1.0}` — wide enough that "easy"
reads as clearly easy (at θ=0, P(easy)≈0.73, P(hard)≈0.27) — tuned later from the
analytics verdict distribution. **Known calibration risk:** plain Rasch/Elo has no
guessing term, but 4-option MCQ has a ~0.25 floor, so "wrong on hard" looks more
damning than it is and lucky-rights inflate θ. v1 ships the simple model and leans
on the verdict thresholds + asymmetric confirm to absorb it; the pre-planned
upgrade is a fixed guessing floor `p_pred = 0.25 + 0.75 · sigmoid(theta - d)`,
swappable behind the same `update_mastery_from_attempt` signature.

---

## New / reused data models

**Reused (from `06_Roadmap_Engine_Spec.md` Phase 2) — required:**
`roadmap.StudentTopicMastery` (`theta`, `n_observations`, `last_seen_at`,
`p_mastery`). The ladder is the first writer of these rows for most students.

**New — `roadmap.ChapterLadderSession`** — one in-progress placement per
(student, module). Server-driven so the next rung is chosen on the server and the
client can't see the ladder logic:

```python
class ChapterLadderSession(models.Model):
    student      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                      related_name="ladder_sessions")
    module       = models.ForeignKey("content.Module", on_delete=models.CASCADE,
                                      related_name="ladder_sessions")
    # Per-topic ladder state: {tag_id: {"rung": int, "asked": [qid...],
    #                                    "verdict": "gap|solid|mastered|null"}}
    state        = models.JSONField(default=dict)
    is_complete  = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["student", "module"])]
```

**Modified — `assessments.TestAttempt`** — ladder answers are ordinary
`TestAttempt`/`AttemptAnswer` rows so they remain first-class for analytics,
gamification, and "questions answered" counts. But `TestAttempt.test` is currently
`NOT NULL` (CASCADE) and `Test` assumes a *predefined* `TestQuestion` set — a ladder
picks questions dynamically, so reusing it as-is would force a synthetic `Test` per
session. Two small changes avoid that:

```python
# assessments/models.py — TestAttempt
test   = models.ForeignKey(Test, null=True, blank=True, on_delete=models.CASCADE,
                           related_name="attempts")          # was NOT NULL
source = models.CharField(max_length=20, default="test",
                          choices=[("test", "Test"), ("ladder", "Chapter ladder")])
```

`AttemptAnswer` is unchanged — `(attempt, question)` `unique_together` already fits
the one-answer-per-question ladder.

**Mastery is updated inline.** `record_answer` calls the Phase-2 update per answer,
so a ladder attempt does **not** need the generic `_trigger_roadmap_hooks`
roadmap-generation path. `finish_attempt` / `_trigger_roadmap_hooks` must guard for
`test_id IS NULL` (and/or `source == "ladder"`) and **no-op the global roadmap
generation** for ladder attempts — the ladder owns its own mastery writes.

---

## Services

**`apps/roadmap/ladder.py`** (new):

- `topics_for_module(module) -> list[Tag]` — distinct `Lesson.tag` for the module,
  ordered by min `Lesson.order` (curriculum order within the chapter).
- `start_ladder(student, module) -> ChapterLadderSession` — initialise per-topic
  state with **skip-on-prior** (keeps a strong/returning student's entry short),
  recency-aware so forgetting doesn't silently skip a decayed topic:
  - `p_mastery ≥ 0.85 AND n_observations ≥ 4 AND last_seen_at` recent (≤ ~45 days)
    → mark `mastered`/`solid` up front, never ask.
  - p/n bar met but `last_seen_at` **stale** → don't skip blind; seed the topic to
    ask a **single hard confirming question** (pass → skip; fail → full ladder).
  - otherwise → full ladder.

  Reuse the same `mastered` logit threshold used for verdicts — don't introduce a
  third magic number.
- `next_question(session) -> Question | None` — for the first unresolved topic,
  pick an *unseen* question at the current rung's difficulty for that tag; `None`
  when all topics are resolved (then set `is_complete`).
- `record_answer(session, question, outcome)` — step the rung up/down, apply
  early-stop + optional `LADDER_CONFIRM`, write the verdict, and call
  `update_mastery_from_attempt` for the answer.
- `chapter_plan(session) -> dict` — final per-topic verdicts + the lesson list for
  each `gap` topic and the hard-problem set for each `mastered` topic.

**`difficulty_to_logit`** — reuse from Phase 2 of the global spec. Starting anchors
`{1: -1.0, 2: 0.0, 3: +1.0}` (see §"Mapping the verdict to the student model" for
the rationale and the guessing-floor upgrade path).

---

## API

Server-driven, mirroring the global spec's `diagnostic/next/` shape:

- `POST /api/v1/roadmap/chapter/<module_id>/ladder/start/` → creates/returns the
  session and the first question.
- `POST /api/v1/roadmap/chapter/ladder/next/` (body: session id + answer) → records
  the answer, returns the next question or, when complete, the `chapter_plan`.

Answers serialize like the existing assessment flow; the correct option /
misconception is never exposed (see `assessments.serializers`).

---

## Bank coverage (the real prerequisite)

The ladder needs questions at ≥1 difficulty per topic, ideally all 3 rungs. Today
the bank is largely `difficulty ∈ {1, 2}`. Graceful degradation when a rung is
absent for a topic:

- **Missing hard (d=3):** the top of the ladder collapses to "clears medium →
  `solid`"; `mastered` is simply unreachable for that topic until the bank fills.
  No crash, no silent wrong verdict.
- **Missing medium (d=2):** start at the nearest available rung; ladder becomes the
  2-rung `{1, 3}` (or whatever exists).
- **Only one difficulty:** the topic degrades to a single-rung pass/fail gate
  (the weakest case — explicitly **logged**, never silently treated as a full
  ladder).

Add `report_ladder_coverage` (a management command, cf. the global spec's
`report_bank_coverage`) that, per module, lists each topic's question count by
difficulty and flags topics that can't form a ≥2-rung ladder. Run in CI as a
content health check.

---

## Feature flags (`conf/settings.py`)

- `CHAPTER_LADDER_ENABLED` — gate the whole flow; off → existing chapter entry.
- `LADDER_CONFIRM` — asymmetric confirm of the verdict-deciding *correct* answer
  (default `True`; one global flag, no per-module variant).
- `LADDER_START_RUNG` — default `2` (medium).
- Reuses `ROADMAP_USE_MASTERY` from the global spec for the shared model.

---

## Admin & analytics

- `apps/roadmap/admin.py`: read-only `ChapterLadderSession` view (state +
  verdicts) so content/support can see *why* a student was placed where they were.
- `apps/analytics`: per-module verdict distribution (what fraction land
  gap/solid/mastered per topic) — the calibration feedback loop. If a topic is
  ~100% `gap`, either it's genuinely hard or the medium rung is mistuned; if ~100%
  `mastered`, the rungs are too easy.

---

## Tests

`apps/roadmap/tests/test_ladder.py`:

- ladder steps up on correct, down on wrong; early-stops in 2 on medium✓→hard✓.
- asymmetric confirm: a deciding *correct* answer triggers a second question at that
  rung; a deciding *wrong* answer does not, and goes straight to lessons; when no
  second question exists at the rung, the single answer is accepted and logged.
- skip-on-prior staleness: fresh mastered prior skips the topic; a stale one asks a
  single hard confirming question instead of skipping blind.
- ladder `TestAttempt` rows carry `source="ladder"` with `test=NULL`, and the global
  roadmap hook no-ops for them.
- `gap` when easy is missed; `solid` when medium cleared but hard missed;
  `mastered` when hard cleared.
- soft fail: a `gap` topic yields *its own* lessons (by `Lesson.order`), and a
  `solid` sibling topic in the same module is **not** assigned.
- skip-on-prior: a topic already mastered in `StudentTopicMastery` is not asked.
- every ladder answer writes a mastery update (`theta` moves by `difficulty_to_logit`).
- bank degradation: missing-hard topic caps at `solid`; single-difficulty topic
  logs and falls back to a 1-rung gate.
- `next_question` returns `None` and sets `is_complete` once all topics resolve.

---

## Rollout order & effort

| Step | What it delivers | Rough size | Risk |
|------|------------------|------------|------|
| 0 — `report_ladder_coverage` | Know if the bank can ladder at all | Small | Low |
| 1 — `StudentTopicMastery` + `difficulty_to_logit` | Shared student model (from global spec Phase 2) | Medium | Medium (tuning) |
| 2 — `ladder.py` + `ChapterLadderSession` + endpoints | The placement flow | Medium | Medium |
| 3 — branch wiring (soft-fail lessons / hard problems) + admin/analytics | Closes the loop | Small–Medium | Low |

**Recommendation:** run step 0 first — it's a read-only report that tells us
whether to design for 3 rungs everywhere or accept 2-rung degradation in some
modules — then build steps 1–3 behind `CHAPTER_LADDER_ENABLED`.

---

## Resolved decisions

1. **Confirm rung** — `LADDER_CONFIRM=True`, **asymmetric**: confirm only the
   verdict-deciding *correct* answer (skip-granting), accept a deciding *wrong* on a
   single attempt. One global flag, no per-module variant. (Protects against
   lucky-guess skips; shortens weak students' ladders.)
2. **Skip-on-prior** — `p_mastery ≥ 0.85 AND n_observations ≥ 4 AND last_seen_at`
   recent → skip; p/n met but stale → single hard confirming question; else full
   ladder. Reuse the `mastered` logit threshold, no third magic number.
3. **Logit anchors** — start `{1:-1.0, 2:0.0, 3:+1.0}`; tune from the analytics
   verdict distribution. MCQ guessing floor (~0.25) is the known calibration risk,
   with the `0.25 + 0.75·sigmoid` upgrade pre-planned behind the same signature.
4. **Attempt storage** — modified reuse: `TestAttempt.test` nullable + `source`
   discriminator, no synthetic `Test` rows; mastery updated inline by
   `record_answer`; `finish_attempt`/`_trigger_roadmap_hooks` guard `test_id IS
   NULL` and no-op global roadmap generation for ladder attempts.
