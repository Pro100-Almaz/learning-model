.PHONY: help up upd down build rebuild shell migrate makemigrations test test-cov logs logs-worker logs-beat superuser seed clean prune ps docs docs-serve docs-build docs-deploy bump update-deps add-dep remove-dep deploy deploy-status

# Default target - show help
help:
	@echo "Django Starter Template - Docker Compose Commands"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Service Management:"
	@echo "  up              Start all services (db, redis, backend, worker, beat) — attached"
	@echo "  upd             Start all services in detached mode (background)"
	@echo "  down            Stop all services"
	@echo "  build           Build Docker image"
	@echo "  rebuild         Rebuild image and restart services"
	@echo "  ps              Show running containers"
	@echo ""
	@echo "Django Commands:"
	@echo "  shell           Open Django shell"
	@echo "  migrate         Run database migrations"
	@echo "  makemigrations  Create new migrations"
	@echo "  superuser       Create a superuser"
	@echo "  seed            Seed database (20 users + superuser)"
	@echo ""
	@echo "Testing & Debugging:"
	@echo "  test            Run all tests"
	@echo "  test-cov        Run tests with coverage report"
	@echo "  logs            View backend logs (follow mode)"
	@echo "  logs-worker     View Celery worker logs"
	@echo "  logs-beat       View Celery beat logs"
	@echo ""
	@echo "Documentation:"
	@echo "  docs            Serve documentation locally (alias for docs-serve)"
	@echo "  docs-serve      Serve documentation with live reload"
	@echo "  docs-build      Build documentation site"
	@echo "  docs-deploy     Deploy documentation to GitHub Pages"
	@echo ""
	@echo "Cloud Deployment (Cloud Run):"
	@echo "  deploy          Build, push, migrate and roll out the current commit"
	@echo "  deploy-status   Show which image each Cloud Run workload is running"
	@echo ""
	@echo "Version Management:"
	@echo "  bump            Bump patch version in pyproject.toml and urls.py"
	@echo ""
	@echo "Dependency Management:"
	@echo "  update-deps     Update all dependencies to latest allowed versions"
	@echo "  add-dep         Add a new dependency (usage: make add-dep pkg=package_name)"
	@echo "  remove-dep      Remove a dependency (usage: make remove-dep pkg=package_name)"
	@echo ""
	@echo "Maintenance:"
	@echo "  clean           Stop services and remove volumes"
	@echo "  prune           Remove unused Docker resources"

# Service Management
up:
	docker compose up

upd:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

rebuild:
	docker compose up --build

ps:
	docker compose ps

# Django Commands
shell:
	docker compose exec backend python manage.py shell_plus

migrate:
	docker compose exec backend python manage.py migrate

makemigrations:
	docker compose exec backend python manage.py makemigrations

superuser:
	docker compose exec backend python manage.py createsuperuser

seed:
	docker compose exec backend python manage.py seed --users 20 --superuser --clean

# Question generation (MAIQE) ----------------------------------------------
# Usage:
#   make generate                          # 1 quadratic, default difficulty (CLI, blocking)
#   make generate topic=calculus_integrals count=3 target=120
generate:
	docker compose exec backend python manage.py generate_questions \
		--topic $(or $(topic),quadratic_equations) \
		--count $(or $(count),1) \
		$(if $(target),--target-score $(target),)

# Testing & Debugging
test:
	docker compose exec backend pytest

test-cov:
	docker compose exec backend pytest --cov

test-html:
	docker compose exec backend pytest --cov --cov-report=html
	@echo "Coverage report generated in htmlcov/index.html"

logs:
	docker compose logs -f backend

logs-worker:
	docker compose logs -f worker

logs-beat:
	docker compose logs -f beat

logs-all:
	docker compose logs -f

# Documentation
docs: docs-serve

docs-serve:
	docker compose exec backend mkdocs serve -a 0.0.0.0:8001

docs-build:
	docker compose exec backend mkdocs build

docs-deploy:
	@echo "Note: Deployment requires local git credentials"
	uv run --with mkdocs-material mkdocs gh-deploy

# Cloud Deployment (Cloud Run) ---------------------------------------------
# The image is a frozen snapshot of the tree at build time (see `COPY . .` in
# the Dockerfile), so nothing you edit locally reaches Cloud Run until it is
# rebuilt and pushed here.
CR_REPO := europe-west1-docker.pkg.dev/qadam-learning-platform/qadam-containers/backend
CR_REGION := europe-west1
CR_SERVICE := qadam-api
CR_JOBS := qadam-migrate qadam-create-admin
# Deferred (`=`, not `:=`) so git only runs for the deploy targets, not on every
# `make help`.
CR_SHA = $(shell git rev-parse --short HEAD)

deploy:
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "Working tree is dirty. Commit first, or the $(CR_SHA) tag will not"; \
		echo "match the code inside the image."; \
		git status --short; \
		exit 1; \
	fi
	@echo "==> Building $(CR_SHA) for linux/amd64"
	docker build --platform linux/amd64 -t $(CR_REPO):$(CR_SHA) -t $(CR_REPO):latest .
	docker push $(CR_REPO):$(CR_SHA)
	docker push $(CR_REPO):latest
	@echo "==> Pointing jobs at $(CR_SHA)"
	@for job in $(CR_JOBS); do \
		gcloud run jobs update $$job --region=$(CR_REGION) --image=$(CR_REPO):$(CR_SHA) || exit 1; \
	done
	@echo "==> Migrating before the new code serves traffic"
	gcloud run jobs execute qadam-migrate --region=$(CR_REGION) --wait
	@echo "==> Rolling out $(CR_SERVICE)"
	gcloud run deploy $(CR_SERVICE) --region=$(CR_REGION) --image=$(CR_REPO):$(CR_SHA)
	@echo "==> Deployed $(CR_SHA)"

# Jobs resolve their tag per execution while the service pins a digest at
# revision creation, so the two can silently drift onto different code.
deploy-status:
	@echo "service $(CR_SERVICE):"
	@gcloud run services describe $(CR_SERVICE) --region=$(CR_REGION) \
		--format="value(status.latestReadyRevisionName, spec.template.spec.containers[0].image)"
	@for job in $(CR_JOBS); do \
		echo "job $$job:"; \
		gcloud run jobs describe $$job --region=$(CR_REGION) \
			--format="value(spec.template.spec.template.spec.containers[0].image)"; \
	done

# Version Management
bump:
	uv run python scripts/bump.py

# Dependency Management
update-deps:
	uv lock --upgrade
	@echo "Lock file updated. Run 'make rebuild' to apply changes."
add-dep:
	@if [ -z "$(pkg)" ]; then echo "Usage: make add-dep pkg=package_name"; exit 1; fi
	uv add $(pkg)
	@echo "Dependency added. Run 'make rebuild' to apply changes."

remove-dep:
	@if [ -z "$(pkg)" ]; then echo "Usage: make remove-dep pkg=package_name"; exit 1; fi
	uv remove $(pkg)
	@echo "Dependency removed. Run 'make rebuild' to apply changes."

# Maintenance
clean:
	docker compose down -v

prune:
	docker system prune -f
	docker volume prune -f
