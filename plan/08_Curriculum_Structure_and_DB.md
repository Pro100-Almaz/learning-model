# Curriculum Structure & Database Notes

This documents the ҰБТ profile-math curriculum split (chapters → lessons/topics) that
was seeded into the database, and the database facts that matter for using it:
persistence, accessibility, and reproducibility across machines.

It is the companion to `07_Chapter_Ladder_Spec.md` — the Chapter Ladder walks a
chapter's topics, and this is the content scaffolding that makes those topics exist.

---

## 1. How chapters and topics map (the data model)

A **chapter** is a `content.Module`. Its **topics** are the distinct `Tag`s of its
`content.Lesson`s, via `Lesson.tag`. The ladder discovers a chapter's topics with
`roadmap.ladder.topics_for_module()`, which reads **`Lesson.tag`** — so every lesson
must carry a tag or the ladder is blind to that topic.

```
Module (chapter)
  └── Lesson (Lesson.order)  ── Lesson.tag ─▶ Tag (topic)
                                               ▲
                       Question.tags (M2M) ────┘   ← questions attach to the topic here
```

- `Lesson.tag` — lets the ladder **know a topic exists** in the chapter (topic discovery).
- `Question.tags` — lets the ladder **fetch questions** for a topic at a difficulty rung.

Both are required for the ladder to run a topic: a fully-tagged question bank is
invisible if the lesson isn't tagged, and a tagged lesson resolves to `gap` by default
if it has no questions.

Tag `name`/`slug` are the single source of truth from each topic's blueprint
(`agents_and_engine/blueprints/<topic>.json`, the `"tag"` field). This keeps the
tags identical to the slugs the question-generation pipeline already wrote.

---

## 2. The split — 8 chapters, 26 lessons

Chapters are ordered by grade + prerequisite flow (algebra foundations → functions →
trig functions → inverse trig → trig equations → calculus). Lessons are listed in
teaching order (`Lesson.order`).

### Chapter 1 — Алгебраические основы: прогрессии и уравнения
*(module slug `algebra-basics`; grade 9–10 foundations)*

| # | Lesson | Topic (blueprint) | Tag slug |
|---|--------|-------------------|----------|
| 1 | Арифметическая прогрессия: n-й член и сумма | `arithmetic_progression` | `progressii` |
| 2 | Квадратные уравнения и теорема Виета | `quadratic_equations` | `kvadratnye-uravneniya` |

### Chapter 2 — Свойства и исследование функции
*(module slug `function-properties`; grade 10)*

| # | Lesson | Topic | Tag slug |
|---|--------|-------|----------|
| 1 | Периодичность и чётность/нечётность функции | `symmetry_periodicity` | `period-i-chetnost` |
| 2 | Область определения и точки экстремума | `domain_extremums` | `odf-i-ekstremumy` |
| 3 | Исследование квадратичной функции (парабола) | `quadratic_analysis` | `issledovanie-funktsii` |
| 4 | Исследование графика функции (7 пунктов) | `function_analysis` | `chtenie-grafikov` |

### Chapter 3 — Преобразование графиков и специальные функции
*(module slug `graph-transformations`; grade 10)*

| # | Lesson | Topic | Tag slug |
|---|--------|-------|----------|
| 1 | Сдвиги графиков (вправо/влево, вверх/вниз) | `shifts_xy` | `parallel-perenos` |
| 2 | Сжатие и растяжение графиков | `deformations_xy` | `deformatsiya` |
| 3 | Дробно-линейная функция и асимптоты | `fractional_linear` | `drobno-lineynaya-funkciya` |
| 4 | Обратная функция для дробных выражений | `inverse_fractional` | `obratnaya-funkciya` |

### Chapter 4 — Тригонометрические функции
*(module slug `trig-functions`; grade 10)*

| # | Lesson | Topic | Tag slug |
|---|--------|-------|----------|
| 1 | Свойства и график y = sin x | `trig_sin` | `svoystva-sinusa` |
| 2 | Свойства и график y = cos x | `trig_cos` | `svoystva-kosinusa` |
| 3 | Свойства функций тангенса и котангенса | `trig_tg_ctg` | `tg-i-ctg` |

### Chapter 5 — Обратные тригонометрические функции
*(module slug `inverse-trig-functions`; grade 10)*

| # | Lesson | Topic | Tag slug |
|---|--------|-------|----------|
| 1 | Аркфункции: свойства и табличные значения | `inv_trig_base` | `inv-trig-base` |
| 2 | Аркфункции от отрицательных аргументов | `inv_trig_neg` | `inv-trig-neg` |
| 3 | Арифметические операции и композиции аркфункций | `inv_trig_arithmetic` | `inv-trig-comp` |
| 4 | Связь различных тригонометрических функций | `inv_trig_complex` | `inv-trig-complex` |

### Chapter 6 — Простейшие тригонометрические уравнения и методы
*(module slug `trig-equations-basic`; grade 10)*

| # | Lesson | Topic | Tag slug |
|---|--------|-------|----------|
| 1 | Уравнения вида sin x = a | `trig_eq_sin` | `trig-eq-sin` |
| 2 | Уравнения вида cos x = a | `trig_eq_cos` | `trig-eq-cos` |
| 3 | Однородные тригонометрические уравнения | `trig_eq_homog` | `trig-homogeneous` |
| 4 | Метод вспомогательного угла | `trig_eq_aux_angle` | `trig-aux-angle` |

### Chapter 7 — Методы понижения и системы
*(module slug `trig-equations-advanced`; grade 10)*

| # | Lesson | Topic | Tag slug |
|---|--------|-------|----------|
| 1 | Метод понижения порядка | `trig_eq_deg_red` | `trig-degree-reduction` |
| 2 | Понижение степени и преобразование в произведение | `trig_eq_deg_sum` | `trig-deg-red-sum` |
| 3 | Системы уравнений: метод сложения | `trig_sys_add` | `trig-sys-add` |
| 4 | Системы уравнений: метод подстановки | `trig_sys_sub` | `trig-sys-sub` |

### Chapter 8 — Начала анализа: интеграл
*(module slug `calculus-integrals`; grade 11)*

| # | Lesson | Topic | Tag slug |
|---|--------|-------|----------|
| 1 | Площадь криволинейной трапеции (определённый интеграл) | `calculus_integrals` | `integraly` |

### Rationale (why these seams)

- Grouping follows each blueprint's `curriculum_ref` (its declared slot in the KZ
  program), rolled up from ~13 fine-grained sections into 8 exam-level chapters.
- Chapters 2 and 3 split the old "functions" chapter into *analysing a given function*
  (properties, investigation) vs *building/reshaping functions* (transformations,
  special functions).
- Chapters 6 and 7 split trig equations into *entry-level + classic reduction methods*
  vs *higher-order techniques + systems* (a difficulty ramp).
- Inverse trig (Ch 5) precedes trig equations (Ch 6–7) because arc-functions are the
  notation used to write equation solutions.

---

## 3. Current question coverage (generation status)

The curriculum scaffolding is complete; the question bank is not. As of seeding, only
the trig-equation topics have generated questions:

| Chapter | Topics with questions | Ladder-runnable |
|---|---|---|
| 6 — basic trig equations | 4/4 (`trig-eq-sin` has d1/d2/d3 → `mastered` reachable) | ✅ fully |
| 7 — reduction & systems | 2/4 (both missing d2; the 2 *systems* topics have 0) | ⚠️ partial |
| 1–5, 8 | 0 — topics exist but no questions | ❌ resolve to `gap` by default |

Filling the gaps is purely a **question-generation** task (run the blueprints for the
empty topics; add d3 where `mastered` should be reachable). No content-structure change
is needed.

---

## 4. How it was seeded

Seeded by an idempotent management command:

```
python manage.py seed_curriculum
```

Source: `apps/content/management/commands/seed_curriculum.py`. It:

1. `update_or_create`s the 8 `Module`s (natural key = `slug`).
2. For each topic, reads the blueprint JSON's `tag` and `get_or_create`s the `Tag`
   by `slug` (existing tags are **reused, never duplicated**).
3. `update_or_create`s each `Lesson` (natural key = `(module, tag)`) with its title,
   order, and — critically — `Lesson.tag`.

Idempotent: re-running upserts by natural key, so it is safe to run repeatedly and on
a fresh database. Seed result: **8 Modules, 26 Lessons (all tagged), 26 Tags** (20
created, 6 trig-equation tags reused).

Related seed commands: `seed`/`seed_content` (baseline fixtures via `loaddata`),
`seed_demo` (demo student + attempts).

---

## 5. Database access, persistence & sharing

**Where the data lives.** Postgres runs in Docker (`docker-compose.yml`, service `db`),
storing everything in the named volume `postgres_data` — a folder on the local machine.

**Persistence.** The volume survives container stops and laptop reboots. It is only
lost by `docker compose down -v` or explicitly deleting the volume. So seeded data
stays available on this machine indefinitely.

**Local by default.** The DB is bound to `localhost:5432` (`DATABASE_URL` in `.env`).
The data does not sync anywhere. Another laptop cloning the repo and running
`docker compose up` gets an **empty** Postgres — it must run the seed itself.

**LAN caveat (security).** `docker-compose.yml` publishes `ports: "5432:5432"`, which
binds `0.0.0.0` on the host. On the same LAN another machine could reach
`<host-ip>:5432` with the dev credentials (`postgres/postgres`). Not internet-reachable
(behind NAT), but do not treat this binding as private on a shared network. Fine for
local dev; never ship it.

**Sharing / reproducibility (recommended path).** Put the seed **command in git**.
Then every teammate runs `python manage.py seed_curriculum` and gets an identical
structure — the *recipe* is shared, not a data blob. Alternatives: a `pg_dump`/
`pg_restore` snapshot (stale, not versioned), or a shared hosted Postgres (real infra
decision for staging/prod).

**Bottom line.** Seeding a local DB helps only that machine. This 8-chapter structure
is available "later" on this laptop (persistent volume) and reproducible elsewhere
because it is a committed, idempotent command.
