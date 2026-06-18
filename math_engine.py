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
"""

from __future__ import annotations

import json
import random
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

        # Accept this roll only if all constraints hold; otherwise re-roll.
        if all(_eval(rule, spec) for rule in constraints):
            return spec

    raise RuntimeError(f"Could not satisfy constraints for {blueprint['topic']}")


def compute_answer_key(blueprint: dict, spec: dict) -> Any:
    """Compute the absolute correct answer, in pure Python, from the spec.

    Never delegated to an LLM — this is the math-integrity guarantee.
    """
    answer = blueprint["answer"]
    kind = answer["type"]

    if kind == "roots":
        # Quadratic: the roots are parameters we rolled directly.
        return sorted(spec[name] for name in answer["values"])

    if kind == "progression":
        # Arithmetic progression: evaluate the nth-term and sum formulas.
        return {
            "a_n": int(_eval(answer["nth_term"], spec)),
            "S_n": int(_eval(answer["sum_n"], spec)),
        }

    if kind == "integral_definite":
        # ∫(a x^2 + b x + c) dx = a/3 x^3 + b/2 x^2 + c x  ->  F(upper) - F(lower).
        # Blueprint constraints guarantee a%3==0 and b%2==0, so it stays integer.
        a, b, c = spec["a"], spec["b"], spec["c"]
        f = lambda x: a / 3 * x**3 + b / 2 * x**2 + c * x
        return round(f(spec["upper"]) - f(spec["lower"]))

    raise ValueError(f"Unknown answer type: {kind}")


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
