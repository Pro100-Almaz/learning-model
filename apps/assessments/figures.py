"""Reading ModeFigure rows on the question-serving path.

Two problems live here, and neither belongs in the serializer.

The first is N+1. ``QuestionPublicSerializer`` runs once per question, and a mock
test is forty of them; a per-object lookup would be forty extra round trips on
the hottest read path in the product. The whole table is at most one row per
blueprint mode -- 231 today, and the great majority will never have a figure --
so it is cached whole rather than queried per row.

The second is trust. This SVG is inlined into the DOM by the frontend, which
makes it a script-injection surface. Figures are authored by staff, so this is a
guard against accident and against a compromised staff account rather than
against an anonymous attacker, but the cost of the guard is thirty lines and the
cost of missing it is stored XSS on every student who opens the question.
"""

from __future__ import annotations

import re
from typing import Any

from django.core.cache import cache

# Versioned so a change to the cached SHAPE (not just its contents) cannot be
# served stale from a Redis that survived the deploy.
CACHE_KEY = "assessments:mode_figures:v1"
# Bounds staleness if a row is written by something that bypasses the signals --
# a raw SQL fixture load, a `.update()` on a queryset. Figures change roughly
# never, so an hour costs nothing and saves a query per request.
CACHE_SECONDS = 3600

# Attribute-name and URL-scheme level, not tag level: a blocklist of tags is the
# classic way to get this wrong, since `<svg onload=...>` needs no forbidden tag
# at all. Anything that can execute is refused outright rather than stripped --
# silently altering an author's markup hides the mistake from them.
_FORBIDDEN = (
    (re.compile(r"<\s*script", re.I), "a <script> element"),
    (re.compile(r"<\s*foreignObject", re.I), "a <foreignObject> element"),
    (re.compile(r"<\s*(iframe|object|embed|link|meta)\b", re.I), "an embedded document"),
    (re.compile(r"\bon[a-z]+\s*=", re.I), "an inline event handler (on*=)"),
    (re.compile(r"(javascript|vbscript|data)\s*:", re.I), "an executable URL scheme"),
    # External refs would leak the reader's IP to a third party and break the
    # offline/print case; every figure must be self-contained.
    (re.compile(r"\b(href|xlink:href|src)\s*=\s*[\"']?\s*(https?:)?//", re.I),
     "a reference to an external host"),
)

_SVG_ROOT = re.compile(r"<\s*svg\b", re.I)


def validate_svg(svg: str) -> None:
    """Raise ValueError unless `svg` is a self-contained, inert SVG document."""
    if not svg or not svg.strip():
        raise ValueError("figure markup is empty")
    if not _SVG_ROOT.search(svg):
        raise ValueError("figure markup has no <svg> element")
    for pattern, what in _FORBIDDEN:
        if pattern.search(svg):
            raise ValueError(f"figure markup contains {what}, which is not allowed")


def _load() -> dict[str, dict[str, Any]]:
    """Every figure, keyed "topic/mode". Cached as one value, not one per key."""
    from apps.assessments.models import ModeFigure

    return {
        f"{row.topic}/{row.mode}": {"svg": row.svg, "alt": row.alt or {}}
        for row in ModeFigure.objects.all().only("topic", "mode", "svg", "alt")
    }


def table() -> dict[str, dict[str, Any]]:
    cached = cache.get(CACHE_KEY)
    if cached is None:
        cached = _load()
        cache.set(CACHE_KEY, cached, CACHE_SECONDS)
    return cached


def invalidate() -> None:
    """Drop the cache. Wired to ModeFigure post_save/post_delete in models.py.

    Deleting the shared key rather than clearing a process-local dict is what
    makes this correct across workers: every gunicorn process and every Celery
    worker misses on its next read.
    """
    cache.delete(CACHE_KEY)


def figure_for_mode(
    topic: str | None,
    mode: str | None,
    language: str | None = None,
) -> dict[str, Any] | None:
    """The figure for a blueprint mode, or None.

    Takes the keys directly rather than a solution, because preview renders items
    that have never been saved -- there is no Question row to read them off.
    """
    if not topic or not mode:
        return None

    entry = table().get(f"{topic}/{mode}")
    if entry is None:
        return None

    alt = entry["alt"].get(language) if language else None
    return {"svg": entry["svg"], "alt": alt}


def figure_for(solution: Any, language: str | None = None) -> dict[str, Any] | None:
    """The figure for a question, or None.

    `solution` is ``Question.solution``. Hand-authored questions and MAIQE ones
    carry no topic/mode and so can never match, which is why this is safe to call
    unconditionally for every question in the bank.
    """
    if not isinstance(solution, dict):
        return None
    return figure_for_mode(solution.get("topic"), solution.get("mode"), language)
