# Stocking the Question Bank — Per Topic, Per Difficulty

How to manually fill the `assessments.Question` bank so the chapter ladder has
questions at every difficulty rung for every topic. This uses the **existing**
generation command — no new code. It is written against the real repo:
`agents_and_engine/` (the LangGraph generator) and
`apps/assessments/management/commands/generate_questions.py` (the entry point).

---

## TL;DR

```bash
# one topic, one rung
python manage.py generate_questions --topic quadratic_equations --count 5 --target-score 100
```

- **One blueprint = one topic = one `content.Tag`.** You call the agents once per
  topic.
- **Difficulty is set indirectly by `--target-score`**, so to fill all three rungs
  you call each topic **three times** (one per rung).
- **Always pass `--target-score`.** Omitting it falls back to the blueprint's
  `default_difficulty`, which clusters everything at one rung and leaves the ladder
  unbuildable.
- **Dedup is automatic** (`Question.content_hash`): re-runs top the bank up, never
  duplicate.
- Needs `OPENAI_API_KEY`. Only the Storyteller + Critic agents cost tokens;
  Architect + Publisher are pure Python.

---

## How difficulty is chosen

The command has no `--difficulty` flag. Difficulty (1–3) is derived from the ENT
target score via `resolve_difficulty` (`agents_and_engine/math_engine.py`):

| `--target-score` | Difficulty rung | Ladder meaning |
|------------------|-----------------|----------------|
| `< 90` (e.g. `60`), or omitted → blueprint default | **1** | easy |
| `90`–`119` (e.g. `100`) | **2** | medium |
| `≥ 120` (e.g. `130`) | **3** | hard |

So the three canonical calls per topic use target scores **60 / 100 / 130**.

---

## The command

```bash
python manage.py generate_questions \
    --topic   <blueprint_name> \
    --count   <how_many_to_attempt> \
    --target-score <0-140>
```

- `--topic` — a blueprint filename (without `.json`) under
  `agents_and_engine/blueprints/`. **One blueprint = one topic.**
- `--count` — how many questions to *attempt* for this topic at this difficulty.
  Some attempts may be dedup-skipped or critic-rejected, so the landed count can be
  lower (see "Why landed < count").
- `--target-score` — selects the rung per the table above.

Each run prints, per question: `created`, `duplicate skipped`, or `failed`, then a
final summary line. Output is auditable — it echoes the stored question + options.

---

## Available topics (current blueprints)

Each `.json` in `agents_and_engine/blueprints/` is one topic. There are **26**:

```
arithmetic_progression   calculus_integrals       deformations_xy
domain_extremums         fractional_linear        function_analysis
inv_trig_arithmetic      inv_trig_base            inv_trig_complex
inv_trig_neg             inverse_fractional       quadratic_analysis
quadratic_equations      shifts_xy                symmetry_periodicity
trig_cos                 trig_eq_aux_angle        trig_eq_cos
trig_eq_deg_red          trig_eq_deg_sum          trig_eq_homog
trig_eq_sin              trig_sin                 trig_sys_add
trig_sys_sub             trig_tg_ctg
```

List the current set anytime with:

```bash
ls agents_and_engine/blueprints/*.json | xargs -n1 basename | sed 's/.json//'
```

> **Two caveats before you assume these are ladder-ready:**
>
> 1. **The blueprint set caps coverage.** A `Module` (chapter) has 3–5 topics;
>    stocking cannot create topics that have no blueprint. Extending past these 26
>    (toward the full 10–11 grade curriculum) is a **blueprint-authoring** task, not
>    a generation-run task.
> 2. **Each blueprint carries its *own* fine-grained `content.Tag`** (e.g.
>    `quadratic_equations` → `kvadratnye-uravneniya`, `trig_cos` →
>    `svoystva-kosinusa`). These are a *different* namespace from the coarse seeded
>    tags (`algebra`, `trigonometry`, …) and the seeded lessons (currently
>    `tag = None`). Until lessons are authored and tagged with these same fine
>    slugs, generated questions and lessons live in separate tag-spaces and the
>    ladder can't link them — a **curriculum-wiring** prerequisite, separate from
>    stocking.

---

## One topic, all three rungs

```bash
TOPIC=quadratic_equations
python manage.py generate_questions --topic "$TOPIC" --count 4 --target-score 60    # rung 1 easy
python manage.py generate_questions --topic "$TOPIC" --count 4 --target-score 100   # rung 2 medium
python manage.py generate_questions --topic "$TOPIC" --count 4 --target-score 130   # rung 3 hard
```

---

## All topics, all three rungs (full bank stock)

```bash
for bp in agents_and_engine/blueprints/*.json; do
  topic=$(basename "$bp" .json)
  echo "=== $topic ==="
  python manage.py generate_questions --topic "$topic" --count 4 --target-score 60    # easy
  python manage.py generate_questions --topic "$topic" --count 4 --target-score 100   # medium
  python manage.py generate_questions --topic "$topic" --count 4 --target-score 130   # hard
done
```

Rough size with the current 9 blueprints: `9 topics × 3 rungs × 4 = ~108` graph
runs. Each run costs Storyteller + Critic tokens; budget accordingly.

---

## How many per rung?

For the ladder, each topic needs **≥2 distinct questions per rung** (so the
asymmetric-confirm second question isn't a repeat), ideally 3–4 for variety across
re-tests. Recommended starting target: **`--count 4` per rung**, then re-run any
rung that landed fewer than 2 distinct questions.

---

## Why landed < count (and what to do)

`--count` is *attempts*, not guaranteed inserts. A run can land fewer because:

- **Duplicate skipped** — the rolled problem already exists (`content_hash` hit).
  Expected and good; it means the bank is saturating for that rung. Re-run with a
  higher `--count` if you still need more *distinct* problems.
- **Critic rejected** — the draft failed review after its revision rounds; nothing
  stored. Usually transient; re-run.
- **API / roll error** — one bad attempt is logged and skipped; the batch
  continues.

The final summary line reports `created / duplicates skipped / failed`. Top up by
re-running the same command — dedup guarantees you never double-insert.

---

## Prerequisites & cost

- **`OPENAI_API_KEY`** must be in the environment (Storyteller + Critic call an
  LLM). The Architect and Publisher are pure Python and free.
- **Django configured** — `DJANGO_SETTINGS_MODULE=conf.settings`. `manage.py`
  handles this; the Publisher writes to Postgres.
- **Idempotent** — safe to re-run any time; dedup tops the bank up rather than
  duplicating.

---

## After stocking: verify coverage

Until `report_ladder_coverage` exists (planned step 0 in
`07_Chapter_Ladder_Spec.md`), spot-check from the Django shell:

```bash
python manage.py shell -c "
from apps.assessments.models import Question
from django.db.models import Count
for row in (Question.objects
            .values('tags__slug', 'difficulty')
            .annotate(n=Count('id'))
            .order_by('tags__slug', 'difficulty')):
    print(row['tags__slug'], 'd=', row['difficulty'], '->', row['n'])
"
```

A topic is ladder-ready when it shows **≥2 questions at ≥2 of the three rungs**
(3 rungs is the target; 2 still ladders, just without a `mastered` ceiling — see
the bank-degradation rule in `07_Chapter_Ladder_Spec.md`).
