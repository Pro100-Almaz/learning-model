"""
Blueprint loading for the UBT question engine.

The single door between the 58 topic JSON files on disk and the rest of the
engine: no other module opens a blueprint file. Loading only — this module never
evaluates an expression, never imports SymPy, and never judges whether a
blueprint is mathematically sound. That is the validator's job.

Topics are addressed by filename stem ("ubt_roots_expressions"), never by path.
The directory tree groups files for humans; it carries no meaning the engine
reads, and one of its folders ("inequalitites") is misspelled on disk. Building
paths from folder names would inherit that typo forever.
"""

from __future__ import annotations

import copy
import difflib
import json
from pathlib import Path
from typing import Any

# Anchored to this file, not to the current working directory: Celery workers,
# Django management commands and pytest all start from different places.
BLUEPRINT_ROOT = Path(__file__).parent / "ubt_blueprints"

# stem -> path, built once on first use. A plain module-level cache rather than
# functools.lru_cache, because the cached value is a mutable dict and every
# caller must get its own copy (see load_blueprint).
_INDEX: dict[str, Path] | None = None
_CACHE: dict[str, dict[str, Any]] = {}


class BlueprintError(LookupError):
    """A blueprint could not be found, or the tree is ambiguous."""


def _build_index() -> dict[str, Path]:
    """Map every blueprint stem to its path, rejecting ambiguity."""
    index: dict[str, Path] = {}
    for path in sorted(BLUEPRINT_ROOT.rglob("*.json")):
        stem = path.stem
        if stem in index:
            # Two files could answer to one topic name. Picking either one
            # silently would make generation depend on directory order.
            raise BlueprintError(
                f"Duplicate blueprint stem {stem!r}: "
                f"{index[stem].relative_to(BLUEPRINT_ROOT)} and "
                f"{path.relative_to(BLUEPRINT_ROOT)}"
            )
        index[stem] = path
    if not index:
        raise BlueprintError(f"No blueprints found under {BLUEPRINT_ROOT}")
    return index


def _index() -> dict[str, Path]:
    global _INDEX
    if _INDEX is None:
        _INDEX = _build_index()
    return _INDEX


def list_topics() -> list[str]:
    """Every topic name the engine can generate, sorted."""
    return sorted(_index())


def load_blueprint(topic: str) -> dict[str, Any]:
    """Return the parsed blueprint for `topic`.

    The result is a deep copy: callers merge difficulty overrides into the
    parameter schema, so handing out the cached dict would let the first caller
    corrupt every later one in the same process.
    """
    index = _index()
    if topic not in index:
        near = difflib.get_close_matches(topic, index, n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        raise BlueprintError(f"Unknown topic {topic!r}.{hint}")

    if topic not in _CACHE:
        # encoding is explicit: the tag names are Cyrillic and Windows would
        # otherwise decode them as cp1251 and corrupt them.
        _CACHE[topic] = json.loads(index[topic].read_text(encoding="utf-8"))

    return copy.deepcopy(_CACHE[topic])


def topic_meta(topic: str) -> dict[str, Any]:
    """Curriculum metadata for one topic, with the gaps already filled in.

    Nine blueprints omit `supported_difficulties`; it is derived from the keys of
    `difficulty_overrides` so that no caller anywhere has to write a `.get(...)`
    fallback and risk two modules disagreeing about a topic's difficulties.
    """
    blueprint = load_blueprint(topic)

    supported = blueprint.get("supported_difficulties")
    if not supported:
        supported = [int(key) for key in blueprint.get("difficulty_overrides", {})]
    supported = sorted(int(level) for level in supported)
    if not supported:
        raise BlueprintError(
            f"Blueprint {topic!r} declares no difficulties: it has neither "
            "supported_difficulties nor difficulty_overrides."
        )

    default = blueprint.get("default_difficulty", supported[0])
    if default not in supported:
        raise BlueprintError(
            f"Blueprint {topic!r} defaults to difficulty {default}, "
            f"which is not among its supported levels {supported}."
        )

    tag = blueprint.get("tag", {})
    return {
        "topic": topic,
        "display_name": blueprint.get("display_name", topic),
        "curriculum_ref": blueprint.get("curriculum_ref", ""),
        "tag_name": tag.get("name", ""),
        "tag_slug": tag.get("slug", ""),
        "supported_difficulties": supported,
        "default_difficulty": int(default),
        "modes": sorted(blueprint.get("modes", {})),
    }


def modes_for_difficulty(topic: str, difficulty: int) -> list[str]:
    """The modes a given difficulty is allowed to draw from.

    Difficulty selects mathematical structure, not bigger numbers (architecture
    doc section 3), and that selection lives in `difficulty_overrides[d].mode`.
    Falls back to the full mode list for a topic that overrides other parameters
    but not the mode itself.
    """
    blueprint = load_blueprint(topic)
    override = blueprint.get("difficulty_overrides", {}).get(str(difficulty))
    if override is None:
        raise BlueprintError(
            f"Topic {topic!r} has no difficulty {difficulty}; "
            f"supported: {topic_meta(topic)['supported_difficulties']}."
        )

    modes = override.get("mode", {}).get("values")
    if not modes:
        modes = blueprint.get("parameters", {}).get("mode", {}).get("values", [])
    return list(modes)
