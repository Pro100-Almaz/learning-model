"""Fill the question bank from the deterministic UBT blueprint engine.

    python manage.py generate_ubt_questions --topic ubt_roots_expressions --count 5
    python manage.py generate_ubt_questions --all --count 2 --languages en
    python manage.py generate_ubt_questions --all --count 10 --dry-run

Every item is generated once and published in every requested language, so the
Kazakh, Russian and English rows of an item are the same question by
construction. Seeds are printed: a bad item can always be reproduced with
`python -m ubt_question_engine.preview <topic> --seed <n>`.
"""

from __future__ import annotations

import random

from django.core.management.base import BaseCommand, CommandError

from ubt_question_engine.generator import generate_question
from ubt_question_engine.loader import list_topics, topic_meta
from ubt_question_engine.localize import ENGINE_LANGUAGES, localize
from ubt_question_engine.params import GenerationError
from ubt_question_engine.publisher import PublishError, publish, publishable_languages


class Command(BaseCommand):
    help = "Generate and publish UBT questions from the blueprint engine."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--topic", help="blueprint stem; omit with --all")
        parser.add_argument("--all", action="store_true", help="every topic")
        parser.add_argument("--count", type=int, default=1, help="items per topic")
        parser.add_argument(
            "--difficulty", type=int, choices=(1, 2, 3),
            help="default: each topic's own default_difficulty",
        )
        parser.add_argument(
            "--all-difficulties", action="store_true",
            help="--count items at every supported difficulty",
        )
        parser.add_argument(
            "--languages", nargs="+", choices=ENGINE_LANGUAGES,
            help="default: every language whose translations are complete",
        )
        parser.add_argument("--seed", type=int, help="base seed; printed if omitted")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="generate and localize, write nothing",
        )

    def handle(self, *args, **options) -> None:
        if not options["topic"] and not options["all"]:
            raise CommandError("give --topic or --all")

        languages = tuple(options["languages"] or publishable_languages())
        if not languages:
            raise CommandError(
                "no language has complete translations. Run: "
                "python -m scripts.build_i18n --all"
            )

        topics = [options["topic"]] if options["topic"] else list_topics()
        base_seed = options["seed"] if options["seed"] is not None else random.randrange(2**31)

        self.stdout.write(f"languages: {', '.join(languages)}")
        self.stdout.write(f"base seed: {base_seed}")

        published = duplicates = failed = 0

        for topic in topics:
            try:
                meta = topic_meta(topic)
            except Exception as error:
                failed += 1
                self.stderr.write(f"{topic}: {error}")
                continue

            if options["all_difficulties"]:
                levels = meta["supported_difficulties"]
            elif options["difficulty"] is not None:
                levels = [options["difficulty"]]
            else:
                levels = [meta["default_difficulty"]]

            for difficulty in levels:
                for index in range(options["count"]):
                    seed = base_seed + index * 1_000 + difficulty
                    try:
                        base = generate_question(topic, difficulty=difficulty, seed=seed)
                        states = {lang: localize(base, lang) for lang in languages}
                    except (GenerationError, LookupError) as error:
                        failed += 1
                        self.stderr.write(f"{topic} d{difficulty} seed {seed}: {error}")
                        continue

                    if options["dry_run"]:
                        published += 1
                        self.stdout.write(
                            f"  would publish {topic}/{base['mode']} d{difficulty} "
                            f"seed {seed} -> {len(languages)} row(s)"
                        )
                        continue

                    try:
                        results = publish(states)
                    except PublishError as error:
                        failed += 1
                        self.stderr.write(f"{topic} d{difficulty} seed {seed}: {error}")
                        continue

                    if all(r["was_duplicate"] for r in results.values()):
                        duplicates += 1
                    else:
                        published += 1
                    ids = ", ".join(f"{k}#{v['question_id']}" for k, v in results.items())
                    self.stdout.write(f"  {topic}/{base['mode']} d{difficulty} seed {seed}: {ids}")

        verb = "would publish" if options["dry_run"] else "published"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {published} item(s), {duplicates} duplicate(s), {failed} failure(s)"
            )
        )
