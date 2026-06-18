# Seed Data Spec

Code can be generated; **this data must be sourced by a human** (universities, thresholds, the question
bank, video URLs). An agent cannot invent real KZ grant thresholds. This file defines the *shapes* and a
small illustrative sample so the import/seed code can be built and tested before the real data lands.

## What must be sourced externally (owner: you / teacher)

| Dataset | Source | Blocks |
|---|---|---|
| Universities + specialties + last-year grant thresholds | Official KZ admissions data, as `.xlsx` | Grant calculator (T-302/303/305) |
| Question bank (per tag) + teacher explanations | Subject teacher | Tests, analytics, the whole loop (T-107) |
| Lesson videos | Recorded, uploaded unlisted to YouTube/Vimeo | Lesson pages (T-105) |

> The demo dies without these. Start them in Week 1 in parallel with code.

## Fixture shapes

**Modules + Tags** (`seed/content.json`)
```json
[
  {"model": "content.module", "pk": 1, "fields": {"title": "Алгебра", "slug": "algebra", "order": 1, "subject": "profile_math"}},
  {"model": "content.tag", "pk": 1, "fields": {"name": "Логарифмы", "slug": "logarithms"}},
  {"model": "content.tag", "pk": 2, "fields": {"name": "Дроби", "slug": "fractions"}}
]
```

**Lesson + Question + Options** (`seed/lessons.json`)
```json
[
  {"model": "content.lesson", "pk": 1, "fields": {
    "module": 1, "title": "Свойства логарифмов", "video_url": "https://youtu.be/XXXX",
    "video_provider": "youtube", "duration_sec": 540, "order": 1}},
  {"model": "assessments.question", "pk": 1, "fields": {
    "text": "Вычислите log2(8)", "explanation": "log2(8)=3, т.к. 2^3=8", "difficulty": 1, "lesson": 1, "tags": [1]}},
  {"model": "assessments.answeroption", "pk": 1, "fields": {"question": 1, "text": "3", "is_correct": true}},
  {"model": "assessments.answeroption", "pk": 2, "fields": {"question": 1, "text": "2", "is_correct": false}}
]
```

## Universities Excel format (for `django-import-export`, T-302)

One row per specialty-threshold. Columns:

| university_code | university_name | city | specialty_code | specialty_name | year | min_score |
|---|---|---|---|---|---|---|
| KBTU | Казахстанско-Британский ТУ | Алматы | 6B06 | Information Systems | 2024 | 118 |
| ENU | ЕНУ им. Гумилёва | Астана | 6B061 | Software Engineering | 2024 | 115 |

Importer upserts University → Specialty → GrantThreshold by code + year.

## Coverage requirements (so the demo looks real)

- **Every tag** has ≥3 questions, or the radar/recommendation has holes.
- ≥2 modules, ≥3 lessons each.
- One **mock test** of realistic length (e.g. 20–30 questions across tags) with `time_limit_sec`.
- ≥15 universities across Алматы/Астана with ≥1 IT specialty each (the common demo target).

## Demo account (T-403) — reproducible

A management command `seed_demo` that creates:
- student `demo@…`, onboarding completed, **target = ЕНУ / IT specialty, target_score = 115**;
- expected scores filled for other subjects;
- **one completed math mock attempt** with a deliberate weakness (e.g. Тригонометрия ~30%) so:
  - analytics shows a clear weak tag,
  - recommendations point to trig lessons,
  - calculator returns a real qualifying list **and** a goal gap with "упор на Тригонометрию".

This single account is what you walk through in the demo. Keep it idempotent so a re-run resets it cleanly.
