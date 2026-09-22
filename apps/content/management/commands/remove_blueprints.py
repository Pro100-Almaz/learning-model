"""Remove generated content and unused curriculum selected by blueprint files."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.assessments.models import Question, Test, TestAttempt
from apps.content.blueprint_curriculum import (
    declared_tag_slug,
    discover_blueprints,
    resolve_folder,
)
from apps.content.management.commands.seed_curriculum import CHAPTERS
from apps.content.models import Lesson, Module, Tag


class Command(BaseCommand):
    help = "Remove unattempted generated questions and unused curriculum for a folder."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "folder",
            help="folder relative to the app root, such as agents_and_engine/blueprints",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="show candidates without writing"
        )

    def handle(self, *args, **options) -> None:
        folder = resolve_folder(options["folder"])
        blueprints, skipped = discover_blueprints(folder)
        for name in skipped:
            self.stdout.write(f"skipped non-blueprint file: {name}")
        if not blueprints:
            self.stdout.write(
                self.style.SUCCESS("no blueprints found; nothing to remove")
            )
            return

        slugs = [blueprint.tag_slug for blueprint in blueprints]
        if len(slugs) != len(set(slugs)):
            raise CommandError(
                "multiple selected blueprints share a tag; removal is ambiguous"
            )

        # Old MAIQE questions have no topic in solution, so their tag is the
        # only reliable bridge back to a blueprint. A shared tag is unsafe.
        roots = (
            settings.BASE_DIR / "agents_and_engine",
            settings.BASE_DIR / "ubt_question_engine" / "ubt_blueprints",
        )
        for root in roots:
            for path in root.rglob("*.json"):
                if path.parent == folder:
                    continue
                other_slug = declared_tag_slug(path)
                if other_slug in slugs:
                    raise CommandError(
                        f"tag {other_slug!r} is also used by {path}; refusing removal"
                    )

        legacy_modules = {
            topic: chapter_slug
            for chapter_slug, _title, lessons in CHAPTERS
            for topic, _lesson_title in lessons
        }
        lesson_filter = Q(pk__in=[])
        for blueprint in blueprints:
            module_slugs = [blueprint.module_slug]
            if blueprint.topic in legacy_modules:
                module_slugs.append(legacy_modules[blueprint.topic])
            lesson_filter |= Q(topic=blueprint.topic) | Q(
                topic="",
                tag__slug=blueprint.tag_slug,
                module__slug__in=module_slugs,
            )

        with transaction.atomic():
            lessons = Lesson.objects.filter(lesson_filter).distinct()
            lesson_ids = list(lessons.values_list("pk", flat=True))
            module_ids = set(lessons.values_list("module_id", flat=True))
            module_ids.update(
                Module.objects.filter(
                    slug__in=[bp.module_slug for bp in blueprints]
                ).values_list("pk", flat=True)
            )
            tag_ids = list(
                Tag.objects.filter(slug__in=slugs).values_list("pk", flat=True)
            )
            questions = Question.objects.filter(
                content_hash__isnull=False, tags__pk__in=tag_ids
            ).distinct()
            candidate_ids = set(questions.values_list("pk", flat=True))

            # Keep questions that have been answered, served in a test attempt,
            # or frozen into an attempt's question_ids snapshot.
            protected = set(
                questions.filter(attemptanswer__isnull=False).values_list("pk", flat=True)
            )
            protected.update(
                questions.filter(tests__attempts__isnull=False).values_list(
                    "pk", flat=True
                )
            )
            if candidate_ids:
                for ids in TestAttempt.objects.exclude(question_ids=[]).values_list(
                    "question_ids", flat=True
                ):
                    if isinstance(ids, list):
                        protected.update(candidate_ids.intersection(ids))
            doomed_ids = candidate_ids - protected

            self.stdout.write(
                f"found {len(blueprints)} blueprint(s), {len(candidate_ids)} generated "
                f"question(s), {len(lesson_ids)} matching lesson(s); "
                f"keeping {len(protected)} question(s) used by students"
            )
            if options["dry_run"]:
                self.stdout.write(
                    f"dry run: would remove up to {len(doomed_ids)} question(s) "
                    "and then unused curriculum; nothing written"
                )
                return

            removed_questions = len(doomed_ids)
            Question.objects.filter(pk__in=doomed_ids).delete()

            # Only micro tests owned by matching lessons are eligible. An
            # attempted or roadmap-linked test remains even when empty.
            removed_tests = 0
            for test in Test.objects.filter(lesson_id__in=lesson_ids, type="micro"):
                if (
                    not test.questions.exists()
                    and not test.attempts.exists()
                    and not test.roadmap_items.exists()
                ):
                    test.delete()
                    removed_tests += 1

            removed_lessons = 0
            for lesson in Lesson.objects.filter(pk__in=lesson_ids):
                if (
                    not lesson.questions.exists()
                    and not lesson.tests.exists()
                    and not lesson.roadmap_items.exists()
                    and not lesson.next_lessons.exists()
                ):
                    lesson.delete()
                    removed_lessons += 1

            removed_tags = 0
            for tag in Tag.objects.filter(pk__in=tag_ids):
                if (
                    not tag.questions.exists()
                    and not tag.lessons.exists()
                    and not tag.student_mastery.exists()
                    and not tag.roadmap_items.exists()
                ):
                    tag.delete()
                    removed_tags += 1

            removed_modules = 0
            for module in Module.objects.filter(pk__in=module_ids):
                if not module.lessons.exists() and not module.ladder_sessions.exists():
                    module.delete()
                    removed_modules += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"removed questions={removed_questions}, micro_tests={removed_tests}, "
                f"lessons={removed_lessons}, tags={removed_tags}, "
                f"modules={removed_modules}"
            )
        )
