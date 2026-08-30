"""Remove lessons and questions whose blueprint no longer exists.

Why this command has to exist
-----------------------------
``seed_ubt_curriculum`` upserts -- it creates and updates, and it never deletes.
That is the right default: a curriculum edit must not be able to wipe a chapter
by accident. But it means retiring a topic leaves the old rows behind, still
attached to a Module, still carrying questions, still served to students. When
the stereometry chapter was rewritten from counting trivia to volume and surface
area, the seven old lessons and every question under them stayed live in the
database with nothing on disk to regenerate them.

So the rule this command enforces is: a Lesson whose ``topic`` names no blueprint
teaches nothing, and a Question whose ``solution["topic"]`` names no blueprint
can never be regenerated or reviewed. Both are garbage, and both are invisible
garbage -- no test fails, no page errors, the questions simply keep appearing.

What it will not do without being told twice
--------------------------------------------
``AttemptAnswer.question`` cascades. Deleting a question a student has actually
answered erases that answer, and with it the score and the analytics built on
it. Those questions are reported and skipped unless ``--include-answered`` is
passed, because losing attempt history is worse than serving a stale question
for another week.

Reports by default; writes only with ``--delete``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q

from apps.assessments.models import Question, Test
from apps.content.models import Lesson
from ubt_question_engine.loader import list_topics


class Command(BaseCommand):
    help = "Delete lessons and generated questions whose blueprint no longer exists."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--delete",
            action="store_true",
            help="actually delete; without it the command only reports",
        )
        parser.add_argument(
            "--include-answered",
            action="store_true",
            help=(
                "also delete questions students have answered. This CASCADES to "
                "AttemptAnswer and permanently loses that attempt history."
            ),
        )
        parser.add_argument(
            "--topic",
            action="append",
            default=[],
            metavar="TOPIC",
            help=(
                "restrict to these topics; repeatable. Anything not named is left "
                "alone even if its blueprint is missing. Use this whenever the "
                "working tree may be mid-edit."
            ),
        )

    def handle(self, *args, **options) -> None:
        known = set(list_topics())
        self.stdout.write(f"{len(known)} blueprint(s) on disk")

        # An allowlist makes the result independent of what happens to be on disk
        # right now. Without it, a topic whose file is momentarily absent -- a
        # rename in flight, a half-finished `git mv` -- reads as retired, and its
        # lessons and questions are deleted. Naming the topics turns "whatever
        # disk says is gone" into "these, or nothing".
        allowed = set(options["topic"])
        if allowed:
            surprises = sorted(allowed & known)
            if surprises:
                raise CommandError(
                    "refusing to prune topic(s) whose blueprint still exists on "
                    f"disk: {surprises}. They are live, not orphaned."
                )
            self.stdout.write(f"restricted to {len(allowed)} named topic(s)")

        # --- what is stale ---------------------------------------------------
        stale_lessons = Lesson.objects.exclude(topic="").exclude(topic__in=known)

        # `solution__topic__isnull=False` narrows to generated questions; MAIQE
        # and hand-authored ones carry no topic and must never be touched here.
        generated = Question.objects.filter(solution__topic__isnull=False)
        stale_questions = generated.exclude(solution__topic__in=list(known))

        if allowed:
            stale_lessons = stale_lessons.filter(topic__in=allowed)
            stale_questions = stale_questions.filter(solution__topic__in=list(allowed))

        # Answered ones are the questions whose deletion costs real data.
        answered = stale_questions.filter(attemptanswer__isnull=False).distinct()
        answered_ids = set(answered.values_list("pk", flat=True))
        deletable = stale_questions.exclude(pk__in=answered_ids)

        lesson_count = stale_lessons.count()
        question_count = stale_questions.count()

        if not lesson_count and not question_count:
            self.stdout.write(self.style.SUCCESS("nothing stale; database matches disk"))
            return

        for lesson in stale_lessons.order_by("topic"):
            self.stdout.write(f"  lesson  {lesson.topic}  ({lesson.title})")
        for topic in sorted(
            {q.solution.get("topic") for q in stale_questions.only("solution")}
        ):
            rows = stale_questions.filter(solution__topic=topic).count()
            self.stdout.write(f"  topic   {topic}  -- {rows} question row(s)")

        self.stdout.write(
            f"\n{lesson_count} stale lesson(s), {question_count} stale question row(s), "
            f"of which {len(answered_ids)} have been answered"
        )

        if not options["delete"]:
            self.stdout.write(self.style.WARNING("nothing written; pass --delete to apply"))
            return

        # --- delete ----------------------------------------------------------
        with transaction.atomic():
            if options["include_answered"]:
                doomed = stale_questions
                kept = 0
            else:
                doomed = deletable
                kept = len(answered_ids)

            removed_questions = doomed.count()
            # Which tests those questions belonged to, read BEFORE the delete
            # cascades the TestQuestion rows away and the link is unrecoverable.
            touched_tests = list(
                Test.objects.filter(questions__in=doomed)
                .values_list("pk", flat=True)
                .distinct()
            )
            doomed.delete()

            # A micro test is a wrapper around its questions; once they are gone
            # it is an empty shell that would still list in the lesson. Scoped to
            # the tests we just emptied -- an unrelated empty micro test is
            # somebody else's business. Mock and diagnostic tests are
            # hand-composed and are left alone entirely.
            empty = (
                Test.objects.filter(pk__in=touched_tests, type="micro")
                .annotate(remaining=Count("questions"))
                .filter(remaining=0)
            )
            removed_tests = empty.count()
            empty.delete()

            # Last, because a lesson still holding questions is a lesson we have
            # not finished cleaning. Any that kept an answered question stays.
            still_used = Lesson.objects.filter(
                Q(questions__isnull=False) | Q(tests__isnull=False)
            ).values_list("pk", flat=True)
            doomed_lessons = stale_lessons.exclude(pk__in=list(still_used))
            removed_lessons = doomed_lessons.count()
            doomed_lessons.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"\ndeleted {removed_questions} question(s), {removed_tests} empty micro "
                f"test(s), {removed_lessons} lesson(s)"
            )
        )
        if kept:
            self.stdout.write(
                self.style.WARNING(
                    f"kept {kept} answered question(s) and any lesson still holding one. "
                    f"Re-run with --include-answered to remove them and their attempt history."
                )
            )
