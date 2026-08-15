"""
The translation cache: the reason serving a Kazakh question costs nothing.

Across 231 modes the collection uses only 179 distinct instructions and 19
distinct answer labels, because the wording belongs to the *mode* while the
numbers belong to the question:

    Simplify        <- fixed by the blueprint, identical in every question
    \\sqrt{63}       <- rolled from the seed, different every time

Mathematics needs no translation, so the entire translatable surface of the
engine is 198 strings. Times three languages, 594 translations that are built
once offline, reviewed once by a native speaker, and then never computed again.

This module owns the store and the lookup. It never calls a model, never touches
the network, and imports nothing from llm.py -- that is what makes it safe on the
request path. Filling the store is scripts/build_i18n.py's job.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .loader import list_topics, load_blueprint

STORE_PATH = Path(__file__).parent / "i18n" / "instructions.json"

# The two kinds of translatable string. Kept apart because the Localizer needs
# different rules for each: an answer label must keep its \text{...} wrapper.
KIND_INSTRUCTION = "instruction"
KIND_ANSWER_LABEL = "answer_label"

# Blueprints are authored in English, so English needs no store: the translation
# of a source string into its own language is the string. Serving `en` therefore
# works before a single model call has ever been made.
SOURCE_LANGUAGE = "en"

_STORE: dict[str, Any] | None = None


class MissingTranslation(LookupError):
    """A string a student must read has no translation in their language."""


def source_key(source: str, language: str) -> str:
    """Cache key for one English string in one language.

    Keyed on a hash of the ENGLISH SOURCE, not on (topic, mode). Two consequences,
    both wanted:

      * Deduplication is automatic. 231 modes collapse to 179 keys, because two
        topics that both say "Find the area" should obviously share one Kazakh
        translation rather than commission two.
      * Invalidation is automatic. Edit an instruction in a blueprint and its
        hash changes, so the build script sees a missing entry instead of
        silently serving a translation of the old wording.
    """
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
    return f"{language}:{digest}"


def load(*, refresh: bool = False) -> dict[str, Any]:
    """The store, read once per process."""
    global _STORE
    if _STORE is None or refresh:
        if STORE_PATH.exists():
            _STORE = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        else:
            _STORE = {}
    return _STORE


def save(store: dict[str, Any]) -> None:
    """Write the store back, in a shape a human can review in a diff.

    `ensure_ascii=False` is not cosmetic: this file exists to be read by a Kazakh
    speaker, and `\\u049b` is unreviewable where `қ` is obvious. sort_keys keeps
    the diff of an incremental build to the lines that actually changed.
    """
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(
        json.dumps(store, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    global _STORE
    _STORE = store


def lookup(source: str, language: str) -> str | None:
    """The translation, or None."""
    if language == SOURCE_LANGUAGE:
        return source
    entry = load().get(source_key(source, language))
    return entry.get("text") if entry else None


def translate(source: str, language: str) -> str:
    """The translation, or raise. What the request path calls.

    Raises rather than falling back to English on a miss, and that choice is
    deliberate. A silent fallback ships a Kazakh student an English question that
    looks deliberate, so nobody reports it and it survives in production for
    months. A raised error is caught by the build gate, by CI, and by the
    publisher before a single row is written.
    """
    text = lookup(source, language)
    if text is None:
        raise MissingTranslation(
            f"No {language!r} translation for {source!r}. "
            f"Run: python -m scripts.build_i18n --language {language}"
        )
    return text


def put(
    store: dict[str, Any],
    source: str,
    language: str,
    text: str,
    *,
    kind: str = KIND_INSTRUCTION,
    reviewed: bool = False,
) -> None:
    """Record one translation.

    The English source is stored beside the translation on purpose: the key is a
    hash, and a reviewer cannot check a translation without seeing what it
    translates.
    """
    store[source_key(source, language)] = {
        "kind": kind,
        "source": source,
        "text": text,
        "reviewed": reviewed,
    }


def translatable() -> dict[str, dict[str, str]]:
    """Every string in the collection that a student reads as words.

    Returns source -> {"kind", "context"}, where context is a representative
    display_name for the prompt. Deduplicated by source, so the count is 198
    rather than the 250 raw occurrences.
    """
    found: dict[str, dict[str, str]] = {}

    for topic in list_topics():
        blueprint = load_blueprint(topic)
        display_name = blueprint.get("display_name", topic)

        for mode_config in blueprint.get("modes", {}).values():
            instruction = mode_config.get("question", {}).get("instruction", "")
            if instruction.strip():
                found.setdefault(
                    instruction,
                    {"kind": KIND_INSTRUCTION, "context": display_name},
                )

        # Answer labels are the option text itself on literal-answer topics. Miss
        # these and a Kazakh student picks between five English phrases -- and the
        # phrases are the answer.
        for label in (blueprint.get("choice_labels") or {}).values():
            if label.strip():
                found.setdefault(
                    label,
                    {"kind": KIND_ANSWER_LABEL, "context": display_name},
                )

    return found


def missing(language: str) -> dict[str, dict[str, str]]:
    """Translatable strings with no entry for `language`.

    Drives the build script, and doubles as a CI gate: a non-empty result for a
    supported language means the question bank cannot be fully served in it.
    """
    if language == SOURCE_LANGUAGE:
        return {}
    store = load()
    return {
        source: meta
        for source, meta in translatable().items()
        if source_key(source, language) not in store
    }


def coverage() -> dict[str, tuple[int, int]]:
    """language -> (translated, total). For the build report and for CI."""
    sources = translatable()
    total = len(sources)
    store = load()
    report: dict[str, tuple[int, int]] = {}
    for language in ("kk", "ru", "en"):
        if language == SOURCE_LANGUAGE:
            report[language] = (total, total)
            continue
        report[language] = (
            sum(1 for source in sources if source_key(source, language) in store),
            total,
        )
    return report
