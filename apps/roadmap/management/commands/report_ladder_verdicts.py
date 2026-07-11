"""Report the chapter-ladder verdict distribution (07 §"Admin & analytics").

Prints, per module and per topic, how completed placements split across
``gap`` / ``solid`` / ``mastered`` — the calibration feedback loop. A topic that
is ~100% gap is either genuinely hard or has a mistuned medium rung; ~100%
mastered means the rungs are too easy.

Usage::

    python manage.py report_ladder_verdicts
    python manage.py report_ladder_verdicts --module trigonometry

Read-only; safe to run anytime.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.analytics.services import compute_ladder_verdict_distribution
from apps.content.models import Module


class Command(BaseCommand):
    help = "Report per-module/per-topic chapter-ladder verdict distribution."

    def add_arguments(self, parser):
        parser.add_argument("--module", default=None, help="Limit to one module (by slug).")

    def handle(self, *args, **opts):
        module = None
        if opts["module"]:
            module = Module.objects.filter(slug=opts["module"]).first()
            if module is None:
                self.stderr.write(self.style.ERROR(f"No module with slug '{opts['module']}'."))
                return

        distribution = compute_ladder_verdict_distribution(module=module)
        if not distribution:
            self.stdout.write("No completed ladder sessions yet.")
            return

        for mod in distribution:
            self.stdout.write(
                self.style.MIGRATE_HEADING(f"\n{mod['module_title']}  (module_id={mod['module_id']})")
            )
            for topic in mod["topics"]:
                c = topic["counts"]
                f = topic["fractions"]
                self.stdout.write(
                    f"  {topic['tag_slug'] or topic['tag_id']:<32} "
                    f"n={topic['total']:<4} "
                    f"gap={c['gap']}({f['gap']:.0%})  "
                    f"solid={c['solid']}({f['solid']:.0%})  "
                    f"mastered={c['mastered']}({f['mastered']:.0%})"
                )
