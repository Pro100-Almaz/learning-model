"""
Deterministic math engine for the MAIQE Architect.

Pure functions only: no LangGraph, no GraphState, no database. Everything here
can be unit-tested directly, e.g. `compute_answer_key(bp, {...}) == 258`. The
Architect node (nodes_self.py) is a thin wrapper that calls into this module.

Public API:
    load_blueprint(topic)                 -> dict
    compute_content_hash(topic, spec)     -> str (dedup key for the bank)
    resolve_difficulty(profile, blueprint) -> int (1-3)
    generate_math_spec(blueprint, difficulty) -> dict
    compute_answer_key(blueprint, spec)   -> Any
    render_constraints(blueprint, spec)   -> str
    build_solution(blueprint, spec, answer_key) -> dict (the Tutor's ground truth)
    parse_verdict(raw)                    -> dict ({"passed", "notes"})
    format_answer(answer_key)             -> str
    build_answer_options(answer_key, distractors, spec) -> list[dict]
        ({"text", "is_correct", "misconception"})
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

# ---------------------------------------------------------------------------
# Where the topic blueprints + Jinja templates live, and the Jinja engine that
# renders them. Built once at import time so we don't rebuild it per call.
# ---------------------------------------------------------------------------
BLUEPRINT_DIR = Path(__file__).resolve().parent.parent / "blueprints"
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


def render_value(value: Any, spec: dict) -> Any:
    """Render one declarative (static_choice) answer/transform value vs the spec.

    A static-choice answer field may embed Jinja expressions referencing the
    rolled parameters, so a parameter-dependent answer needs no symbolic engine:

        "{{ -denom_d }}/{{ denom_c }}"                 -> "4/1"   (arithmetic)
        "2pi/{{ k }}"                                  -> "2pi/3" (substitution)
        "{{ 'Влево' if shift_x > 0 else 'Вправо' }}"   -> "Вправо" (branching)

    Non-string values, and strings without a Jinja marker (`{{` expression or
    `{%` statement) — e.g. "нечетная", "[-1, 1]" — pass through unchanged, so
    purely static answers cost nothing. Note a branched answer may use only
    `{% if %}` tags with no `{{ }}`, so both markers must be checked.
    """
    if not isinstance(value, str) or ("{{" not in value and "{%" not in value):
        return value
    return TEMPLATE_ENV.from_string(value).render(**spec)


def compute_content_hash(topic: str, math_spec: dict[str, Any]) -> str:
    """A stable fingerprint of a problem's *mathematical* identity (dedup key).

    Two generated questions are "the same problem" when they share a topic and
    the same rolled numbers — NOT when their text matches. The Storyteller
    rewraps the same spec in a fresh narrative on every run, so hashing the
    draft would never catch a real duplicate; hashing the spec does. The
    answer_key and worked solution are deterministic functions of the spec, so
    (topic, math_spec) captures the whole identity.

    Canonical JSON (sorted keys, no whitespace) makes the hash independent of
    dict ordering; `default=str` keeps it from crashing on a stray Fraction.
    Used by the Publisher to fill assessments.Question.content_hash (unique).
    """
    canonical = json.dumps(
        {"topic": topic, "spec": math_spec},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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

        # Roll one value per parameter: pick from a fixed `options` list when the
        # blueprint declares one (string/float-valued params like a function name
        # or a table angle), otherwise a random integer inside its [min, max] range.
        for name, rule in parameters.items():
            if "options" in rule:
                spec[name] = random.choice(rule["options"])
            else:
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
# Worked-solution building (the ground truth the Tutor relies on, arch.md §5)
# ---------------------------------------------------------------------------
def build_solution(blueprint: dict, spec: dict, answer_key: Any) -> dict[str, Any]:
    """Build a structured, fully-worked solution for one rolled problem.

    This is what the Tutor (Agent 5) reverse-engineers a student's mistake
    against: the concrete numbers (`spec`), the canonical method, and a
    deterministic step-by-step derivation. Every number here is computed in
    pure Python — never an LLM — so the solution carries the same math-integrity
    guarantee as `compute_answer_key`. It dispatches on the same `answer.type`,
    so add a branch here whenever you add one there.

    Returns a JSON-serializable dict (it is persisted on assessments.Question
    via the Publisher; see GraphState.solution):

        {
          "answer_type":    "roots" | "progression" | "integral_definite",
          "curriculum_ref": str,
          "spec":           {<the rolled numbers, incl. derived ones>},
          "answer_key":     <the correct answer, as compute_answer_key returns it>,
          "steps":          [{"label": str, "detail": str}, ...],
        }
    """
    answer = blueprint["answer"]
    kind = answer["type"]

    builders = {
        "roots": _solution_roots,
        "progression": _solution_progression,
        "integral_definite": _solution_integral,
        "static_choice": _solution_static,
    }
    builder = builders.get(kind)
    if builder is None:
        raise ValueError(f"Unknown answer type: {kind}")

    return {
        "answer_type": kind,
        "curriculum_ref": blueprint.get("curriculum_ref", ""),
        "spec": dict(spec),
        "answer_key": answer_key,
        "steps": builder(answer, spec),
    }


def _solution_roots(answer: dict, spec: dict) -> list[dict[str, str]]:
    """Vieta's-theorem derivation for a*x^2 + b*x + c = 0.

    `b` and `c` are derived from the roots in the blueprint (b = -a(r1+r2),
    c = a*r1*r2), so the Vieta identities below are exact integers here.
    """
    a, b, c = spec["a"], spec["b"], spec["c"]
    roots = sorted(spec[name] for name in answer["values"])
    return [
        {"label": "Equation",
         "detail": f"{a}x^2 + ({b})x + ({c}) = 0  — find both roots"},
        {"label": "Vieta: sum of roots",
         "detail": f"x1 + x2 = -b/a = -({b})/{a} = {roots[0] + roots[1]}"},
        {"label": "Vieta: product of roots",
         "detail": f"x1 * x2 = c/a = ({c})/{a} = {roots[0] * roots[1]}"},
        {"label": "Roots",
         "detail": f"x1 = {roots[0]}, x2 = {roots[1]}"},
    ]


def _solution_progression(answer: dict, spec: dict) -> list[dict[str, str]]:
    """nth-term and partial-sum derivation for an arithmetic progression."""
    a1, d, n = spec["a1"], spec["d"], spec["n"]
    a_n = int(_eval(answer["nth_term"], spec))
    s_n = int(_eval(answer["sum_n"], spec))
    return [
        {"label": "Given",
         "detail": f"a1 = {a1}, d = {d}, n = {n}"},
        {"label": "nth term",
         "detail": f"a_n = a1 + (n-1)d = {a1} + ({n}-1)*{d} = {a_n}"},
        {"label": "Partial sum",
         "detail": f"S_n = n(2a1 + (n-1)d)/2 = {n}(2*{a1} + ({n}-1)*{d})/2 = {s_n}"},
    ]


def _solution_integral(answer: dict, spec: dict) -> list[dict[str, str]]:
    """Definite integral via the antiderivative (FTC), using exact rationals.

    Mirrors compute_answer_key's integral branch: Fraction (not float+round)
    so the shown intermediate values can never drift from the answer_key.
    """
    a, b, c = spec["a"], spec["b"], spec["c"]
    lower, upper = spec["lower"], spec["upper"]
    antideriv = lambda x: Fraction(a, 3) * x**3 + Fraction(b, 2) * x**2 + Fraction(c) * x
    f_upper, f_lower = antideriv(upper), antideriv(lower)
    return [
        {"label": "Integral",
         "detail": f"S = integral of ({a}x^2 + {b}x + {c}) dx, from {lower} to {upper}"},
        {"label": "Antiderivative",
         "detail": f"F(x) = ({a}/3)x^3 + ({b}/2)x^2 + {c}x"},
        {"label": "Evaluate at upper limit",
         "detail": f"F({upper}) = {_fmt_frac(f_upper)}"},
        {"label": "Evaluate at lower limit",
         "detail": f"F({lower}) = {_fmt_frac(f_lower)}"},
        {"label": "Definite integral (FTC)",
         "detail": f"F({upper}) - F({lower}) = {_fmt_frac(f_upper - f_lower)}"},
    ]


def _solution_static(answer: dict, spec: dict) -> list[dict[str, str]]:
    """'Worked solution' for a declarative (static_choice) answer.

    These topics are conceptual — a classification or a set of named properties —
    so there is nothing to *compute*: the derivation is simply the correct
    value(s) spelled out, mirroring the `correct` field compute_answer_key
    returns. A dict yields one step per named property; a scalar yields one step.
    """
    correct = answer["correct"]
    if isinstance(correct, dict):
        return [
            {"label": str(key), "detail": str(render_value(val, spec))}
            for key, val in correct.items()
        ]
    return [{"label": "Answer", "detail": str(render_value(correct, spec))}]


def _fmt_frac(value: Fraction) -> str:
    """Render a Fraction as a plain int when it's whole, else as 'p/q'."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


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


def build_answer_options(
    answer_key: Any,
    distractors: list[dict] | None = None,
    spec: dict | None = None,
    n_options: int = 4,
    literal: bool = False,
) -> list[dict[str, Any]]:
    """Build one correct option plus wrong options, each tagged with its cause.

    Two sources of distractors, used in order:

      1. MISCONCEPTION-DERIVED (preferred): each entry in the blueprint's
         `distractors` carries an `id`, a human `desc`, and a `transform` that
         computes the answer a student would get if they made that specific
         mistake (see `_apply_misconception`). The resulting option is tagged
         with that `id`, so a student's wrong pick *names their error* — the
         Tutor (arch.md §5) reads it instead of guessing.
      2. GENERIC NUMERIC SHIFTS (fallback): if the blueprint declares too few
         misconceptions, or some collapse to duplicate/degenerate values, the
         remaining slots are topped up with the answer shifted by a fixed delta.
         These carry an empty `misconception` — the Tutor must infer those.

    `literal=True` marks a declarative (static_choice) answer: transforms are
    applied as literal replacement text (no eval / no int-coercion) and the
    numeric-shift fallback is skipped, since shifting strings is meaningless. A
    static topic therefore yields exactly one option per declared distractor (plus
    the correct one); declare enough distractors to fill `n_options`.

    Options are de-duplicated by rendered text and shuffled, so the correct one
    isn't always first. The shuffle uses `random`; seed it in tests.
    """
    spec = spec or {}
    correct_text = format_answer(answer_key)
    options = [{"text": correct_text, "is_correct": True, "misconception": ""}]
    seen = {correct_text}

    # 1) Misconception-derived distractors (each names a specific student error).
    for d in distractors or []:
        if len(options) >= n_options:
            break
        try:
            wrong = _apply_misconception(d["transform"], spec, answer_key, literal)
        except (ArithmeticError, KeyError, TypeError, ValueError):
            # A transform that can't be evaluated for this roll (e.g. divide by
            # zero) is simply skipped; the fallback below tops up the slot.
            continue
        text = format_answer(wrong)
        if text in seen:
            continue  # collapsed onto the correct answer or another distractor
        seen.add(text)
        options.append({"text": text, "is_correct": False, "misconception": d["id"]})

    # 2) Fallback: top up any remaining slots with generic numeric shifts. Skipped
    #    for declarative answers, where the values are text and shifting is a no-op.
    if not literal:
        for delta in (1, -1, 2, -2, 3, -3, 5, -5, 10, -10):
            if len(options) >= n_options:
                break
            candidate = format_answer(_shift_numbers(answer_key, delta))
            if candidate not in seen:
                seen.add(candidate)
                options.append({"text": candidate, "is_correct": False, "misconception": ""})

    random.shuffle(options)
    return options


def _apply_misconception(
    transform: Any, spec: dict, answer_key: Any, literal: bool = False
) -> Any:
    """Evaluate a blueprint distractor `transform` into a wrong-but-plausible answer.

    The result has the SAME shape as `answer_key` (so `format_answer` renders it
    identically to the correct option), mirroring `compute_answer_key`'s answer
    shapes. `transform` is expressed in terms of the rolled `spec`, exactly like
    the blueprint's `derived` / `answer` formulas, and is evaluated with the same
    builtin-free `_eval`. Every value is coerced to int, matching the
    integer-clean guarantee the real answer key satisfies.

        roots        -> transform is a list of exprs:  ["-r1", "-r2"]
        progression  -> transform is a dict of exprs:  {"a_n": "...", "S_n": "..."}
        scalar       -> transform is a single expr:    "a/3*upper**3 + ..."

    `literal=True` (declarative / static_choice answers): the transform carries
    replacement TEXT, applied without evaluation or int-coercion. A dict transform
    overrides just the named fields of the correct answer (the other properties
    stay correct, so only the targeted misconception is wrong); a list/scalar
    transform replaces the answer outright.

        static dict  -> transform overrides fields:    {"четность": "четная"}
    """
    if literal:
        if isinstance(answer_key, dict):
            # Render each override vs the spec, then override only those fields.
            overrides = {key: render_value(val, spec) for key, val in transform.items()}
            return {**answer_key, **overrides}
        if isinstance(answer_key, (list, tuple)):
            return [render_value(v, spec) for v in transform]
        return render_value(transform, spec)

    if isinstance(answer_key, dict):
        # Follow answer_key's key order so the rendered text lines up column-for-column.
        return {key: int(_eval(transform[key], spec)) for key in answer_key}
    if isinstance(answer_key, (list, tuple)):
        return [int(_eval(expr, spec)) for expr in transform]
    return int(_eval(transform, spec))


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

# A constraints template may split its rendered payload into a student-facing
# section and an author-only guidance section, the latter introduced by this
# marker (see blueprints/*.j2 — the trig topics use it). Only the student-facing
# section states the problem, so number-fidelity is checked against that part
# alone: numbers that live only in the INTERNAL method notes are guidance for
# the Storyteller, not values the draft is required to echo. Payloads without
# the marker (most topics) are treated wholly as student-facing, unchanged.
INTERNAL_MARKER = "=== INTERNAL"


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
    # Only the student-facing section is posed to the student; numbers confined
    # to the INTERNAL guidance section are not required to appear in the draft.
    student_facing = constraints_payload.split(INTERNAL_MARKER, 1)[0]
    shown = _abs_number_set(student_facing)
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