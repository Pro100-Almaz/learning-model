"""Turning a generation request into concrete (topic, difficulty, seed) work.

Extracted from ``ubt_views`` so the Celery tasks plan a batch exactly the way
the API does. The seed scheme is the part that must not fork: a batch is
reproducible only because every item's seed is derived from one base number,
and a task that derived them differently would produce items nobody could
reproduce from the seed the caller was handed.

These helpers raise DRF exceptions (``NotFound``, ``ValidationError``) because
that is what the views need, and their messages -- the loader's "did you mean"
suggestion, the exact build_i18n command to run -- are the most useful thing in
either a response or a worker log. A task caller sees them as ordinary
exceptions carrying a good message.
"""

from __future__ import annotations

import random

from rest_framework.exceptions import NotFound, ValidationError

from ubt_question_engine import i18n
from ubt_question_engine.loader import BlueprintError, list_topics, topic_meta
from ubt_question_engine.publisher import publishable_languages

SEED_SPACE = 2**31


def resolve_topics(topic: str | None) -> list[str]:
    if not topic:
        return list_topics()
    try:
        topic_meta(topic)
    except BlueprintError as error:
        # A misspelt stem is a 404, not a 400: the client named a resource that
        # does not exist. The loader's difflib suggestion rides along in the
        # message, which is the single most useful thing in the response.
        raise NotFound({"detail": str(error), "code": "unknown_topic"}) from error
    return [topic]


def resolve_languages(requested: list[str] | None) -> list[str]:
    if requested:
        # Checked up front rather than per item. Without this, asking for a
        # language whose cache is empty returns 200 and a list of N identical
        # "no translation" failures -- technically accurate, useless to read, and
        # it buries a one-line configuration problem in a batch report.
        for language in requested:
            gaps = i18n.missing(language)
            if gaps:
                raise ValidationError(
                    {
                        "detail": f"{language!r} is missing {len(gaps)} of "
                                  f"{len(i18n.translatable())} translations, e.g. "
                                  f"{next(iter(gaps))!r}. Run: "
                                  f"python -m scripts.build_i18n --language {language}",
                        "code": "incomplete_translations",
                    }
                )
        return list(requested)
    available = list(publishable_languages())
    if not available:
        raise ValidationError(
            {
                "detail": "no language has complete translations; run "
                          "`python -m scripts.build_i18n --all`",
                "code": "no_translations",
            }
        )
    return available


def difficulties_for(topic: str, data: dict) -> list[int]:
    meta = topic_meta(topic)
    if data.get("all_difficulties"):
        return meta["supported_difficulties"]
    if data.get("difficulty") is not None:
        difficulty = data["difficulty"]
        if difficulty not in meta["supported_difficulties"]:
            raise ValidationError(
                {
                    "detail": f"topic {topic!r} does not support difficulty "
                              f"{difficulty}; supported: "
                              f"{meta['supported_difficulties']}",
                    "code": "unsupported_difficulty",
                }
            )
        return [difficulty]
    return [meta["default_difficulty"]]


def build_plan(data: dict) -> tuple[list[tuple[str, int, int]], int]:
    """Expand the request into concrete (topic, difficulty, seed) triples.

    Seeds are derived from one base seed rather than drawn per item, so the whole
    batch is reproducible from the single number echoed in the response.
    """
    base_seed = data.get("seed")
    if base_seed is None:
        base_seed = random.randrange(SEED_SPACE)

    plan: list[tuple[str, int, int]] = []
    for topic in resolve_topics(data.get("topic")):
        for difficulty in difficulties_for(topic, data):
            for index in range(data["count"]):
                plan.append((topic, difficulty, base_seed + index * 1_000 + difficulty))
    return plan, base_seed
