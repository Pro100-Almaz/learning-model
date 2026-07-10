"""Seed the ҰБТ profile-math curriculum: chapters (Modules), topics (Tags), lessons.

Creates the 8-chapter / 26-lesson structure the Chapter Ladder needs. Each lesson
carries a ``Lesson.tag`` — the field ``roadmap.ladder.topics_for_module`` reads to
discover a chapter's topics, so without this the ladder finds nothing to assess.

Tag ``name``/``slug`` are read from each topic's blueprint
(``agents_and_engine/blueprints/<topic>.json``) so tags stay a single source of
truth and match the slugs the question-generation pipeline already wrote (existing
tags are reused by slug, never duplicated).

Idempotent: re-running upserts by natural key (Module.slug, Tag.slug,
(Lesson.module, Lesson.tag)). Run with::

    python manage.py seed_curriculum
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.content.models import Lesson, Module, Tag

# Blueprints live at <repo>/agents_and_engine/blueprints; this file is at
# <repo>/apps/content/management/commands/seed_curriculum.py -> parents[4] == repo.
BLUEPRINTS_DIR = Path(__file__).resolve().parents[4] / "agents_and_engine" / "blueprints"

SUBJECT = "profile_math"

# (chapter_slug, chapter_title, [(blueprint_topic, lesson_title), ...]) in order.
CHAPTERS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "algebra-basics",
        "Алгебраические основы: прогрессии и уравнения",
        [
            ("arithmetic_progression", "Арифметическая прогрессия: n-й член и сумма"),
            ("quadratic_equations", "Квадратные уравнения и теорема Виета"),
        ],
    ),
    (
        "function-properties",
        "Свойства и исследование функции",
        [
            ("symmetry_periodicity", "Периодичность и чётность/нечётность функции"),
            ("domain_extremums", "Область определения и точки экстремума"),
            ("quadratic_analysis", "Исследование квадратичной функции (парабола)"),
            ("function_analysis", "Исследование графика функции (7 пунктов)"),
        ],
    ),
    (
        "graph-transformations",
        "Преобразование графиков и специальные функции",
        [
            ("shifts_xy", "Сдвиги графиков (вправо/влево, вверх/вниз)"),
            ("deformations_xy", "Сжатие и растяжение графиков"),
            ("fractional_linear", "Дробно-линейная функция и асимптоты"),
            ("inverse_fractional", "Обратная функция для дробных выражений"),
        ],
    ),
    (
        "trig-functions",
        "Тригонометрические функции",
        [
            ("trig_sin", "Свойства и график y = sin x"),
            ("trig_cos", "Свойства и график y = cos x"),
            ("trig_tg_ctg", "Свойства функций тангенса и котангенса"),
        ],
    ),
    (
        "inverse-trig-functions",
        "Обратные тригонометрические функции",
        [
            ("inv_trig_base", "Аркфункции: свойства и табличные значения"),
            ("inv_trig_neg", "Аркфункции от отрицательных аргументов"),
            ("inv_trig_arithmetic", "Арифметические операции и композиции аркфункций"),
            ("inv_trig_complex", "Связь различных тригонометрических функций"),
        ],
    ),
    (
        "trig-equations-basic",
        "Простейшие тригонометрические уравнения и методы",
        [
            ("trig_eq_sin", "Уравнения вида sin x = a"),
            ("trig_eq_cos", "Уравнения вида cos x = a"),
            ("trig_eq_homog", "Однородные тригонометрические уравнения"),
            ("trig_eq_aux_angle", "Метод вспомогательного угла"),
        ],
    ),
    (
        "trig-equations-advanced",
        "Методы понижения и системы",
        [
            ("trig_eq_deg_red", "Метод понижения порядка"),
            ("trig_eq_deg_sum", "Понижение степени и преобразование в произведение"),
            ("trig_sys_add", "Системы уравнений: метод сложения"),
            ("trig_sys_sub", "Системы уравнений: метод подстановки"),
        ],
    ),
    (
        "calculus-integrals",
        "Начала анализа: интеграл",
        [
            ("calculus_integrals", "Площадь криволинейной трапеции (определённый интеграл)"),
        ],
    ),
]


class Command(BaseCommand):
    help = "Seed ҰБТ curriculum: 8 chapters (Modules), 26 topics (Tags), 26 lessons."

    def _blueprint_tag(self, topic: str) -> tuple[str, str]:
        """Return (slug, name) for a topic's tag, read from its blueprint JSON."""
        path = BLUEPRINTS_DIR / f"{topic}.json"
        if not path.exists():
            raise CommandError(f"Blueprint not found for topic {topic!r}: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        tag = data.get("tag") or {}
        slug, name = tag.get("slug"), tag.get("name")
        if not slug or not name:
            raise CommandError(f"Blueprint {topic!r} has no tag name/slug: {tag!r}")
        return slug, name

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        n_mod = n_tag_new = n_lesson = 0

        for order, (slug, title, lessons) in enumerate(CHAPTERS, start=1):
            module, created = Module.objects.update_or_create(
                slug=slug,
                defaults={"title": title, "order": order, "subject": SUBJECT},
            )
            n_mod += 1
            self.stdout.write(f"[{order}] {title}  ({'created' if created else 'updated'})")

            for lesson_order, (topic, lesson_title) in enumerate(lessons, start=1):
                tag_slug, tag_name = self._blueprint_tag(topic)
                tag, tag_created = Tag.objects.get_or_create(
                    slug=tag_slug, defaults={"name": tag_name}
                )
                n_tag_new += int(tag_created)

                Lesson.objects.update_or_create(
                    module=module,
                    tag=tag,
                    defaults={
                        "title": lesson_title,
                        "order": lesson_order,
                        "video_url": "",
                        "video_provider": "youtube",
                    },
                )
                n_lesson += 1
                flag = "new-tag" if tag_created else "reused-tag"
                self.stdout.write(f"      {lesson_order}. {lesson_title}  [{tag_slug} · {flag}]")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Modules: {n_mod}, Lessons: {n_lesson}, "
                f"Tags created: {n_tag_new} (existing tags reused)."
            )
        )
