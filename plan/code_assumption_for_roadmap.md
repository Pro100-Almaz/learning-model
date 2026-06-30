# Code Assumptions — Roadmap / Chapter Ladder

This document records the **engineering assumptions** behind the roadmap work
(`06_Roadmap_Engine_Spec.md` and `07_Chapter_Ladder_Spec.md`): what stack it runs
on, what is new code vs. reused, where the AI agents fit (and where they
deliberately don't), and the dependency / ML footprint. It exists so a reader can
answer "what does building this actually touch?" without reverse-engineering the
specs.

It is written against the real repo: a Django starter (`pyproject.toml` still
carries the upstream name `django-starter-template`) with domain apps under
`apps/`, config under `conf/`, and a standalone LangGraph question-generation
engine under `agents_and_engine/`.

---

## TL;DR

> **The chapter-ladder / roadmap runtime is plain, deterministic Django + DRF +
> Postgres. No LLM, no AI agents, no ML frameworks, no new dependencies at runtime.
> The only "learning" is a ~30-line online logistic (Elo/IRT-style) update using
> `math.exp` from the stdlib. The LangGraph AI agents are upstream supply — they
> fill the question bank offline; they are never called while a student is taking a
> ladder.**

---

## 1. Existing stack (what we build on)

**Backend core**

| Concern | Tech (from `pyproject.toml`) |
|---|---|
| Language / runtime | Python **3.13** |
| Web / ORM | **Django 6.0** |
| API | **Django REST Framework 3.15** |
| Database | **PostgreSQL** via **psycopg 3** |
| Cache / broker | **Redis 7** (`django-redis`) |
| Async tasks | **Celery 5.6** + `django-celery-beat` |
| API schema / docs | **drf-spectacular** (OpenAPI) |
| Filtering | **django-filter** |
| Auth | **PyJWT[crypto]** |
| Images | **Pillow** (question images) |
| Serving | **gunicorn**, **whitenoise** |
| Errors / logging | **sentry-sdk**, **python-json-logger** |
| Import/export | **django-import-export**, **openpyxl** |

**Quality gates**

- **ruff** — lint + format, `line-length = 90`, rules `E,F,I,B`, double quotes.
- **mypy** — `python_version = 3.13`.
- Make targets: `make build`, `make test`; lint via `ruff check`.

**AI engine — `agents_and_engine/`** (top-level package, shipped alongside
`apps` and `conf` per `[tool.hatch.build.targets.wheel]`)

| Concern | Tech |
|---|---|
| Agent graph | **LangGraph 1.2** (`StateGraph`) |
| LLM clients | **langchain-openai** (primary, `ChatOpenAI`), **langchain-anthropic** (`ChatAnthropic` available) |
| Pipeline | `architect → storyteller → critic →(revision loop)→ publisher` (`graph.py`, `nodes_self.py`) |
| Determinism | answers computed **natively in Python** (`math_engine.py`), not by the LLM; `content_hash` dedup |
| State contract | `GraphState` (`state.py`) already defines `difficulty: int  # 1-3` and `tag_slug` |

> Key alignment: the generator already emits exactly `difficulty ∈ {1,2,3}` per
> `tag_slug`, which is the precise shape the 3-rung ladder consumes. No new contract
> between generation and the ladder is needed.

---

## 2. Assumption: the ladder runtime is AI-free and dependency-free

The chapter ladder is **deterministic control flow**, not a model call. "Step up on
correct, step down on wrong, early-stop, branch into lessons or hard problems" is a
few lines of Python over data already in Postgres.

**At student runtime the feature uses NONE of:** LLMs, LangGraph, LangChain,
OpenAI/Anthropic, neural nets, GPUs, `numpy`/`sklearn`/`torch`, model training, or
any new pip dependency.

**At student runtime it uses ONLY:** Django ORM, DRF, Postgres, optionally Redis
for caching the small prereq DAG, and `math.exp` from the standard library.

### The one nuance: a tiny statistical model

The persistent mastery estimate **is** a statistical learning model, stated plainly
so this doc isn't misleading:

```
p_pred  = sigmoid(theta - difficulty_to_logit(d))
theta  += K(n) * (outcome - p_pred)     # outcome in {0,1}
n      += 1
```

- It **is** machine learning in the literal sense: `theta` is a per-(student,topic)
  parameter *learned from observations* (an online logistic / Rasch-IRT-style
  update).
- It is **not**: a trained-offline model, a model artifact, a dataset pipeline, a
  framework, or a black box. It is ~30 lines of inspectable arithmetic, fully
  unit-testable, using only `math.exp`.

**Fallback if even that is unwanted:** replace `theta` with a hard rule ("verdict =
highest rung passed; store a counter"). The ladder branching is identical. The
theta model only earns its place by (a) **accumulating across chapters/attempts**
(a topic seen in chapter 3 informs chapter 7) and (b) **degrading gracefully**
instead of flipping on a single noisy MCQ.

---

## 3. Where the AI agents *do* run (upstream, offline)

The LangGraph engine is the ladder's **supply chain, not its runtime**.

```
OFFLINE (fills the bank — AI agents, may cost money/time):
  Celery task → agents_and_engine LangGraph
              → architect → storyteller → critic → publisher
              → new assessments.Question rows (deduped by content_hash)

RUNTIME (per student — deterministic, free, fast):
  DRF endpoint → apps/roadmap/ladder.py
              → reads Question bank + StudentTopicMastery
              → mastery.py Elo update (math.exp)
              → returns next question / chapter_plan
```

Assumption: when `report_ladder_coverage` finds a topic short on a difficulty rung,
we **queue generation runs** (the existing agent graph) targeting that
`tag_slug` + `difficulty`. The agents run ahead of time; the student-facing ladder
only ever does `Question.objects.filter(tags=…, difficulty=…)`.

---

## 4. New / changed code map

All under existing apps — no new Django app required.

| Layer | File | New or changed |
|---|---|---|
| Data | `apps/roadmap/models.py` | **new** `StudentTopicMastery`, `ChapterLadderSession` |
| Data | `apps/content/models.py` | **new** `TagPrerequisite` (optional, for chapter ordering) |
| Data | `apps/assessments/models.py` | **changed** `TestAttempt.test` → nullable + `source` field |
| Migrations | `apps/*/migrations/` | one per model/field change |
| Logic | `apps/roadmap/ladder.py` | **new** `start_ladder`, `next_question`, `record_answer`, `chapter_plan` |
| Logic | `apps/roadmap/mastery.py` | **new** `update_mastery_from_attempt`, `difficulty_to_logit`, `infer_prior` |
| Logic | `apps/roadmap/graph.py` | **new** (optional) prereq DAG + Kahn topological order |
| Logic | `apps/roadmap/services.py` | **changed** planner reads mastery, not one attempt |
| Hook | `apps/assessments/services.py` | **changed** `_trigger_roadmap_hooks` / `finish_attempt` guard `test_id IS NULL` (no-op global roadmap for ladder attempts) |
| API | `apps/roadmap/{views,serializers,urls}.py` | **new** `ladder/start/`, `ladder/next/` (DRF + drf-spectacular) |
| Config | `conf/settings.py` | **new** flags `CHAPTER_LADDER_ENABLED`, `LADDER_CONFIRM`, `LADDER_START_RUNG`, `ROADMAP_USE_MASTERY`, `ADAPTIVE_DIAGNOSTIC_ENABLED` |
| Admin | `apps/roadmap/admin.py`, `apps/content/admin.py` | **new** read-only mastery + ladder-session views; `TagPrerequisite` inline |
| Commands | `apps/roadmap/management/commands/` | **new** `report_ladder_coverage`, `backfill_mastery`, `validate_prereq_dag` |
| Tests | `apps/roadmap/tests/test_ladder.py`, `test_graph.py` | **new**, Django `TestCase` via `make test` |

**Net new third-party dependencies: zero.** Everything above is Django, DRF, the
stdlib, and the ORM.

---

## 5. Integration assumptions

- **Hierarchy mapping:** "chapter" = `content.Module`; its "topics" = the distinct
  `content.Tag`s of its `content.Lesson`s (`Lesson.tag`, ordered by `Lesson.order`).
- **Question signal:** `Question.difficulty` (1–3) and `Question.tags` are the only
  signals the ladder needs; both already exist and are populated by the generator.
- **Attempt storage:** ladder answers are `TestAttempt`/`AttemptAnswer` rows with
  `test=NULL`, `source="ladder"` — so analytics, gamification, and "questions
  answered" counts keep working without synthetic `Test` rows.
- **Mastery writes are inline:** `record_answer` updates `StudentTopicMastery`
  directly; ladder attempts deliberately **bypass** the generic roadmap-generation
  hook (which is why that hook needs the `test_id IS NULL` guard).
- **Async:** only the offline bank-filling uses Celery. The ladder request/response
  is synchronous and cheap.
- **Caching:** the prereq DAG (~100 nodes) is cached in Redis; mastery rows are read
  per request with `select_related`/`prefetch_related`.

---

## 6. What this explicitly is NOT

- Not a new microservice, not a new app, not a new datastore.
- Not an LLM feature — no prompt, no inference, no token cost at student runtime.
- Not a trained ML model — no training job, no model file, no feature store.
- Not dependent on the adaptive global diagnostic (Phase 3 of the global spec); the
  ladder replaces that need with locality.

---

## 7. Open engineering assumptions to confirm

1. **Bank depth:** assumes most topics have ≥2 questions across difficulties 1–3.
   `report_ladder_coverage` (step 0) validates this before committing to the model.
2. **`TestAttempt.test` nullability:** assumes making it nullable is acceptable; the
   alternative (a ladder-specific answer row) is heavier and loses unified history.
3. **Stdlib-only math:** assumes `math.exp` is sufficient (it is) and we do not pull
   in `numpy` for the mastery update.
4. **Generator targeting:** assumes the existing generation pipeline can be asked to
   produce a specific `tag_slug` at a specific `difficulty` to fill coverage gaps.
