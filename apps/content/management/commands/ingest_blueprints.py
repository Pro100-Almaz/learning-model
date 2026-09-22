"""Seed blueprint curricula and one generated question per difficulty."""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import config
from apps.assessments.models import Question
from apps.content.blueprint_curriculum import (
    discover_blueprints,
    resolve_folder,
    seed_blueprint,
)
from apps.generation.models import HiddenBlueprintTopic

TARGET_SCORES = {1: 10, 2: 22, 3: 34}
MAX_GENERATION_ATTEMPTS = 3


def generate_one(topic: str, target_score: int) -> dict:
    """Import the LLM graph only when a question actually needs generating."""
    from agents_and_engine.graph import generate_question

    return generate_question(
        topic, {"target_score": target_score}, config.DEFAULT_LANGUAGE
    )


def existing_question(topic_tag_slug: str, difficulty: int) -> Question | None:
    return (
        Question.objects.filter(
            content_hash__isnull=False,
            tags__slug=topic_tag_slug,
            difficulty=difficulty,
        )
        .order_by("pk")
        .first()
    )


class Command(BaseCommand):
    help = "Seed blueprint curricula and one question at each difficulty (1-3)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "folder",
            help="folder relative to app root, e.g. agents_and_engine/qadam_blueprints",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="show topics without writing"
        )
        parser.add_argument(
            "--seed-only",
            action="store_true",
            help="seed curriculum without generating questions",
        )

    def handle(self, *args, **options) -> None:
        folder = resolve_folder(options["folder"])
        blueprints, skipped = discover_blueprints(folder)
        for name in skipped:
            self.stdout.write(f"skipped non-blueprint file: {name}")
        if not blueprints:
            self.stdout.write(
                self.style.SUCCESS("no blueprints found; nothing to ingest")
            )
            return

        if not options["dry_run"]:
            uses_redis = (
                settings.CACHES["default"]["BACKEND"] == "django_redis.cache.RedisCache"
            )
            if uses_redis and not os.environ.get("REDIS_URL"):
                raise CommandError("REDIS_URL is missing from the environment or .env")
        totals = dict.fromkeys(("subject", "grade", "module", "tag", "lesson"), 0)
        generated = already_present = 0
        failures: list[str] = []
        for blueprint in blueprints:
            if options["dry_run"]:
                if options["seed_only"]:
                    self.stdout.write(f"would ingest {blueprint.topic}")
                    continue
                missing = [
                    level
                    for level in TARGET_SCORES
                    if existing_question(blueprint.tag_slug, level) is None
                ]
                self.stdout.write(
                    f"would ingest {blueprint.topic}; missing difficulties: {missing}"
                )
                continue
            created = seed_blueprint(blueprint)
            HiddenBlueprintTopic.objects.filter(topic=blueprint.topic).delete()
            for name, was_created in created.items():
                totals[name] += int(was_created)
            self.stdout.write(f"ingested {blueprint.topic}")
            if options["seed_only"]:
                continue
            for difficulty, target_score in TARGET_SCORES.items():
                question = existing_question(blueprint.tag_slug, difficulty)
                if question is not None:
                    already_present += 1
                    self.stdout.write(
                        f"  difficulty {difficulty}: Question #{question.pk} exists"
                    )
                    continue

                last_error = "no question was published"
                for _attempt in range(MAX_GENERATION_ATTEMPTS):
                    try:
                        result = generate_one(blueprint.topic, target_score)
                    except Exception as error:
                        last_error = str(error)
                    else:
                        if result.get("question_id") is None:
                            last_error = "the graph did not publish a question"
                        else:
                            last_error = "published question has the wrong difficulty"
                    question = existing_question(blueprint.tag_slug, difficulty)
                    if question is not None:
                        break
                if question is None:
                    failures.append(
                        f"{blueprint.topic} difficulty {difficulty}: {last_error}"
                    )
                    self.stderr.write(
                        self.style.ERROR(f"  difficulty {difficulty}: {last_error}")
                    )
                else:
                    generated += 1
                    self.stdout.write(
                        f"  difficulty {difficulty}: generated Question #{question.pk}"
                    )
        if options["dry_run"]:
            self.stdout.write(f"dry run: {len(blueprints)} blueprint(s), nothing written")
        else:
            counts = ", ".join(f"{name}={count}" for name, count in totals.items())
            self.stdout.write(
                self.style.SUCCESS(
                    f"done: {len(blueprints)} blueprint(s); created {counts}; "
                    f"questions generated={generated}, existing={already_present}, "
                    f"failed={len(failures)}"
                )
            )
            if failures:
                raise CommandError(
                    "could not generate every missing difficulty: " + "; ".join(failures)
                )
