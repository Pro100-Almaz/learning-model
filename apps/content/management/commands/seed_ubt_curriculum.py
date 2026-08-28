"""Build the UBT chapter/lesson tree so generated questions have somewhere to land.

Why this command has to exist
-----------------------------
``publish_generated_question`` links a question to a Lesson by matching the
question's Tag to ``Lesson.tag`` (apps/assessments/services.py). No Lesson with
that tag means ``lesson=None``, no micro Test, and a question that no student
can ever reach -- it sits in the bank invisible to /attempts/ and to the
roadmap. The sibling ``seed_curriculum`` command seeds the older MAIQE topics
and shares exactly zero tag slugs with the UBT engine, so without this command
every UBT question published is orphaned.

Structure comes from ``ubt_question_engine/curriculum.json``; tag slugs come
from the blueprints themselves. The JSON never repeats a slug, so a
blueprint retagged on disk cannot silently disagree with the curriculum.

Three languages, one row
------------------------
Titles are written straight into modeltranslation's ``title_kk`` / ``_ru`` /
``_en`` columns rather than through the ``title`` descriptor, which would write
whichever language happened to be active and leave the other two empty.

Idempotent: everything upserts on its natural key (Subject.slug, Module.slug,
Tag.slug, and Lesson by ``(module, tag)``), so re-running after editing a title
updates in place and never duplicates. Lessons already carrying a video keep it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag
from ubt_question_engine.loader import list_topics, load_blueprint

ENGINE_DIR = Path(__file__).resolve().parents[4] / "ubt_question_engine"
BLUEPRINTS_DIR = ENGINE_DIR / "ubt_blueprints"
# Deliberately NOT inside ubt_blueprints/: the loader treats every .json under
# that tree as a topic blueprint, so a data file living there is discovered as a
# 59th topic and fails validation.
CURRICULUM_PATH = ENGINE_DIR / "curriculum.json"
LANGUAGES = ("kk", "ru", "en")


def _titled(field: str, values: dict[str, str]) -> dict[str, str]:
    """{'title_kk': ..., 'title_ru': ..., 'title_en': ...} for modeltranslation."""
    return {f"{field}_{language}": values[language] for language in LANGUAGES}


class Command(BaseCommand):
    help = "Create the UBT chapters, lessons and tags that generated questions attach to."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="validate the curriculum against the blueprints and print the tree, write nothing",
        )

    # -- validation ----------------------------------------------------------

    def _load_curriculum(self) -> dict[str, Any]:
        if not CURRICULUM_PATH.exists():
            raise CommandError(f"curriculum file not found: {CURRICULUM_PATH}")
        with CURRICULUM_PATH.open(encoding="utf-8") as handle:
            return json.load(handle)

    def _check(self, curriculum: dict[str, Any]) -> None:
        """Refuse to run on a curriculum that has drifted from the blueprints.

        A missing topic is the failure that matters: it produces a lesson-less
        tag, which produces orphaned questions -- and nothing downstream errors,
        so it would only surface as "that topic has no practice questions" weeks
        later. Cheaper to fail here.
        """
        on_disk = set(list_topics())
        listed: list[str] = []
        problems: list[str] = []

        for chapter in curriculum["chapters"]:
            folder = BLUEPRINTS_DIR / chapter["folder"]
            if not folder.is_dir():
                problems.append(f"chapter {chapter['slug']}: no such folder {chapter['folder']}/")
            listed.extend(chapter["topics"])

        duplicates = {t for t in listed if listed.count(t) > 1}
        if duplicates:
            problems.append(f"topics listed in more than one chapter: {sorted(duplicates)}")

        missing = on_disk - set(listed)
        if missing:
            problems.append(
                f"{len(missing)} blueprint(s) not in any chapter -- their questions would be "
                f"orphaned: {sorted(missing)}"
            )
        unknown = set(listed) - on_disk
        if unknown:
            problems.append(f"chapters name topics with no blueprint: {sorted(unknown)}")

        for topic in sorted(set(listed) & on_disk):
            if topic not in curriculum["lessons"]:
                problems.append(f"no lesson title for {topic}")
                continue
            title = curriculum["lessons"][topic]
            missing_langs = [lang for lang in LANGUAGES if not title.get(lang)]
            if missing_langs:
                problems.append(f"lesson {topic} is missing {missing_langs} titles")

        for topic in sorted(set(listed) & on_disk):
            slug = (load_blueprint(topic).get("tag") or {}).get("slug")
            if not slug:
                problems.append(f"blueprint {topic} declares no tag slug")
            elif slug not in curriculum["tags"]:
                problems.append(f"tag {slug!r} (from {topic}) has no name in curriculum.json")

        if problems:
            raise CommandError(
                "curriculum.json and the blueprints disagree:\n  - " + "\n  - ".join(problems)
            )

    # -- writing -------------------------------------------------------------

    def handle(self, *args, **options) -> None:
        curriculum = self._load_curriculum()
        self._check(curriculum)

        if options["dry_run"]:
            self._print_tree(curriculum)
            self.stdout.write(self.style.SUCCESS("\nvalidated; nothing written (--dry-run)"))
            return

        with transaction.atomic():
            counts = self._write(curriculum)

        self.stdout.write(
            self.style.SUCCESS(
                "\n{chapters} chapter(s), {lessons} lesson(s), {tags} tag(s) in "
                "{languages}".format(languages="/".join(LANGUAGES), **counts)
            )
        )
        self.stdout.write(
            "Publish into it with: python manage.py generate_ubt_questions --all"
        )

    def _write(self, curriculum: dict[str, Any]) -> dict[str, int]:
        subject_spec = curriculum["subject"]
        subject, _ = Subject.objects.update_or_create(
            slug=subject_spec["slug"], defaults=_titled("name", subject_spec["name"])
        )
        grade, _ = ClassGrade.objects.get_or_create(
            grade=curriculum["grade"], subject=subject
        )

        tags: dict[str, Tag] = {}
        for slug, name in curriculum["tags"].items():
            # `name` is unique on Tag, so an existing row with the same name and a
            # different slug would collide. Match on slug and let it raise -- a
            # duplicated tag name is a data problem worth stopping for.
            tag, _ = Tag.objects.update_or_create(slug=slug, defaults=_titled("name", name))
            tags[slug] = tag

        lesson_count = 0
        for order, chapter in enumerate(curriculum["chapters"], start=1):
            module, _ = Module.objects.update_or_create(
                slug=chapter["slug"],
                defaults={
                    "class_grade": grade,
                    "order": order,
                    **_titled("title", chapter["title"]),
                },
            )
            self.stdout.write(f"{order:2}. {chapter['title']['kk']}")

            for lesson_order, topic in enumerate(chapter["topics"], start=1):
                slug = load_blueprint(topic)["tag"]["slug"]
                tag = tags[slug]
                # Keyed on `topic`, not on (module, tag) and not on title. Tag is
                # too coarse -- eight planimetry topics share one tag, so keying on
                # it would collapse them into a single lesson. Title is unstable:
                # a reworded title would spawn a second lesson and split the
                # topic's questions across both.
                lesson, created = Lesson.objects.update_or_create(
                    topic=topic,
                    defaults={
                        "module": module,
                        "tag": tag,
                        "order": lesson_order,
                        **_titled("title", curriculum["lessons"][topic]),
                    },
                )
                if created and not lesson.video_url:
                    # URLField is non-null; a lesson with no video is still a valid
                    # practice target, and an editor fills this in later.
                    lesson.video_url = ""
                    lesson.save(update_fields=["video_url"])
                lesson_count += 1
                self.stdout.write(
                    f"     {lesson_order}. {curriculum['lessons'][topic]['kk']}  [{slug}]"
                )

        return {
            "chapters": len(curriculum["chapters"]),
            "lessons": lesson_count,
            "tags": len(tags),
        }

    def _print_tree(self, curriculum: dict[str, Any]) -> None:
        for order, chapter in enumerate(curriculum["chapters"], start=1):
            self.stdout.write(f"{order:2}. {chapter['title']['kk']}  ({chapter['folder']}/)")
            for lesson_order, topic in enumerate(chapter["topics"], start=1):
                slug = load_blueprint(topic)["tag"]["slug"]
                self.stdout.write(
                    f"     {lesson_order}. {curriculum['lessons'][topic]['kk']}  [{slug}]"
                )
