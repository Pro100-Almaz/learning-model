# Environment & Scaffold Spec

Pinned, reproducible setup. An agent scaffolds both repos from this in Phase 0; a human supplies the
secrets marked 🔑.

## Repos

Two repos, trunk-based. `main` auto-deploys to staging.

### `ent-backend` layout
```
ent-backend/
├── config/            # settings/{base,dev,prod}.py, urls.py, wsgi.py
├── accounts/  content/  assessments/  analytics/  careers/  gamification/  common/
├── manage.py
├── pyproject.toml     # deps + ruff config
├── .github/workflows/ci.yml
└── render.yaml        # or railway.json
```

### `ent-frontend` layout
```
ent-frontend/
├── src/  (app/ components/ features/ lib/ stores/  — see frontend plan)
├── index.html  vite.config.ts  tailwind.config.ts  tsconfig.json
├── package.json
├── .github/workflows/ci.yml
└── public/manifest.webmanifest
```

## Pinned dependencies

**Backend (`pyproject.toml`)** — pin exact versions at scaffold time; baseline:
```
django==5.*            djangorestframework==3.15.*
djangorestframework-simplejwt   django-allauth   dj-rest-auth
drf-spectacular        django-import-export   django-environ
psycopg[binary]        whitenoise   pillow
# dev: pytest  pytest-django  ruff  openapi-spec-validator
```

**Frontend (`package.json`)** — baseline:
```
react ^18  react-dom ^18  typescript ^5  vite ^5
react-router-dom ^6  @tanstack/react-query ^5  zustand ^4  axios ^1
tailwindcss ^3  + shadcn/ui (radix)  recharts ^2  framer-motion ^11
react-hook-form ^7  zod ^3  vite-plugin-pwa ^0.20
# dev: eslint  prettier  openapi-typescript  @hookform/resolvers
```

## Environment variables

**Backend `.env`**
```
DJANGO_SECRET_KEY=          🔑
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=api.staging.example.com,localhost
DATABASE_URL=postgres://...  🔑 (managed Postgres)
GOOGLE_OAUTH_CLIENT_ID=     🔑
GOOGLE_OAUTH_CLIENT_SECRET= 🔑
CORS_ALLOWED_ORIGINS=https://app.staging.example.com,http://localhost:5173
```
**Frontend `.env`**
```
VITE_API_BASE_URL=https://api.staging.example.com/api/v1
VITE_GOOGLE_CLIENT_ID=      🔑 (same client id)
```

## Local setup

**Backend**
```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python manage.py migrate
python manage.py loaddata seed/*.json   # see 05_Seed_Data_Spec
python manage.py createsuperuser
python manage.py runserver
```
**Frontend**
```
npm install
npm run dev        # proxies VITE_API_BASE_URL
# contract mock (frontend ahead of backend):
npx @stoplight/prism-cli mock ../ent-backend/openapi.yaml
```

## CI (both repos)

- On PR: install → lint (`ruff` / `eslint`) → test (`pytest` / `vitest`) → **backend also runs a contract test** that the generated schema matches `openapi.yaml`.
- On merge to `main`: deploy to staging.

## Deployment

- **Backend:** Render/Railway web service + managed PostgreSQL. Build runs `migrate` + `collectstatic`; WhiteNoise serves static; backups enabled in prod.
- **Frontend:** Vercel/Netlify; env vars set in dashboard; SPA rewrite to `index.html`.

## Secrets checklist (human-provided 🔑)

- [ ] Google OAuth client (Cloud Console) — client id + secret, redirect URIs for staging + localhost
- [ ] Managed Postgres URL (staging + prod)
- [ ] Django secret key (per environment)
- [ ] Hosting accounts: Render/Railway + Vercel/Netlify
- [ ] (If SMS auth is chosen later) KZ SMS provider credentials — not in MVP scope
