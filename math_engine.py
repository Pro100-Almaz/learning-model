"""
Deterministic math engine for the MAIQE Architect.

Pure functions only: no LangGraph, no GraphState, no database. Everything here
can be unit-tested directly, e.g. `compute_answer_key(bp, {...}) == 258`. The
Architect node (nodes_self.py) is a thin wrapper that calls into this module.

Public API:
    load_blueprint(topic)                 -> dict
    resolve_difficulty(profile, blueprint) -> int (1-3)
    generate_math_spec(blueprint, difficulty) -> dict
    compute_answer_key(blueprint, spec)   -> Any
    render_constraints(blueprint, spec)   -> str
    parse_verdict(raw)                    -> dict ({"passed", "notes"})
    format_answer(answer_key)             -> str
    build_answer_options(answer_key)      -> list[dict] ({"text", "is_correct"})
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

# ---------------------------------------------------------------------------
# Where the topic blueprints + Jinja templates live, and the Jinja engine that
# renders them. Built once at import time so we don't rebuild it per call.
# ---------------------------------------------------------------------------
BLUEPRINT_DIR = Path(__file__).parent / "blueprints"
TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(BLUEPRINT_DIR)),
    autoescape=select_autoescape(),
)

# Difficulty (Question.difficulty is 1-3) chosen from the student's ENT target
# score. Thresholds are on the 0-140 ENT scale (ENT_CONFIG.max_total_score=140).
# A student aiming high gets harder problems.
DIFFICULTY_BY_TARGET = [
    (120, 3),   # target >= 120  -> hard
    (90, 2),    # target >=  90  -> medium
    (0, 1),     # everyone else  -> easy
]


# ---------------------------------------------------------------------------
# Blueprint loading + template rendering
# ---------------------------------------------------------------------------
def load_blueprint(topic: str) -> dict[str, Any]:
    """Load blueprints/<topic>.json."""
    return json.loads((BLUEPRINT_DIR / f"{topic}.json").read_text("utf-8"))


def render_constraints(blueprint: dict, spec: dict) -> str:
    """Render the topic's Jinja template with the rolled numbers."""
    template = TEMPLATE_ENV.get_template(blueprint["constraints_template"])
    return template.render(**spec)


# ---------------------------------------------------------------------------
# Difficulty
# ---------------------------------------------------------------------------
def resolve_difficulty(profile: dict, blueprint: dict) -> int:
    """Pick a 1-3 difficulty from the student's ENT target score.

    `profile` is the dict form of accounts.StudentProfile. If there's no score
    to go on, use the blueprint's declared default.
    """
    target = profile.get("target_score")
    if target is None:
        return int(blueprint.get("default_difficulty", 1))

    target = min(int(target), 140)  # clamp to the ENT max (ENT_CONFIG)
    for threshold, level in DIFFICULTY_BY_TARGET:
        if target >= threshold:
            return level
    return 1


# ---------------------------------------------------------------------------
# Parameter generation + answer computation
# ---------------------------------------------------------------------------
def generate_math_spec(blueprint: dict, difficulty: int) -> dict[str, Any]:
    """Roll concrete values that obey the blueprint's rules at this difficulty.

    Uses rejection sampling: keep rolling the whole set until every constraint
    passes. The per-difficulty `difficulty_overrides` tighten/loosen ranges
    (e.g. a wider integration interval for harder problems).
    """
    parameters = _ranges_for_difficulty(blueprint, difficulty)
    derived = blueprint.get("derived", {})
    constraints = blueprint.get("constraints", [])

    for _ in range(1000):  # safety cap so a bad blueprint can't loop forever
        spec: dict[str, Any] = {}

        # Roll one random integer per parameter, inside its [min, max] range.
        for name, rule in parameters.items():
            spec[name] = random.randint(rule["min"], rule["max"])

        # Fill in any values that are *computed* from the rolled ones.
        for name, expr in derived.items():
            spec[name] = _eval(expr, spec)

        # Accept only if the blueprint's own constraints hold AND the roll isn't
        # structurally degenerate; otherwise re-roll.
        if all(_eval(rule, spec) for rule in constraints) and not _is_degenerate(
            blueprint, spec
        ):
            return spec

    raise RuntimeError(f"Could not satisfy constraints for {blueprint['topic']}")


def _is_degenerate(blueprint: dict, spec: dict) -> bool:
    """Universal non-degeneracy floor, applied on top of blueprint constraints.

    Rejects rolls that produce a trivial or malformed problem *structurally* —
    independent of whether the blueprint author remembered to forbid them — so a
    forgetful blueprint can't ship a broken question. Derived from the `answer`
    block, so it needs no per-blueprint configuration:

      - `roots` answers must have pairwise-distinct, not-all-zero roots. A
        repeated root collapses a "find both roots" problem (and its multiple-
        choice options) to a single value; all-zero roots is the trivial x = 0.

    Topic-specific non-degeneracy that the engine can't infer (e.g. a leading
    coefficient that must be non-zero so a curve stays a parabola) still belongs
    in the blueprint's `constraints` list.
    """
    answer = blueprint.get("answer", {})
    if answer.get("type") == "roots":
        roots = [spec[name] for name in answer["values"]]
        if len(set(roots)) < len(roots):  # a repeated root
            return True
        if all(r == 0 for r in roots):  # the trivial x = 0 problem
            return True
    return False


# ---------------------------------------------------------------------------
# Answer option building (used by the Publisher node to fill AnswerOptions)
# ---------------------------------------------------------------------------
def format_answer(answer_key: Any) -> str:
    """Render an answer_key (scalar, list of roots, or progression dict) as text.

    Mirrors the three `compute_answer_key` shapes:
      - dict   -> "a_n = 12, S_n = 90"   (progression)
      - list   -> "-3, 5"                (roots)
      - scalar -> "258"                  (definite integral)
    """
    if isinstance(answer_key, dict):
        return ", ".join(f"{k} = {v}" for k, v in answer_key.items())
    if isinstance(answer_key, (list, tuple)):
        return ", ".join(str(v) for v in answer_key)
    return str(answer_key)


def build_answer_options(answer_key: Any, n_options: int = 4) -> list[dict[str, Any]]:
    """Build one correct option plus deterministic numeric distractors.

    Distractors are the correct answer with every number shifted by a fixed set
    of deltas (so they look plausible but are wrong), de-duplicated by rendered
    text, then shuffled so the correct option isn't always first. The shuffle
    uses `random`, so seed it in tests for reproducible output.
    """
    correct_text = format_answer(answer_key)
    options = [{"text": correct_text, "is_correct": True}]
    seen = {correct_text}

    for delta in (1, -1, 2, -2, 3, -3, 5, -5, 10, -10):
        if len(options) >= n_options:
            break
        candidate = format_answer(_shift_numbers(answer_key, delta))
        if candidate not in seen:
            seen.add(candidate)
            options.append({"text": candidate, "is_correct": False})

    random.shuffle(options)
    return options


def _shift_numbers(answer_key: Any, delta: int) -> Any:
    """Return a copy of answer_key with every numeric value shifted by `delta`."""
    if isinstance(answer_key, dict):
        return {k: _shift(v, delta) for k, v in answer_key.items()}
    if isinstance(answer_key, (list, tuple)):
        return [_shift(v, delta) for v in answer_key]
    return _shift(answer_key, delta)


def _shift(value: Any, delta: int) -> Any:
    if isinstance(value, bool):  # bool is an int subclass; leave it alone
        return value
    if isinstance(value, int):
        return value + delta
    if isinstance(value, float):
        return round(value + delta, 2)
    return value


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _ranges_for_difficulty(blueprint: dict, difficulty: int) -> dict[str, dict]:
    """Return the parameter ranges, with this difficulty's overrides applied.

    Base ranges live under `parameters`; `difficulty_overrides[str(difficulty)]`
    may patch individual min/max values on top of them.
    """
    # Shallow-copy each rule so we never mutate the loaded blueprint.
    ranges = {name: dict(rule) for name, rule in blueprint["parameters"].items()}

    overrides = blueprint.get("difficulty_overrides", {}).get(str(difficulty), {})
    for name, patch in overrides.items():
        ranges[name].update(patch)
    return ranges


def _eval(expr: str, scope: dict) -> Any:
    """Evaluate a small arithmetic/boolean expression from a blueprint string.

    Blueprint strings may carry a trailing `// comment` for humans; we strip
    that first. `__builtins__` is emptied so the expression can only touch the
    numbers in `scope` — it can't call functions or import anything.
    """
    code = expr.split("//")[0].strip()
    return eval(code, {"__builtins__": {}}, scope)


# ---------------------------------------------------------------------------
# Deterministic verification gate (the Critic's hallucination-proof first pass)
# ---------------------------------------------------------------------------
# Matches integers and decimals (comma OR dot), with an optional leading sign.
_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _abs_number_set(text: str) -> set[float]:
    """Every numeric literal in `text`, as a set of absolute float values.

    We compare on magnitude (abs) so that minus-sign spacing/dash differences
    between the rendered constraints and the draft can't cause false mismatches.
    """
    out: set[float] = set()
    for token in _NUM_RE.findall(text):
        try:
            out.add(abs(float(token.replace(",", "."))))
        except ValueError:
            continue
    return out


def _answer_number_set(answer_key: Any) -> set[float]:
    """The numeric value(s) inside an answer_key (scalar, list, or dict)."""
    if isinstance(answer_key, dict):
        values = list(answer_key.values())
    elif isinstance(answer_key, (list, tuple)):
        values = list(answer_key)
    else:
        values = [answer_key]

    out: set[float] = set()
    for value in values:
        if isinstance(value, bool):  # bool is an int subclass; not a real number here
            continue
        if isinstance(value, (int, float)):
            out.add(abs(float(value)))
    return out


def deterministic_review(
    constraints_payload: str, answer_key: Any, draft_text: str
) -> dict[str, Any]:
    """Hallucination-proof checks the LLM Critic must NOT be trusted with.

    The Architect already knows every number exactly, so number fidelity and
    answer-leak are decided in pure Python — no model judgement involved:

      1. FIDELITY: every value shown in `constraints_payload` must survive into
         the draft (the Storyteller may not drop, round, or alter a given value).
      2. LEAK: no `answer_key` value may appear in the draft UNLESS it was also a
         shown input. That exception is what resolves the case where the answer
         is itself one of the rolled inputs (e.g. quadratic roots).

    Returns {"passed": bool, "notes": str} — same shape as `parse_verdict`, so
    the Critic node can merge it with the LLM's semantic verdict.
    """
    shown = _abs_number_set(constraints_payload)
    drafted = _abs_number_set(draft_text)
    answers = _answer_number_set(answer_key)

    notes: list[str] = []

    missing = sorted(shown - drafted)
    if missing:
        notes.append(
            "NUMBERS: these required values are missing or altered in the draft "
            f"(reproduce them as digits, exactly as given): {missing}."
        )

    # A leaked answer value: present in the draft, but not legitimately shown.
    leaked = sorted((answers & drafted) - shown)
    if leaked:
        notes.append(
            f"ANSWER LEAK: the draft exposes the final answer value(s) {leaked}; "
            "remove them — the problem statement must never reveal the answer."
        )

    return {"passed": not notes, "notes": " ".join(notes)}


# ---------------------------------------------------------------------------
# Verdict parsing (used by the Critic node to read an LLM's JSON reply)
# ---------------------------------------------------------------------------
def parse_verdict(raw: str) -> dict[str, Any]:
    """Pull the {"passed", "notes"} object out of the Critic model's reply.

    Tolerant of ```json fences or stray prose around the object. On any parse
    failure we fail safe (passed=False) so a malformed verdict triggers a
    rewrite rather than letting an unverified draft through.
    """
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return {
                "passed": bool(data.get("passed", False)),
                "notes": str(data.get("notes", "")).strip(),
            }
        except (json.JSONDecodeError, ValueError):
            pass
    return {"passed": False, "notes": f"Could not parse critic verdict: {text[:200]}"}