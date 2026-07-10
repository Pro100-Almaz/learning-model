# Roadmap Engine — Implementation Plan

This is the implementation plan for the per-student roadmap generator. It replaces
the single-shot "one MCQ → weak/strong sides" diagnosis with a continuously-updated
student model, a prerequisite DAG, and (eventually) an adaptive diagnostic. It is
written against the current code: the hook point is
`apps/assessments/services.py::_trigger_roadmap_hooks` (every completed attempt
flows through it), the signal is `Question.difficulty` + `Question.tags` +
`Lesson.tag`, and the planner lives in `apps/roadmap/services.py`.

## Guiding principle

Split the one function that currently does everything
(`generate_roadmap_for_student` reads an attempt *and* emits a plan) into three
independent layers:

1. **Evidence → belief** (student model): every attempt nudges a persistent
   per-topic mastery estimate.
2. **Belief → ordered plan** (planner): pure function of mastery + a prerequisite
   DAG.
3. **Belief → next question** (adaptive diagnostic): use the DAG to cover many
   topics with few questions.

Each phase ships independently and leaves the system working. Phase 1 is pure
graph plumbing (no ML), Phase 2 adds the student model, Phase 3 makes diagnosis
scale.

### Why the current approach breaks at scale

`_compute_tag_mastery` computes `correct / total` per tag from a single
diagnostic attempt. With 26 topics each tag gets 1–3 questions; on 4-option MCQ
that is ~25% noise from guessing alone, and one careless slip flips a topic from
"strong" to "weak". At 100+ topics most tags get **zero** questions and silently
fall into the `"Общая практика"` bucket with no signal. The fix is not a better
single test — it is to stop treating the diagnostic as the source of truth and
let every subsequent interaction refine the estimate.

---

## New data models

Two new tables, both in existing apps. Field specs are migration-ready.

**`content.TagPrerequisite`** — one edge of a DAG over topics:

```python
class TagPrerequisite(models.Model):
    tag      = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="prerequisites")
    requires = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="required_by")
    # "soft" prereqs nudge ordering; "hard" ones gate the topic entirely.
    strength = models.CharField(max_length=10, choices=[("hard", "Hard"), ("soft", "Soft")], default="hard")

    class Meta:
        unique_together = ("tag", "requires")
```

Acyclicity isn't enforceable by a DB constraint — a `clean()` check plus a
`validate_prereq_dag` management command that runs cycle detection on save and in
CI.

**`roadmap.StudentTopicMastery`** — the persistent student model (the thing that
fixes "one test can't tell you"):

```python
class StudentTopicMastery(models.Model):
    student        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                        related_name="topic_mastery")
    tag            = models.ForeignKey("content.Tag", on_delete=models.CASCADE,
                                        related_name="student_mastery")
    theta          = models.FloatField(default=0.0)            # latent ability on this topic (logit scale)
    n_observations = models.PositiveIntegerField(default=0)    # confidence
    last_seen_at   = models.DateTimeField(null=True, blank=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "tag")
        indexes = [models.Index(fields=["student", "tag"])]

    @property
    def p_mastery(self) -> float:        # 0..1 for the UI and the planner threshold
        return 1 / (1 + math.exp(-self.theta))
```

---

## Phase 1 — Prerequisite DAG + topological planner

**Goal:** order the roadmap by dependencies instead of `module.order`. Biggest
quality jump, zero ML.

1. **Model + migration** for `TagPrerequisite` (above).
2. **Seed the edges** — extend
   `apps/accounts/management/commands/seed_demo.py` (or a new
   `seed_prerequisites` command) reading a hand-authored `plan/prereq_edges.yaml`.
   At ~100 topics this is a one-time curriculum-modeling pass, not code. Each
   edge is `tag_slug: [required_slugs...]`.
3. **New service `apps/roadmap/graph.py`:**
   - `build_prereq_graph() -> dict[tag_id, set[tag_id]]` (cached per request).
   - `topological_order(tag_ids, graph)` — Kahn's algorithm restricted to a
     subset; raises on cycle.
4. **Rewrite the ordering in `generate_roadmap_for_student`:** keep mastery
   computation as-is for now, but order weak topics by `topological_order`
   (foundations first), tiebreak by weakness then module order. The
   `"Общая практика"` leftover bucket stays but moves strictly after all
   prereq-ordered items.
5. **Tests** (`apps/roadmap/tests/test_graph.py`, extend `test_generation.py`):
   cycle detection raises; a weak topic never precedes its weak prerequisite;
   deterministic ordering on ties.

*Ships behind no flag — it is strictly better ordering of the same items.*

---

## Phase 2 — Persistent student model (mastery)

**Goal:** stop trusting a single MCQ. Every attempt refines a belief; the roadmap
reads the belief, not one attempt.

1. **Model + migration** for `StudentTopicMastery`.
2. **Update service `apps/roadmap/mastery.py`:**
   - `update_mastery_from_attempt(attempt)` — for each `AttemptAnswer`, for each
     tag on the question, apply a **difficulty-weighted Elo update**:

     ```
     p_pred = sigmoid(theta - difficulty_to_logit(q.difficulty))
     theta += K(n) * (outcome - p_pred)        # outcome in {0, 1}
     n_observations += 1
     ```

     `K(n)` is a learning rate that **decays with `n_observations`** (large early
     moves, small once confident) — this is exactly what kills single-answer
     noise. `difficulty_to_logit` maps `Question.difficulty` 1..N onto the logit
     scale. This is ~30 lines and fully unit-testable.
   - Recommendation: Elo-lite now (one scalar + confidence, handles difficulty,
     accumulates across all attempts). BKT (slip/guess params per skill) is a
     drop-in upgrade later behind the same function signature.
3. **Wire the hook** — in
   `assessments/services.py::_trigger_roadmap_hooks`, add
   `update_mastery_from_attempt(attempt)` for **every** completed attempt
   (diagnostic, micro, mock), before the existing roadmap calls. This is the
   one-line integration that makes micro-tests continuously self-correct the
   diagnosis.
4. **Prior inference for untested topics** (`mastery.py::infer_prior`): a topic
   with no observation inherits a discounted prior from its prerequisites'
   mastery (e.g. `min(prereq p_mastery) * 0.8`). This fixes the scaling hole
   where, with 100+ topics, most never get a diagnostic question and silently
   fall into "general practice".
5. **Rewrite the planner** (`generate_roadmap_for_student`) to read
   `StudentTopicMastery` instead of `_compute_tag_mastery(one_attempt)`:
   - Select topics where `p_mastery < THRESHOLD` (configurable, ~0.7).
   - Distinguish **tested-weak** (high `n_observations`, low mastery) from
     **unknown** (low `n_observations`) in the `rationale` — the UI can show
     "needs assessment" vs "weak topic".
   - Order via Phase-1 topological sort.
   - **Backward-compat:** if a student has zero mastery rows (pre-migration),
     fall back to the old per-attempt computation so existing flows/tests don't
     break. A `backfill_mastery` management command replays historical attempts
     through the update service.
6. **Tests:** one correct answer moves `theta` a little, not to 100%; `K` decay
   means the 10th observation moves less than the 1st; prior inference reads
   prerequisites; planner picks weak + unknown topics in topological order; the
   `finish_attempt` hook writes mastery rows.

---

## Phase 3 — Adaptive diagnostic

**Goal:** cover all of 10–11 grade with ~20–30 questions instead of a fixed bank,
by exploiting the DAG.

1. **Selection service `apps/roadmap/adaptive.py`:**
   - Initialize beliefs for all topics from priors.
   - **Next-question rule:** pick the topic with highest
     *uncertainty × graph-centrality* near the frontier (frontier = topics whose
     prerequisites look mastered but which aren't yet confident). Start
     mid-level.
   - **Correct on a hard topic** → propagate confidence up to its prerequisites
     (likely known) and skip them. **Incorrect** → descend into prerequisites to
     localize the real gap.
   - **Stop** when the question budget is hit or all topics exceed a confidence
     threshold.
2. **Attempt flow:** the diagnostic becomes a server-driven sequence — a new
   endpoint `POST /api/v1/roadmap/diagnostic/next/` returns the next question
   given answers so far (rather than a fixed `Test`). Keep the existing
   fixed-`Test` diagnostic as the fallback behind a feature flag
   (`ADAPTIVE_DIAGNOSTIC_ENABLED`).
3. **Question-bank dependency:** adaptive testing needs several questions per
   topic across difficulties. This couples to the generation pipeline — add a
   coverage report (`report_bank_coverage` command) that flags topics with too
   few questions per difficulty band, and logs gaps rather than silently
   degrading.
4. **Tests:** acing a high topic skips its prereqs; failing descends; the session
   terminates within budget; falls back to fixed diagnostic when the flag is off.

---

## Cross-cutting

- **Feature flags** in `conf/settings.py`: `ROADMAP_USE_MASTERY` (Phase 2),
  `ADAPTIVE_DIAGNOSTIC_ENABLED` (Phase 3). Lets each phase ship dark and flip
  per-environment.
- **Admin** (`apps/roadmap/admin.py`, `apps/content/admin.py`): register
  `TagPrerequisite` (inline on Tag) and a read-only `StudentTopicMastery` view —
  the content team authors the DAG and inspects diagnoses without DB access.
- **Analytics:** `apps/analytics` can surface aggregate per-topic mastery to
  validate that the model matches reality (the feedback loop that tells you the
  math is calibrated).
- **Performance:** mastery update runs inside `finish_attempt`'s existing
  transaction; bulk-update rows, prefetch `question__tags`. The DAG is small
  (~100 nodes) — cache it.

---

## Rollout order & effort

| Phase | What it fixes | Rough size | Risk |
|-------|---------------|------------|------|
| 1 — DAG + topo order | Dependency-correct sequencing | Small (model + Kahn + seed) | Low |
| 2 — Student model | Single-test noise; untested-topic hole | Medium (model + update math + hook + planner rewrite) | Medium (tuning K/threshold) |
| 3 — Adaptive diagnostic | Scaling to all topics | Large (selection + server-driven flow + endpoints) | Higher (needs bank coverage) |

Phase 1 alone is shippable next and removes the most visible weakness. Phase 2 is
the conceptual heart. Phase 3 only pays off once the bank is deep enough — so it
is correctly last.

---

## Open decisions before writing code

1. **Prereq authoring format** — YAML seed file the team edits, or admin-inline
   UI? (Affects whether Phase 1 includes an authoring surface or just a seed
   command.)
2. **Mastery model** — start with Elo-lite as recommended, or go straight to BKT?
   (Recommendation: ship Elo-lite; the function signature is identical so
   swapping later is cheap.)
3. **Mastery threshold + `K` schedule** — pick defaults now (0.7 / decaying K) and
   tune from analytics, or set them deliberately?
4. **Spec location** — this file; implement Phase 1 end-to-end as the first PR.

**Recommendation:** implement Phase 1 end-to-end (model + migration + graph
service + planner ordering + tests) as the first PR, since it is self-contained
and low-risk.
