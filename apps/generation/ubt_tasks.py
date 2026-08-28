"""Celery tasks that fill the UBT bank off the request path.

Why these are separate from ``tasks.run_generation_job``
--------------------------------------------------------
That task drives MAIQE: a LangGraph pipeline with LLM calls, minutes per
question, per-node progress streamed to an SSE endpoint. The UBT engine is
nothing like it -- pure CPU, no network, milliseconds per item, and
deterministic. It needs no GenerationStep trail because there is nothing to
watch: an item either generates or raises, and the seed reproduces it exactly.

What it does need is to stop blocking. Publishing every topic synchronously
through the management command or the API is one long request; a real bank
wants many items per topic, generated on demand, and the work is embarrassingly
parallel because topics share no state.

Hence two tasks. ``generate_ubt_topic`` does one topic and is the unit of
parallelism -- a worker per topic, as many as the pool allows.
``generate_ubt_batch`` fans those out and returns immediately with the base
seed, so the caller can reproduce anything the batch produced.

Determinism across the fan-out
------------------------------
The base seed is drawn ONCE, in the batch task, and passed down. Letting each
topic task draw its own would make the batch unreproducible from the single
number the caller was given -- the property the whole seed scheme exists for.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from celery import shared_task
from django.db.models import Count

from apps.assessments.models import Question
from apps.generation.ubt_planning import (
    SEED_SPACE,
    build_plan,
    resolve_languages,
    resolve_topics,
)
from ubt_question_engine.generator import generate_question
from ubt_question_engine.loader import BlueprintError
from ubt_question_engine.localize import localize
from ubt_question_engine.params import GenerationError
from ubt_question_engine.publisher import PublishError, publish

logger = logging.getLogger("apps.generation")


@shared_task(bind=True, name="generation.ubt_topic")
def generate_ubt_topic(
    self,
    topic: str,
    *,
    count: int = 1,
    difficulty: int | None = None,
    all_difficulties: bool = False,
    languages: list[str] | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate and publish `count` items of one topic, in every language.

    One item is generated once and dressed in each language, so an item's rows
    are the same mathematics by construction. A duplicate is not a failure --
    re-rolling a seed already in the bank is ordinary, and dedup on
    content_hash is exactly what should happen.

    A bad roll is caught per item. One topic's broken blueprint must not lose
    the items that already published in the same task.
    """
    resolved_languages = resolve_languages(languages)
    plan, base_seed = build_plan(
        {
            "topic": topic,
            "count": count,
            "difficulty": difficulty,
            "all_difficulties": all_difficulties,
            "seed": seed,
        }
    )

    published = duplicates = 0
    failures: list[str] = []

    for plan_topic, plan_difficulty, item_seed in plan:
        try:
            base = generate_question(
                plan_topic, difficulty=plan_difficulty, seed=item_seed
            )
            results = publish(
                {language: localize(base, language) for language in resolved_languages}
            )
        except (GenerationError, BlueprintError, PublishError, LookupError) as error:
            failures.append(f"{plan_topic} d{plan_difficulty} seed {item_seed}: {error}")
            logger.warning(
                "generate_ubt_topic: %s d%s seed %s failed: %s",
                plan_topic,
                plan_difficulty,
                item_seed,
                error,
            )
            continue

        # Every language row of an item shares one content_hash, so the item is
        # a duplicate if any row was: they were all written or none were.
        if any(row.get("was_duplicate") for row in results.values()):
            duplicates += 1
        else:
            published += 1

    summary = {
        "topic": topic,
        "requested": len(plan),
        "published": published,
        "duplicates": duplicates,
        "failed": len(failures),
        "seed": base_seed,
        "languages": resolved_languages,
        "failures": failures[:20],
    }
    logger.info(
        "generate_ubt_topic %s: %s published, %s duplicate, %s failed (seed %s)",
        topic,
        published,
        duplicates,
        len(failures),
        base_seed,
    )
    return summary


@shared_task(name="generation.ubt_batch")
def generate_ubt_batch(
    topic: str | None = None,
    *,
    count: int = 1,
    difficulty: int | None = None,
    all_difficulties: bool = False,
    languages: list[str] | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Fan one task out per topic. `topic=None` means every topic.

    Returns as soon as the children are queued -- it does not wait for them.
    The point is the fan-out: 65 topics on a pool of N workers is 65/N wall
    clock, not 65 sequential publishes.

    Languages are resolved HERE, once, and passed down. Resolving them in each
    child would run the same coverage check 65 times and, worse, could hand
    different children different language sets if build_i18n finished midway.
    """
    resolved_languages = resolve_languages(languages)
    topics = resolve_topics(topic)
    base_seed = seed if seed is not None else random.randrange(SEED_SPACE)

    task_ids = []
    for name in topics:
        async_result = generate_ubt_topic.delay(
            name,
            count=count,
            difficulty=difficulty,
            all_difficulties=all_difficulties,
            languages=resolved_languages,
            seed=base_seed,
        )
        task_ids.append(async_result.id)

    logger.info(
        "generate_ubt_batch: queued %s topic task(s), seed %s", len(topics), base_seed
    )
    return {
        "queued": len(topics),
        "topics": topics,
        "seed": base_seed,
        "languages": resolved_languages,
        "task_ids": task_ids,
    }


@shared_task(name="generation.ubt_top_up")
def top_up_ubt_bank(
    *,
    target: int = 20,
    languages: list[str] | None = None,
) -> dict[str, Any]:
    """Queue only the per-topic shortfalls needed to reach the target."""
    resolved_languages = resolve_languages(languages)
    all_topics = resolve_topics(None)
    depths = {
        row["solution__topic"]: row["depth"]
        for row in (
            Question.objects.filter(solution__topic__in=all_topics)
            .values("solution__topic")
            .annotate(depth=Count("solution__content_hash", distinct=True))
        )
    }
    base_seed = random.randrange(SEED_SPACE)

    queued_topics: list[str] = []
    task_ids: list[str] = []
    for topic in all_topics:
        shortfall = target - depths.get(topic, 0)
        if shortfall <= 0:
            continue
        async_result = generate_ubt_topic.delay(
            topic,
            count=shortfall,
            languages=resolved_languages,
            seed=base_seed,
        )
        queued_topics.append(topic)
        task_ids.append(async_result.id)

    logger.info(
        "top_up_ubt_bank: queued %s topic task(s), target %s, seed %s",
        len(queued_topics),
        target,
        base_seed,
    )
    return {
        "queued": len(queued_topics),
        "topics": queued_topics,
        "seed": base_seed,
        "languages": resolved_languages,
        "task_ids": task_ids,
        "target": target,
    }
