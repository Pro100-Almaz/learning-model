# Ticket Breakdown — agent-executable

One ticket = one PR = one reviewable unit. Each ticket has an ID, owner (**S**=Senior/backend-spine,
**J**=Junior/data, **F**=frontend — note the Senior does most frontend; **F** marks frontend work),
dependencies, and **acceptance criteria** the agent verifies before opening the PR. All criteria are in
addition to the global Definition of Done in `00_README`. Contract reference = `openapi.yaml`.

---

## EPIC 0 — Foundations (Week 1)

**T-001 [S] Backend scaffold + CI**
Deps: none.
AC: `ent-backend` runs locally; apps from data-model spec created; `ruff` + `pytest` wired in GitHub Actions; `/api/docs/` (drf-spectacular) serves the schema and it matches `openapi.yaml`.

**T-002 [S] Models + initial migration**
Deps: T-001.
AC: every model in `02_Data_Model_Spec` migrated; admin registers all of them; invariants (one correct option, ≥1 tag) validated in admin.

**T-003 [S] Google OAuth → JWT**
Deps: T-001.
AC: `POST /auth/google/` returns `AuthTokens`; `POST /auth/refresh/` works; `GET /auth/me/` returns `User` with `onboarding_completed`. Contract test green.

**T-004 [S] Deploy staging (backend + Postgres)**
Deps: T-001.
AC: staging URL live by day 3; `/api/docs/` reachable; CI auto-deploys `main`.

**T-005 [F] Frontend scaffold + AppShell + login**
Deps: T-003 (or mock from contract).
AC: Vite+TS+Tailwind+shadcn app deploys to Vercel/Netlify; responsive shell swaps BottomNav↔Sidebar at `md`; Google login stores JWT and routes to onboarding/home; Axios refresh interceptor works.

**T-006 [J] Seed scaffolding**
Deps: T-002.
AC: management command loads fixtures for modules, tags, and a sample question set; documented in `05_Seed_Data_Spec` format.

---

## EPIC 1 — Learning core (Weeks 2–3)

**T-101 [S] Content endpoints**
Deps: T-002.
AC: `/modules/`, `/modules/{id}/`, `/lessons/{id}/` match contract; `completed` flag reflects the user's attempts; 404s shaped per `Error`.

**T-102 [S] Attempt lifecycle**
Deps: T-101.
AC: `POST /attempts/` returns questions with **no** correctness leaked; `/answer/` records + returns `is_correct` for micro and `null` for mock; `/finish/` computes score; unit tests cover scoring.

**T-103 [S] Error review**
Deps: T-102.
AC: `/attempts/{id}/review/` returns correct option + teacher `explanation` per question; only the attempt's owner can read it.

**T-104 [S] Admin test constructor**
Deps: T-002.
AC: a content manager can build a Test with ordered questions and inline options entirely in Django Admin; publishing a question with 0 tags or ≠1 correct option is blocked.

**T-105 [F] Catalog + Lesson page**
Deps: T-101.
AC: modules→lessons browse on mobile; lesson page embeds YouTube/Vimeo by provider; sticky "Пройти тест по теме" CTA; loading/empty/error states.

**T-106 [F] Micro-test + Results**
Deps: T-102, T-103.
AC: question-by-question flow with instant feedback; results screen shows score; review screen lists mistakes + solutions; works at 360px.

**T-107 [J] Real seed content**
Deps: T-006; teacher content track.
AC: ≥3 lessons + a question bank covering **every** tag, with explanations; loads via the seed command.

---

## EPIC 2 — Analytics + gamification + simulator (Weeks 4–5)

**T-201 [J] Tag aggregation service + endpoint**
Deps: T-102, T-107.
AC: `/analytics/tags/` returns `TagStat[]`; `percent` null-safe; unit-tested against a fixture with known counts.

**T-202 [J] Recommendation endpoint**
Deps: T-201.
AC: `/analytics/recommendations/` returns tags <50% with linked lessons, weakest first; empty array when no weak tags (not an error).

**T-203 [S] Gamification engine**
Deps: T-102.
AC: XP awarded per `XP_RULES` on video + correct answer; level recomputed from `LEVELS`; streak increments once/day and resets after a gap; `/gamification/me/` matches contract.

**T-204 [S] Mock simulator (timed)**
Deps: T-102.
AC: server enforces `time_limit_sec` from `started_at`; auto-finish on expiry; `is_correct` withheld until finish; client cannot extend time.

**T-205 [F] Analytics dashboard**
Deps: T-201, T-202.
AC: Recharts radar + per-tag progress bars + "Что нужно подтянуть" cards linking to lessons; responsive.

**T-206 [F/J] Profile + gamification UI**
Deps: T-203.
AC: XP bar, level badge, streak flame; level-up + XP-gain micro-animations (Framer Motion).

**T-207 [F] Mock simulator UI**
Deps: T-204.
AC: full-screen timed flow; visible countdown with near-expiry warning; auto-submit on timeout; mobile-safe.

---

## EPIC 3 — Onboarding + grant calculator (Weeks 6–7)

**T-301 [S] Profile + onboarding endpoints**
Deps: T-002.
AC: `/profile/` GET/PATCH and `/profile/onboarding-options/` match contract; completing onboarding flips `onboarding_completed`.

**T-302 [J] University DB + Excel import**
Deps: T-002.
AC: `django-import-export` resource imports the universities `.xlsx` into `Specialty`/`GrantThreshold` with validation + preview; real KZ data loaded (`05_Seed_Data_Spec`).

**T-303 [J] Grant calculator service + endpoint**
Deps: T-302, T-201, T-204.
AC: `/careers/calculate/` returns `GrantCalcResult`; predicted = latest completed math mock + expected scores; qualifying list correct; goal gap + weakest-tag advice when target set; HTTP 409 when no completed mock; unit-tested.

**T-304 [F] Onboarding wizard**
Deps: T-301.
AC: multi-step form (target uni/specialty searchable, expected scores, optional target) with Zod validation; guards app until complete.

**T-305 [F] Grant calculator + GoalTracker screen**
Deps: T-303.
AC: "Рассчитать" → qualifying-grants cards + GoalTracker plate ("Прогноз X · не хватает Y · упор на Z"); this is the demo centerpiece — highest polish; 409 handled with a friendly "сначала пройди пробный тест".

---

## EPIC 4 — Hardening + demo (Week 8)

**T-401 [S] Backend hardening**
AC: DRF throttling on auth + answer endpoints; prod config; Postgres backups on; error responses consistent.

**T-402 [F] PWA + responsive QA**
AC: installable PWA (manifest + service worker); Lighthouse PWA + mobile pass; every screen has loading/empty/error; verified on a real Android phone.

**T-403 [J] Final seed + demo account**
AC: believable universities/thresholds, full tag coverage; one reproducible demo student with a completed mock so calculator + analytics are populated.

**T-404 [Both] Demo rehearsal + API freeze**
AC: happy-path walkthrough (login → lesson → test → analytics → calculator) runs clean twice on the demo account; contract frozen.

---

## Dependency-critical path (watch this)

`T-001 → T-002 → T-102 → T-201 → T-303 → T-305`
The calculator (the demo peak) depends on attempts → analytics → thresholds. If anything slips, protect this chain. Frontend can always proceed against the contract mock, so it is rarely the blocker — **content (T-107/T-403) is the real risk.**
