"""
Writing generated questions into the question bank.

THE ONLY MODULE IN THIS ENGINE THAT IMPORTS DJANGO. Nothing else here touches a
model, a setting or a connection, which is what lets the other twelve modules be
imported and tested without a database -- and is why preview.py can render all 58
topics on a laptop with no services running. Nothing in the engine imports this
module; the dependency points one way, inwards.

The unit of publication is an ITEM, not a row: one generation becomes three rows,
one per language, written in a single transaction.

    publish(build_all_languages("ubt_roots_expressions", difficulty=1, seed=41))
    -> {"kk": {...}, "ru": {...}, "en": {...}}

Two hashes, two jobs, and confusing them is the subtle bug this module exists to
prevent:

    state["content_hash"]          language-free. The identity of the MATHEMATICS.
                                   Identical across the three rows, stored inside
                                   Question.solution, and the value that joins the
                                   three rows back together as one question.

    row_content_hash(...)          carries the language. Written to
                                   Question.content_hash, whose UNIQUE constraint
                                   would otherwise reject the second and third
                                   language of every item as duplicates.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.db import transaction

from apps.assessments.services import publish_generated_question

from . import i18n
from .generator import generate_question
from .math_functions import CHOICE_COUNT
from .localize import ENGINE_LANGUAGES, localize
from .state import Choice, EngineState, Language


class PublishError(RuntimeError):
    """An item could not be published. Nothing was written."""


def row_content_hash(math_hash: str, language: str) -> str:
    """The per-row fingerprint: this item's mathematics, in this language.

    Question.content_hash is UNIQUE, so the three language versions of one item
    need three distinct values. Derived from the language-free hash rather than
    recomputed from scratch, so the relationship between them stays obvious to
    anyone reading a row.
    """
    return hashlib.sha256(f"{math_hash}|{language}".encode("utf-8")).hexdigest()


def to_options(choices: list[Choice]) -> list[dict[str, Any]]:
    """EngineState choices -> the shape publish_generated_question expects.

    `distractor_id` becomes `misconception`: the blueprint's own name for the
    mistake that produces this option ("forgot_half"), which is what the Tutor
    later reads to name the student's error instead of guessing at it.
    """
    return [
        {
            "text": choice["latex"],
            "is_correct": choice["is_correct"],
            "misconception": choice["distractor_id"],
        }
        for choice in choices
    ]


def _preflight(states: dict[Language, EngineState]) -> None:
    """Refuse the whole item before writing any of it.

    Everything checkable is checked here, up front. The alternative -- discovering
    a missing Kazakh label on the third insert and rolling back -- works, but it
    turns a clear, cheap error into a database round trip and a confusing one.
    """
    if not states:
        raise PublishError("nothing to publish: no language versions given.")

    hashes = {state["content_hash"] for state in states.values()}
    if len(hashes) != 1:
        # Would mean the versions came from different generations, which defeats
        # the entire point of publishing them together.
        raise PublishError(
            f"language versions disagree about the mathematics: {sorted(hashes)}. "
            "Build them with localize.build_all_languages, not separate calls."
        )

    # Translation completeness is deliberately NOT checked here. localize() calls
    # i18n.translate(), which raises on a miss, so an untranslated item can never
    # reach this function at all -- and by then the state's `instruction` is
    # already in the target language, so looking it up in the cache would search
    # for the Kazakh text and "find" a gap that does not exist. Callers that want
    # to check BEFORE generating use localize.missing_for on the English state.
    for language, state in states.items():
        if language not in ENGINE_LANGUAGES:
            raise PublishError(f"unsupported language {language!r}.")
        if state.get("language") != language:
            raise PublishError(
                f"state filed under {language!r} says it is "
                f"{state.get('language')!r}."
            )
        if not state.get("text", "").strip():
            raise PublishError(
                f"{state['topic']!r}/{language}: no text. Publish the output of "
                "localize(), not of generate_question()."
            )


def _solution_payload(state: EngineState) -> dict[str, Any]:
    """Question.solution: the reproduction record plus the cross-language key.

    `content_hash` is folded in here because Question.content_hash carries the
    language and so cannot serve as the join key. This is the one place the
    language-free identity survives into the database.
    """
    return {**state["solution"], "content_hash": state["content_hash"]}


def publish(
    states: dict[Language, EngineState],
    *,
    explanation: str = "",
) -> dict[Language, dict[str, Any]]:
    """Write every language version of one item, atomically.

    All three rows or none. A half-published item is worse than an unpublished
    one: the bank looks fuller than it is, and only a student sitting the exam in
    the missing language ever finds out.

    Returns language -> {question_id, was_duplicate, lesson_id, test_id}. A
    duplicate is not an error -- a batch re-rolling a seed it already used is
    ordinary, and dedup on content_hash is exactly what should happen.
    """
    _preflight(states)

    results: dict[Language, dict[str, Any]] = {}
    with transaction.atomic():
        for language, state in states.items():
            results[language] = publish_generated_question(
                text=state["text"],
                explanation=state.get("explanation", explanation),
                difficulty=int(state["difficulty"]),
                language=language,
                solution=_solution_payload(state),
                options=to_options(state["answer_options"]),
                tag_slug=state["tag_slug"],
                tag_name=state["tag_name"],
                content_hash=row_content_hash(state["content_hash"], language),
                # The bank defaults to MAIQE's four options; a real ҰБТ paper
                # offers five (A-E), and so does every blueprint here.
                expected_options=CHOICE_COUNT,
            )
    return results


def publish_item(
    topic: str,
    difficulty: int | None = None,
    seed: int | None = None,
    languages: tuple[Language, ...] = ENGINE_LANGUAGES,
) -> dict[Language, dict[str, Any]]:
    """Generate one item and publish every language version of it.

    Generates ONCE and dresses the result, rather than generating per language.
    The three rows are then the same mathematics by construction, not by a
    coincidence that a blueprint edit could later break.
    """
    base = generate_question(topic, difficulty=difficulty, seed=seed)
    return publish({language: localize(base, language) for language in languages})


def publishable_languages() -> tuple[Language, ...]:
    """Languages whose translation coverage is complete enough to publish.

    Lets a batch job serve English today and pick up Kazakh the moment
    build_i18n finishes, instead of failing every item until then.
    """
    return tuple(
        language
        for language in ENGINE_LANGUAGES
        if not i18n.missing(language)
    )
