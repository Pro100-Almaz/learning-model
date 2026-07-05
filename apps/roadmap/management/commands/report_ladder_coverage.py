    """Report question-bank coverage for the chapter ladder (07 §"Bank coverage").

The ladder needs questions at >=1 difficulty per topic, ideally all three rungs
(easy=1, medium=2, hard=3). This read-only report shows, per module and per
topic, how many questions exist at each difficulty and flags the topics that
can't form at least a 2-rung ladder — so we know whether to design for 3 rungs
everywhere or accept graceful degradation in some chapters.

Usage::

    python manage.py report_ladder_coverage
    python manage.py report_ladder_coverage --module trigonometry

Nothing is written; safe to run anytime (intended for CI as a content-health
check, cf. the global spec's ``report_bank_coverage``).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.assessments.models import Question
from apps.content.models import Module
from apps.roadmap.ladder import topics_for_module

# The ladder's difficulty rungs, easy -> hard.
RUNGS = (1, 2, 3)


class Command(BaseCommand):
    help = "Report per-module/per-topic question counts by difficulty for the chapter ladder."

    def add_arguments(self, parser):
        parser.add_argument(
            "--module",
            default=None,
            help="Limit the report to one module (by slug).",
        )

    def handle(self, *args, **opts):
        modules = Module.objects.all().order_by("order")
        if opts["module"]:
            modules = modules.filter(slug=opts["module"])
            if not modules.exists():
                self.stderr.write(self.style.ERROR(f"No module with slug '{opts['module']}'."))
                return

        # Roll-up counters for the closing summary.
        total_topics = 0
        no_questions = 0
        one_rung = 0            # degrades to a single pass/fail gate
        two_rung = 0
        full_ladder = 0
        missing_hard = 0        # d1 & d2 present, d3 absent -> "mastered" unreachable

        for module in modules:
            topics = topics_for_module(module)
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{module.title}  (slug={module.slug})"))
            if not topics:
                self.stdout.write("  (no tagged lessons — nothing to ladder)")
                continue

            for tag in topics:
                total_topics += 1
                counts = {
                    d: Question.objects.filter(tags=tag, difficulty=d).count() for d in RUNGS
                }
                rungs_present = sum(1 for d in RUNGS if counts[d] > 0)
                bar = "  ".join(f"d{d}={counts[d]}" for d in RUNGS)

                if rungs_present == 0:
                    no_questions += 1
                    note, style = "NO QUESTIONS", self.style.ERROR
                elif rungs_present == 1:
                    one_rung += 1
                    note, style = "1-rung only -> pass/fail gate", self.style.WARNING
                elif rungs_present == 2:
                    two_rung += 1
                    if counts[3] == 0:
                        missing_hard += 1
                        note, style = "2-rung (no hard -> 'mastered' unreachable)", self.style.WARNING
                    else:
                        note, style = "2-rung", self.style.WARNING
                else:
                    full_ladder += 1
                    note, style = "full 3-rung", self.style.SUCCESS

                self.stdout.write(f"  {tag.slug:<32} {bar:<24} {style(note)}")

        self.stdout.write(self.style.MIGRATE_HEADING("\nSummary"))
        self.stdout.write(f"  topics total:        {total_topics}")
        self.stdout.write(f"  full 3-rung:         {full_ladder}")
        self.stdout.write(f"  2-rung:              {two_rung}  (of which no-hard: {missing_hard})")
        self.stdout.write(self.style.WARNING(f"  1-rung (gate only):  {one_rung}"))
        self.stdout.write(self.style.ERROR(f"  no questions:        {no_questions}"))
        cannot_ladder = one_rung + no_questions
        self.stdout.write(
            self.style.WARNING(
                f"  cannot form a >=2-rung ladder: {cannot_ladder} topic(s)"
            )
        )
