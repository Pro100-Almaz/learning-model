"""
Dressing a generated question in a student's language.

The public entry point of the whole engine lives here:

    build_question("ubt_roots_expressions", "kk", difficulty=1, seed=41)
    build_all_languages("ubt_roots_expressions", difficulty=1, seed=41)

Generate once, dress three times. `generate_question` is language-free by
construction -- it decides the mode, the numbers, the answer, the four
distractors and the order they are shuffled into, all before any language is
mentioned. Localization only replaces words. So the Kazakh, Russian and English
versions of an item are not merely *expected* to pose the same question; it is
structurally impossible for them not to.

Contains no LLM call and no network access. If this module ever imports llm.py,
the design has gone wrong: everything a model needed to say was said once,
offline, and stored by i18n.py.
"""

from __future__ import annotations

import copy
from typing import Any

from . import i18n
from .generator import generate_question
from .present import render_question
from .state import EngineState, Language

# Kept in one place so the engine, the build script and the publisher cannot
# disagree about what "all languages" means. config.SUPPORTED_LANGUAGES is the
# backend's list and currently omits "en"; reconciling the two is PART 9's job,
# because it changes Question.language's choices and needs a migration.
ENGINE_LANGUAGES: tuple[Language, ...] = ("kk", "ru", "en")


def _localize_labels(state: EngineState, language: str) -> dict[str, str]:
    """English answer-label LaTeX -> its translation, for this question.

    Only 3 of 58 topics need this, and on those topics the label IS the answer:
    a Kazakh student would otherwise be choosing between five English phrases.
    """
    labels = state.get("choice_labels") or {}
    return {
        label: i18n.translate(label, language)
        for label in labels.values()
        if label.strip()
    }


def localize(state: EngineState, language: Language) -> EngineState:
    """Return a copy of `state` with its words in `language`.

    A copy, never a mutation: the caller is usually rendering the same question
    in three languages, and mutating would make the second call translate the
    first call's output.
    """
    if language not in ENGINE_LANGUAGES:
        raise ValueError(
            f"Unsupported language {language!r}; expected one of {ENGINE_LANGUAGES}."
        )

    localized: EngineState = copy.deepcopy(state)
    localized["language"] = language
    localized["instruction"] = i18n.translate(state.get("instruction", ""), language)

    label_map = _localize_labels(state, language)
    if label_map:
        for choice in localized.get("answer_options", []):
            choice["latex"] = label_map.get(choice["latex"], choice["latex"])
        localized["answer_latex"] = label_map.get(
            state["answer_latex"], state["answer_latex"]
        )

    # `solution` is deliberately left in its English/LaTeX canonical form. It is
    # the reproduction record, and regenerate() compares against it: translating
    # it would make every stored question look like it had drifted.

    localized["text"] = render_question(localized)
    localized["used_contextualizer"] = False
    return localized


def build_question(
    topic: str,
    language: Language,
    difficulty: int | None = None,
    seed: int | None = None,
) -> EngineState:
    """One finished question, in one language. What callers should use."""
    return localize(generate_question(topic, difficulty=difficulty, seed=seed), language)


def build_all_languages(
    topic: str,
    difficulty: int | None = None,
    seed: int | None = None,
    languages: tuple[Language, ...] = ENGINE_LANGUAGES,
) -> dict[Language, EngineState]:
    """The same question in every language, from a single generation.

    Preferred over calling build_question three times. Generating once removes
    the time gap in which a blueprint edit could slip between the Kazakh version
    and the Russian one and quietly make them different questions.

    Every returned state shares one `content_hash` -- that hash is the identity
    of the mathematics, and it is what joins the rows back together in the
    database.
    """
    base = generate_question(topic, difficulty=difficulty, seed=seed)
    return {language: localize(base, language) for language in languages}


def missing_for(state: EngineState, language: Language) -> list[str]:
    """Which of this question's strings cannot yet be shown in `language`.

    Lets a caller check before building instead of catching MissingTranslation,
    which matters to the publisher: it writes three rows in one transaction and
    would rather refuse up front than roll back.
    """
    if language == i18n.SOURCE_LANGUAGE:
        return []
    sources: list[Any] = [state.get("instruction", "")]
    sources.extend((state.get("choice_labels") or {}).values())
    return [
        source
        for source in sources
        if source and i18n.lookup(source, language) is None
    ]
