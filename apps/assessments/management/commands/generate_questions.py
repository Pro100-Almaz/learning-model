"""Generate questions with the MAIQE graph and store them in the bank.

Usage::

    python manage.py generate_questions --topic calculus_integrals --count 5
    python manage.py generate_questions --topic calculus_integrals --count 5 --target-score 30

Runs the full generation graph (Architect -> Storyteller -> Critic ->
Publisher) once per question and persists each approved one as an
``assessments.Question`` with its ``AnswerOption``s. Dedup is automatic: a
re-rolled problem that already exists is skipped (see Question.content_hash),
so re-runs top the bank up rather than duplicating it.

Needs ``OPENAI_API_KEY`` in the environment (the Storyteller and Critic call
an LLM). The Architect and Publisher are pure Python; only the two middle
agents cost tokens. ``--target-score`` (0-40, the student's intended
profile-subject score) raises difficulty; omit it for the blueprint's default.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

import config
from apps.assessments.models import Question


class Command(BaseCommand):
    help = "Generate questions for a topic with the MAIQE graph and store them in the DB."

    def add_arguments(self, parser):
        parser.add_argument(
            "--topic",
            default="calculus_integrals",
            help="Blueprint name under blueprints/ (default: calculus_integrals).",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=1,
            help="How many questions to attempt to generate (default: 1).",
        )
        parser.add_argument(
            "--target-score",
            type=int,
            default=None,
            help="Intended profile-subject score 0-40; higher => harder. Omit for the blueprint default.",
        )
        parser.add_argument(
            "--language",
            choices=config.SUPPORTED_LANGUAGES,
            default=config.DEFAULT_LANGUAGE,
            help=f"Output language (default: {config.DEFAULT_LANGUAGE}).",
        )

    def handle(self, *args, **opts):
        # math_engine is light (jinja only); import it first so a bad
        # --target-score fails fast, before the heavy LangGraph / LLM stack.
        from agents_and_engine.math_engine import PROFILE_SUBJECT_MAX_SCORE

        topic = opts["topic"]
        count = opts["count"]
        language = opts["language"]
        profile = {}
        if opts["target_score"] is not None:
            score = opts["target_score"]
            if not (0 <= score <= PROFILE_SUBJECT_MAX_SCORE):
                raise CommandError(
                    f"Please enter a valid profile-subject score between 0 and "
                    f"{PROFILE_SUBJECT_MAX_SCORE} (got {score})."
                )
            profile = {"target_score": score}

        # Imported here, not at module top, so `manage.py` startup (and other
        # commands) don't pull in the LangGraph / LLM stack.
        from agents_and_engine.graph import generate_question

        created = skipped = failed = 0
        for i in range(1, count + 1):
            try:
                result = generate_question(topic, profile, language)
            except Exception as exc:  # one bad roll / API hiccup must not kill the batch
                failed += 1
                self.stderr.write(self.style.ERROR(f"[{i}/{count}] generation error: {exc}"))
                continue

            qid = result.get("question_id")
            if qid is None:
                # Critic never approved a draft -> graph broke out to fallback.
                failed += 1
                self.stderr.write(
                    self.style.WARNING(
                        f"[{i}/{count}] critic rejected the draft after "
                        f"{result.get('revision_count', '?')} rounds; nothing stored."
                    )
                )
                continue

            if result.get("was_duplicate"):
                skipped += 1
                self.stdout.write(
                    f"[{i}/{count}] duplicate problem -> reused Question #{qid}, nothing written."
                )
                continue

            created += 1
            self._report_question(i, count, qid)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done (topic={topic}): {created} created, "
                f"{skipped} duplicates skipped, {failed} failed."
            )
        )

    def _report_question(self, i: int, count: int, qid: int) -> None:
        """Print the stored question and its options so the run is auditable."""
        question = Question.objects.prefetch_related("options").get(pk=qid)
        self.stdout.write(
            self.style.SUCCESS(f"[{i}/{count}] stored Question #{qid} (difficulty {question.difficulty})")
        )
        self.stdout.write(f"    {question.text}")
        for opt in question.options.all():
            mark = "*" if opt.is_correct else " "
            tag = f"   <- {opt.misconception}" if opt.misconception else ""
            self.stdout.write(f"      [{mark}] {opt.text}{tag}")
