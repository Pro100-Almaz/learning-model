"""Discover blueprint files and map their curriculum metadata to content rows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag


@dataclass(frozen=True)
class Blueprint:
    topic: str
    display_name: str
    curriculum_ref: str
    tag_slug: str
    tag_name: str
    path: Path

    @property
    def grade_and_module(self) -> tuple[int, str]:
        parts = re.split(r"\s*[—–]\s*", self.curriculum_ref, maxsplit=1)
        match = re.search(r"\d+", parts[0])
        grade = int(match.group()) if match else 10
        title = parts[1].strip() if len(parts) > 1 else self.curriculum_ref.strip()
        return grade, title

    @property
    def module_slug(self) -> str:
        grade, title = self.grade_and_module
        return f"g{grade}-{slugify(title, allow_unicode=True)}"


def resolve_folder(folder: str) -> Path:
    root = Path(settings.BASE_DIR).resolve()
    relative = Path(folder)
    if relative.is_absolute():
        raise CommandError("folder must be relative to the app root")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise CommandError("folder must stay inside the app root")
    if not path.is_dir():
        raise CommandError(f"folder does not exist: {folder}")
    return path


def read_blueprint(path: Path) -> Blueprint | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("topic") != path.stem:
        return None
    tag = data.get("tag")
    if (
        not isinstance(tag, dict)
        or not isinstance(data.get("answer"), dict)
        or not isinstance(data.get("parameters"), dict)
        or not isinstance(data.get("constraints_template"), str)
    ):
        return None
    display_name = data.get("display_name")
    curriculum_ref = data.get("curriculum_ref")
    tag_slug = tag.get("slug")
    tag_name = tag.get("name")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (display_name, curriculum_ref, tag_slug, tag_name)
    ):
        return None
    assert isinstance(display_name, str)
    assert isinstance(curriculum_ref, str)
    assert isinstance(tag_slug, str)
    assert isinstance(tag_name, str)
    return Blueprint(path.stem, display_name, curriculum_ref, tag_slug, tag_name, path)


def declared_tag_slug(path: Path) -> str | None:
    """Read only the topic/tag identity, including from other blueprint engines."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("topic") != path.stem:
        return None
    tag = data.get("tag")
    if not isinstance(tag, dict) or not isinstance(tag.get("slug"), str):
        return None
    return tag["slug"]


def discover_blueprints(folder: Path) -> tuple[list[Blueprint], list[str]]:
    found: list[Blueprint] = []
    skipped: list[str] = []
    for path in sorted(folder.glob("*.json")):
        blueprint = read_blueprint(path)
        if blueprint is None:
            skipped.append(path.name)
        else:
            found.append(blueprint)
    return found, skipped


def seed_blueprint(blueprint: Blueprint) -> dict[str, bool]:
    """Create only missing curriculum rows, and identify a reused lesson by topic."""
    grade, module_title = blueprint.grade_and_module
    with transaction.atomic():
        tag, tag_new = Tag.objects.get_or_create(
            slug=blueprint.tag_slug, defaults={"name": blueprint.tag_name}
        )
        lesson = Lesson.objects.filter(topic=blueprint.topic).first()
        if lesson is None:
            tagged = Lesson.objects.filter(tag=tag)
            if tagged.count() == 1:
                lesson = tagged.first()
        if lesson is not None:
            if lesson.tag_id not in (None, tag.pk):
                raise CommandError(f"lesson for {blueprint.topic!r} has a different tag")
            updates = []
            if not lesson.topic:
                lesson.topic = blueprint.topic
                updates.append("topic")
            if lesson.tag_id is None:
                lesson.tag = tag
                updates.append("tag")
            if updates:
                lesson.save(update_fields=updates)
            return {
                "subject": False,
                "grade": False,
                "module": False,
                "tag": tag_new,
                "lesson": False,
            }

        subject, subject_new = Subject.objects.get_or_create(
            slug="math", defaults={"name": "Математика"}
        )
        class_grade, grade_new = ClassGrade.objects.get_or_create(
            grade=grade, subject=subject
        )
        module, module_new = Module.objects.get_or_create(
            slug=blueprint.module_slug,
            defaults={
                "title": module_title,
                "class_grade": class_grade,
                "order": Module.objects.filter(class_grade=class_grade).count(),
            },
        )
        lesson = Lesson.objects.filter(
            module=module, title=blueprint.display_name
        ).first()
        lesson_new = lesson is None
        if lesson is None:
            Lesson.objects.create(
                module=module,
                tag=tag,
                topic=blueprint.topic,
                title=blueprint.display_name,
                video_url="",
                order=Lesson.objects.filter(module=module).count(),
            )
        elif not lesson.topic or lesson.tag_id is None:
            updates = []
            if not lesson.topic:
                lesson.topic = blueprint.topic
                updates.append("topic")
            if lesson.tag_id is None:
                lesson.tag = tag
                updates.append("tag")
            lesson.save(update_fields=updates)
    return {
        "subject": subject_new,
        "grade": grade_new,
        "module": module_new,
        "tag": tag_new,
        "lesson": lesson_new,
    }
