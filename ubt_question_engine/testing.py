"""Test support for anything that needs translated questions.

Real translations cost 396 paid model calls, so CI must never make them. This
builds an in-memory cache whose "translation" is the English source prefixed with
its language code:

    "Simplify"            ->  "kk::Simplify"
    "\\text{circle}"       ->  "\\text{kk::circle}"

Crude on purpose. It proves the right source string was looked up and
substituted, which is the only thing a test can meaningfully assert about a
translation -- judging the Kazakh itself is a job for a Kazakh speaker reading
i18n/instructions.json, not for an assertion.

Lives in the package rather than in a tests/ directory because three separate
test packages need it (the engine's, assessments', and generation's) and
duplicating the store-swapping logic is how the copies drift apart.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from . import i18n

FAKE_LANGUAGES = ("kk", "ru")


def fake_translations(languages: tuple[str, ...] = FAKE_LANGUAGES) -> dict[str, Any]:
    """A complete cache for `languages`, covering every translatable string."""
    store: dict[str, Any] = {}
    for source, meta in i18n.translatable().items():
        for language in languages:
            if meta["kind"] == i18n.KIND_ANSWER_LABEL:
                # Must stay a single \text{...} group: the renderer emits it
                # verbatim into the option, and the symbol check enforces it.
                text = f"\\text{{{language}::{source[6:-1]}}}"
            else:
                text = f"{language}::{source}"
            i18n.put(store, source, language, text, kind=meta["kind"])
    return store


@contextmanager
def use_fake_translations(
    languages: tuple[str, ...] = FAKE_LANGUAGES,
) -> Iterator[dict[str, Any]]:
    """Install a fake cache, then put the real one back.

    i18n caches the store in a module global, so a leak here would silently
    change every later test in the session.
    """
    saved = i18n._STORE
    store = fake_translations(languages)
    i18n._STORE = store
    try:
        yield store
    finally:
        i18n._STORE = saved


@contextmanager
def use_no_translations() -> Iterator[None]:
    """An empty cache: the state of a fresh checkout."""
    saved = i18n._STORE
    i18n._STORE = {}
    try:
        yield
    finally:
        i18n._STORE = saved
