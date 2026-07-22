#!/usr/bin/env python
"""Seed the «Математика» curriculum from the blueprints, then bulk-generate questions.

For every ``blueprints/<topic>.json`` this script:

1. Ensures the subject **Математика** exists.
2. Parses the blueprint's ``curriculum_ref`` to derive a grade + module name and
   ensures the ``ClassGrade`` and ``Module`` exist.
3. Ensures a ``Tag`` (from the blueprint's ``tag`` block) and a ``Lesson`` titled
   after ``display_name`` exist, with the lesson linked to that tag — which is the
   hook ``assessments.services.publish_generated_question`` uses to attach
   generated questions to a lesson's micro test.
4. Fires one ``GenerationJob`` per (topic, difficulty) — ``--per-difficulty``
   questions at difficulty 1, 2 and 3 — running the
   Architect→Storyteller→Critic→Publisher pipeline **in-process** across a thread
   pool. No web server or Celery worker is required; only the LLM API keys the
   pipeline itself needs (e.g. ``OPENAI_API_KEY``).

Usage (run from anywhere)::

    python agents_and_engine/blueprints/seed_and_generate.py
    python agents_and_engine/blueprints/seed_and_generate.py --seed-only
    python agents_and_engine/blueprints/seed_and_generate.py --topics quadratic_equations trig_sin
    python agents_and_engine/blueprints/seed_and_generate.py --per-difficulty 4 --workers 8
    python agents_and_engine/blueprints/seed_and_generate.py --difficulties 2 3 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Django bootstrap — must happen before any apps.* / agents_and_engine import.
# --------------------------------------------------------------------------- #
BLUEPRINT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BLUEPRINT_DIR.parents[1]  # <root>/agents_and_engine/blueprints -> <root>
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "conf.settings")

import django  # noqa: E402

django.setup()

from django.db import connection, transaction  # noqa: E402
from django.utils.text import slugify  # noqa: E402

from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag  # noqa: E402
from apps.generation.models import GenerationJob  # noqa: E402
from apps.generation.tasks import run_generation_job  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed_and_generate")

SUBJECT_NAME = "Математика"
SUBJECT_SLUG = "math"

# The pipeline derives difficulty (1-3) from ``target_score`` (профильная
# математика, 0-40) via math_engine.DIFFICULTY_BY_TARGET: >=28 -> 3, >=18 -> 2,
# else 1. These representative scores land squarely in each band.
DIFFICULTY_TARGET_SCORE = {1: 10, 2: 22, 3: 34}


# --------------------------------------------------------------------------- #
# curriculum_ref parsing
# --------------------------------------------------------------------------- #
def parse_curriculum_ref(ref: str) -> tuple[int, str]:
    """Split ``curriculum_ref`` into (grade, module_title).

    Handles both observed shapes, e.g.::

        "KZ Grade 10 — Quadratic equations / Vieta's theorem" -> (10, "Quadratic ...")
        "KZ Grade 9-10 — Sequences and progressions"          -> (9,  "Sequences ...")
        "10 класс — Преобразование графиков"                   -> (10, "Преобразование ...")

    Grade = first integer in the left segment (so a "9-10" range files under 9).
    Module title = text after the em/en-dash separator; the whole string is used
    as a fallback if no separator is present.
    """
    parts = re.split(r"\s*[—–]\s*", ref, maxsplit=1)
    left = parts[0]
    module_title = parts[1].strip() if len(parts) > 1 else ref.strip()

    m = re.search(r"\d+", left)
    grade = int(m.group()) if m else 10  # default to grade 10 if unparseable
    return grade, module_title


def load_blueprint(topic: str) -> dict:
    return json.loads((BLUEPRINT_DIR / f"{topic}.json").read_text("utf-8"))


def discover_topics() -> list[str]:
    return sorted(p.stem for p in BLUEPRINT_DIR.glob("*.json"))


# --------------------------------------------------------------------------- #
# Content seeding (single-threaded — get_or_create is not race-safe under fan-out)
# --------------------------------------------------------------------------- #
def seed_content(topics: list[str]) -> dict[str, Lesson]:
    """Ensure Subject → ClassGrade → Module → Lesson (+ Tag) for each topic.

    Returns a ``{topic: Lesson}`` map. Idempotent: re-running only fills gaps.
    """
    subject, created = Subject.objects.get_or_create(
        slug=SUBJECT_SLUG, defaults={"name": SUBJECT_NAME}
    )
    log.info("Subject %-9s %s (id=%s)", "created" if created else "exists", subject.name, subject.pk)

    lessons: dict[str, Lesson] = {}
    # Order counters so repeated runs keep a stable, readable ordering.
    module_order: dict[int, int] = {}
    lesson_order: dict[int, int] = {}

    for topic in topics:
        bp = load_blueprint(topic)
        display_name = bp["display_name"]
        tag_block = bp["tag"]
        grade, module_title = parse_curriculum_ref(bp.get("curriculum_ref", ""))

        with transaction.atomic():
            class_grade, _ = ClassGrade.objects.get_or_create(grade=grade, subject=subject)

            module_slug = f"g{grade}-{slugify(module_title, allow_unicode=True)}"
            module, m_created = Module.objects.get_or_create(
                slug=module_slug,
                defaults={
                    "title": module_title,
                    "class_grade": class_grade,
                    "order": module_order.get(class_grade.pk, Module.objects.filter(class_grade=class_grade).count()),
                },
            )
            if m_created:
                module_order[class_grade.pk] = module.order + 1

            tag, _ = Tag.objects.get_or_create(
                slug=tag_block["slug"], defaults={"name": tag_block["name"]}
            )

            lesson, l_created = Lesson.objects.get_or_create(
                module=module,
                title=display_name,
                defaults={
                    "tag": tag,
                    "video_url": "",
                    "order": lesson_order.get(module.pk, Lesson.objects.filter(module=module).count()),
                },
            )
            if l_created:
                lesson_order[module.pk] = lesson.order + 1
            elif lesson.tag_id != tag.pk:
                # Backfill the tag link on a pre-existing lesson so generated
                # questions can resolve to it.
                lesson.tag = tag
                lesson.save(update_fields=["tag"])

        lessons[topic] = lesson
        log.info(
            "  %-24s grade=%-2s module=%-45s tag=%-24s lesson#%s%s",
            topic,
            grade,
            module_title[:45],
            tag_block["slug"],
            lesson.pk,
            " (new)" if l_created else "",
        )

    return lessons


# --------------------------------------------------------------------------- #
# Generation (threaded — one GenerationJob per (topic, difficulty))
# --------------------------------------------------------------------------- #
def _run_job(job_id: int) -> dict:
    """Run one job synchronously in this worker thread, then release the DB conn."""
    try:
        # Calling the bound task directly executes it in-process (no broker),
        # driving the full LangGraph pipeline and writing Question rows.
        run_generation_job(job_id)
    finally:
        # Each thread gets its own Django DB connection; close it so the pool
        # doesn't leak connections for the lifetime of the process.
        connection.close()

    job = GenerationJob.objects.get(pk=job_id)
    return {
        "job_id": job.pk,
        "topic": job.topic,
        "target_score": job.target_score,
        "status": job.status,
        "created": job.created_count,
        "skipped": job.skipped_count,
        "failed": job.failed_count,
        "error": job.error,
    }


def generate(
    topics: list[str],
    difficulties: list[int],
    per_difficulty: int,
    workers: int,
    dry_run: bool = False,
) -> None:
    """Create and run one job per (topic, difficulty) across a thread pool."""
    plan: list[tuple[str, int, int]] = [
        (topic, diff, DIFFICULTY_TARGET_SCORE[diff])
        for topic in topics
        for diff in difficulties
    ]
    log.info(
        "Generation plan: %d jobs (%d topics × %d difficulties × %d questions) — workers=%d",
        len(plan),
        len(topics),
        len(difficulties),
        per_difficulty,
        workers,
    )
    if dry_run:
        for topic, diff, ts in plan:
            log.info("  DRY-RUN would generate %d×d%d for %s (target_score=%d)", per_difficulty, diff, topic, ts)
        return

    # Create all job rows up front (cheap, single-threaded) so the pool only
    # runs the heavy pipeline work.
    job_ids: list[int] = []
    for topic, _diff, ts in plan:
        job = GenerationJob.objects.create(
            user=None,
            topic=topic,
            count=per_difficulty,
            target_score=ts,
        )
        job_ids.append(job.pk)

    totals = {"created": 0, "skipped": 0, "failed": 0}
    done = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_job, jid): jid for jid in job_ids}
        for fut in as_completed(futures):
            jid = futures[fut]
            try:
                r = fut.result()
            except Exception:  # pragma: no cover - defensive; task usually self-reports
                log.exception("job %s crashed", jid)
                continue
            with lock:
                done += 1
                for k in totals:
                    totals[k] += r[k]
            log.info(
                "[%d/%d] job#%s %-24s d(ts=%s) -> %-9s created=%d skipped=%d failed=%d%s",
                done,
                len(job_ids),
                r["job_id"],
                r["topic"],
                r["target_score"],
                r["status"],
                r["created"],
                r["skipped"],
                r["failed"],
                f"  ERROR: {r['error'][:120]}" if r["error"] else "",
            )

    log.info(
        "DONE — %d jobs: created=%d skipped(dedup)=%d failed=%d",
        len(job_ids),
        totals["created"],
        totals["skipped"],
        totals["failed"],
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topics", nargs="*", help="Blueprint stems to process (default: all).")
    parser.add_argument("--per-difficulty", type=int, default=4, help="Questions per difficulty (default: 4).")
    parser.add_argument(
        "--difficulties", type=int, nargs="*", default=[1, 2, 3], choices=[1, 2, 3],
        help="Which difficulties to generate (default: 1 2 3).",
    )
    parser.add_argument("--workers", type=int, default=8, help="Thread pool size (default: 8).")
    parser.add_argument("--seed-only", action="store_true", help="Seed content, skip generation.")
    parser.add_argument("--generate-only", action="store_true", help="Skip seeding (assumes content exists).")
    parser.add_argument("--dry-run", action="store_true", help="Print the generation plan without running it.")
    args = parser.parse_args(argv)

    all_topics = discover_topics()
    topics = args.topics or all_topics
    unknown = [t for t in topics if not (BLUEPRINT_DIR / f"{t}.json").exists()]
    if unknown:
        parser.error(f"unknown topic(s): {', '.join(unknown)}. Available: {', '.join(all_topics)}")

    if not args.generate_only:
        log.info("=== Seeding content for %d topic(s) ===", len(topics))
        seed_content(topics)

    if not args.seed_only:
        log.info("=== Generating questions ===")
        generate(
            topics=topics,
            difficulties=sorted(set(args.difficulties)),
            per_difficulty=args.per_difficulty,
            workers=args.workers,
            dry_run=args.dry_run,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
