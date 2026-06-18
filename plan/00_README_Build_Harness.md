# Build Harness — ЕНТ Prep Platform MVP

This pack is the **build-ready layer** that sits on top of the two strategy docs
(`01_Backend_ML_Implementation_Plan`, `02_Frontend_Implementation_Plan`). The strategy docs set
*direction*; this harness gives a coding agent (or a developer) enough precision to start clean,
self-verify, and not drift at the backend↔frontend seam.

## Files in this pack

| # | File | Purpose |
|---|------|---------|
| 00 | `00_README_Build_Harness.md` | This file — rules, workflow, blockers |
| 01 | `openapi.yaml` | **The contract.** Single source of truth for every API. Build against this, not prose. |
| 02 | `02_Data_Model_Spec.md` | Migration-ready models: fields, types, constraints, relations, Django code |
| 03 | `03_Ticket_Breakdown.md` | Epics → tickets, each with owner, deps, and **acceptance criteria** |
| 04 | `04_Environment_And_Scaffold.md` | Repo layout, pinned deps, env vars, local/CI/deploy, secrets |
| 05 | `05_Seed_Data_Spec.md` | Fixture shapes, required external datasets, demo account |

## The one golden rule: contract-first

`openapi.yaml` is law. Backend implements it; frontend consumes it; neither invents fields.
If a change is needed, the **contract changes first**, then both sides follow. This is the single
thing that keeps two independently-running agents from producing code that won't integrate.

## Recommended agent workflow

Do **not** hand an agent "build the 8-week plan." Drive it ticket by ticket:

1. **Phase 0 (human + 1 agent, ~1 day):** scaffold both repos per file 04, commit the empty
   structure + `openapi.yaml` + CI. Nothing else.
2. **Backend agent** works the backend tickets in order; every PR must validate against `openapi.yaml`
   (contract test) and pass acceptance criteria from file 03.
3. **Frontend agent** works against a **mock server generated from `openapi.yaml`** (e.g. Prism)
   until the real endpoint is green, then switches to staging. It never waits idle for backend.
4. One ticket = one PR = one reviewable unit. Agents self-verify against the ticket's acceptance
   criteria before opening the PR.

## Repos & branching

- Two repos: `ent-backend`, `ent-frontend`.
- Trunk-based: short-lived `feat/<ticket-id>` branches → PR → `main`. `main` auto-deploys to staging.
- One PR per ticket. PR title = ticket ID + summary.

## Definition of Done (applies to EVERY ticket)

- [ ] Acceptance criteria in file 03 all met
- [ ] Backend: matches `openapi.yaml` (contract test green); unit tests for any business logic
- [ ] Frontend: responsive 360px→desktop; loading + empty + error states present
- [ ] Lint + tests green in CI
- [ ] No secrets committed; new env vars documented in file 04
- [ ] Demo-relevant change verified on a seeded account

## Coding conventions

- **Backend:** Django app-per-domain (file 02), DRF ViewSets/serializers, `ruff` lint, `pytest`.
  Business logic lives in `services.py`, never in views. Snake_case JSON via consistent serializer config.
- **Frontend:** TypeScript strict, feature folders (file 02 of frontend plan), TanStack Query for all
  server state, Zustand only for auth/UI, `eslint` + `prettier`. Generate a typed client from
  `openapi.yaml` (`openapi-typescript`) — do not hand-type response shapes.

## ⚠ Decisions you MUST lock before agents start (these are blockers)

1. **Auth method.** Harness assumes **Google OAuth only**. If phone/SMS is required for your audience,
   say so now — it adds a provider integration and ~3–5 days, and changes the auth schemas in `openapi.yaml`.
2. **ЕНТ grant formula.** File 02 defines a *configurable* additive model with a sample config. You must
   confirm the current-year subject set, max score, and any weighting, or the calculator will ship a guess.
3. **Hosting accounts.** Render/Railway + managed Postgres, Vercel/Netlify, a Google OAuth client — a human
   must provision these and supply secrets (file 04). Agents cannot.
4. **Design accent + logo.** Pick one primary color + app name now; otherwise every screen drifts.

Until 1–4 are answered, agents will fill them with guesses. Lock them first.
