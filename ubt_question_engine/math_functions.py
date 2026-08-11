"""
Symbolic mathematics for the UBT question engine.

Takes the parameters params.py rolled and produces the finished mathematics of
one item: derived values, the correct answer, and four wrong answers that each
encode a real student mistake.

This is the module that can be wrong without crashing. A bad roll raises; a bad
answer is computed quietly and shipped to a student sitting the real exam. Two
rules follow from that:

  * Nothing here compares rendered text. `4\\sqrt{3}` and `2\\sqrt{12}` are
    different strings and the same number; shipping both as separate options
    gives an item two correct answers. Equality is decided symbolically.
  * Nothing here is allowed to fail silently. SymPy returns `zoo` for division
    by zero rather than raising, so undefined values are checked for explicitly.

Owns: evaluation, derived values, answers, distractor filtering, LaTeX
rendering. Does not own: rolling parameters (params.py), retrying a failed
question (generator.py), or anything to do with disk (loader.py).
"""

from __future__ import annotations

import random
from typing import Any

import sympy

from .params import GenerationError
from .state import Choice

# The only names a blueprint expression may call. Builtins are stripped during
# evaluation, so everything an expression needs must appear here -- including
# `abs`, which 16 expressions use and which would otherwise be gone with the
# builtins. Deliberately a superset of what the current blueprints call, so a
# new topic using `acos` or `ceiling` works instead of failing in production.
SYMPY_NS: dict[str, Any] = {
    "sqrt": sympy.sqrt, "root": sympy.root, "Rational": sympy.Rational,
    "FiniteSet": sympy.FiniteSet, "Tuple": sympy.Tuple,
    "Interval": sympy.Interval, "Union": sympy.Union,
    "Intersection": sympy.Intersection, "Complement": sympy.Complement,
    "EmptySet": sympy.EmptySet, "abs": sympy.Abs, "Abs": sympy.Abs,
    "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan, "cot": sympy.cot,
    "asin": sympy.asin, "acos": sympy.acos, "atan": sympy.atan,
    "log": sympy.log, "exp": sympy.exp, "Max": sympy.Max, "Min": sympy.Min,
    "floor": sympy.floor, "ceiling": sympy.ceiling, "sign": sympy.sign,
    "factorial": sympy.factorial, "binomial": sympy.binomial,
    "Eq": sympy.Eq, "Symbol": sympy.Symbol,
    "pi": sympy.pi, "oo": sympy.oo, "I": sympy.I, "E": sympy.E,
    "x": sympy.Symbol("x"), "y": sympy.Symbol("y"),
}

# Every UBT item shows five options: the answer plus four distinct wrong ones.
CHOICE_COUNT = 5
DISTRACTORS_NEEDED = CHOICE_COUNT - 1


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    """Evaluate one blueprint expression against concrete values.

    Values are bound into the namespace *before* evaluation rather than
    substituted afterwards. This is not a style choice: sympify("Rational(k-k2,
    m)") raises TypeError while k and m are still symbols, because Rational
    demands numbers at construction. Binding them first is the only order that
    works for the 127 Rational expressions in the collection.

    Context values are sympified so that integer division stays exact -- a bare
    Python int would make `a/3` a float and ship 1.3333333333333333 as an
    "exact" answer.
    """
    namespace = {**SYMPY_NS}
    for name, value in context.items():
        if isinstance(value, (sympy.Basic, str)):
            namespace[name] = value
        else:
            namespace[name] = sympy.sympify(value)

    try:
        result = eval(expression, {"__builtins__": {}}, namespace)
    except Exception as error:
        raise GenerationError(
            f"cannot evaluate {expression!r} with {context}: "
            f"{type(error).__name__}: {error}"
        ) from error

    if isinstance(result, str):
        return result
    return sympy.sympify(result)


def build_context(
    blueprint: dict[str, Any],
    mode: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Rolled parameters plus derived values -- the mathematical context.

    Derived values are evaluated in declaration order and fed forward, so a
    derived value may reference an earlier one (one mode in the collection,
    rational_linear, does exactly that).

    Deliberately free of LaTeX: this dict is the namespace every later
    expression is evaluated in, and a LaTeX string in it would be sympified as
    if it were mathematics. Presentation strings come from template_context.
    """
    context: dict[str, Any] = dict(parameters)

    for name, expression in blueprint["modes"][mode].get("derived", {}).items():
        context[name] = evaluate(expression, context)

    return context


def template_context(context: dict[str, Any]) -> dict[str, Any]:
    """The math context plus a LaTeX twin of every value, for rendering.

    A question template may write `R={r_latex}` where the context holds
    `r = sqrt(u**2+v**2)`. Raw str() of that is "sqrt(41)"; only sympy.latex
    gives "\\sqrt{41}". Kept apart from build_context so these strings can never
    reach an evaluation namespace.
    """
    rendered = {
        f"{name}_latex": value if isinstance(value, str) else sympy.latex(value)
        for name, value in context.items()
    }
    return {**context, **rendered}


def answer_kind(blueprint: dict[str, Any], mode: str) -> str:
    """"literal" for a named answer, "symbolic" for a computed one.

    The mode's own answer_render outranks the blueprint-level answer.type:
    ubt_sphere_plane_positions is mixed_by_mode, where three modes answer with a
    word ("circle") and one answers with a number, and only answer_render tells
    them apart. Three topics (ubt_powers, ubt_roots_expressions,
    ubt_trigonometry) carry no answer_render block at all, hence the fallback.

    Of the 24 render kinds in the collection only literal_label changes how a
    value is produced; the rest (exact_number, interval_union, degree_set, ...)
    are display hints and are all treated as symbolic here.
    """
    render = blueprint.get("answer_render", {}).get(mode)
    if render is not None:
        return "literal" if render == "literal_label" else "symbolic"
    return "literal" if blueprint["answer"]["type"] == "literal_by_mode" else "symbolic"


def compute_answer(
    blueprint: dict[str, Any],
    mode: str,
    context: dict[str, Any],
) -> Any:
    """The correct answer: a label string, or a SymPy value."""
    expression = blueprint.get("answer", {}).get("expression", {}).get(mode)
    if expression is None:
        raise GenerationError(
            f"{blueprint.get('topic')!r}/{mode}: no answer expression."
        )

    if answer_kind(blueprint, mode) == "literal":
        labels = blueprint.get("choice_labels", {})
        if labels and expression not in labels:
            raise GenerationError(
                f"{blueprint.get('topic')!r}/{mode}: answer literal "
                f"{expression!r} has no choice_labels entry."
            )
        return expression

    return evaluate(expression, context)


def is_defined(value: Any) -> bool:
    """False for values no student could ever pick.

    SymPy does not raise on division by zero -- Rational(3, 0) returns `zoo`
    quietly, and without this check it would render as \\tilde{\\infty} and ship
    as a real option. Complex results (an even root of a negative radicand) are
    rejected for the same reason.

    Infinity itself is NOT rejected: Interval(-oo, 2) is a legitimate answer in
    six topics.
    """
    if isinstance(value, str):
        return True
    if not isinstance(value, sympy.Basic):
        return value is not None
    return not value.has(sympy.zoo, sympy.nan, sympy.I)


def canonical(value: Any) -> Any:
    """Rebuild a value with every expression leaf simplified.

    Containers cannot be compared by subtraction -- FiniteSet(1,2) -
    FiniteSet(3) is set difference, not a comparison -- so equivalence for the
    196 Tuple and 157 FiniteSet expressions in the collection goes through
    structural comparison of simplified leaves instead.
    """
    if isinstance(value, str) or not isinstance(value, sympy.Basic):
        return value
    if isinstance(value, sympy.Expr):
        return sympy.simplify(value)
    if value.args:
        return value.func(*(canonical(arg) for arg in value.args))
    return value


def _numerically_distinct(a: Any, b: Any) -> bool:
    """True when two expressions provably differ, decided by floating point.

    A cheap pre-filter in front of sympy.simplify, which is the expensive part
    of generation. Most distractors differ from the answer by an obvious margin;
    only the near-misses need symbolic work. Returns False (meaning "cannot
    decide, do the real check") whenever the numeric evaluation is not
    conclusive.
    """
    if not (isinstance(a, sympy.Expr) and isinstance(b, sympy.Expr)):
        return False
    try:
        difference = complex((a - b).evalf(20))
    except (TypeError, ValueError, AttributeError):
        return False
    if difference != difference:  # NaN
        return False
    return abs(difference) > 1e-9


def equivalent(a: Any, b: Any) -> bool:
    """True when two answers are the same mathematical object.

    Never compares rendered text: 4\\sqrt{3} and 2\\sqrt{12} are different
    strings and the same number, and an item offering both has two correct
    answers.
    """
    if isinstance(a, str) or isinstance(b, str):
        return a == b

    if a == b:  # SymPy canonicalises hard at construction; this catches most
        return True

    if _numerically_distinct(a, b):
        return False

    if isinstance(a, sympy.Expr) and isinstance(b, sympy.Expr):
        try:
            return sympy.simplify(a - b) == 0
        except (TypeError, ValueError):
            return False

    try:
        return canonical(a) == canonical(b)
    except Exception:
        return False


def render(value: Any, blueprint: dict[str, Any]) -> str:
    """LaTeX for one answer option."""
    if isinstance(value, str):
        labels = blueprint.get("choice_labels", {})
        # Labels are authored as LaTeX already, e.g. "\\text{parallel lines}".
        return labels.get(value, rf"\text{{{value}}}")
    return sympy.latex(value)


def build_choices(
    blueprint: dict[str, Any],
    mode: str,
    context: dict[str, Any],
    correct: Any,
    rng: random.Random,
) -> list[Choice]:
    """Five options: the answer plus four surviving distractors, shuffled.

    Raises GenerationError when the pool cannot supply four usable wrong
    answers. Retrying with a fresh draw is the caller's job -- keeping the
    failure and the retry in separate functions keeps the loops readable.
    """
    pool = blueprint.get("distractors", {}).get(mode, [])
    if not pool:
        raise GenerationError(f"{blueprint.get('topic')!r}/{mode}: no distractor pool.")

    survivors: list[tuple[str, Any]] = []
    for item in pool:
        identifier = str(item.get("id", ""))

        if "literal" in item:
            value: Any = str(item["literal"])
        else:
            try:
                value = evaluate(str(item.get("transform", "")), context)
            except GenerationError:
                # One unusable distractor must not cost the whole question;
                # there are five or more others.
                continue

        if not is_defined(value):
            continue
        if equivalent(value, correct):
            continue
        # Compared against what was kept, not against the raw pool: comparing
        # pairwise over the pool would drop both members of a duplicate pair.
        if any(equivalent(value, kept) for _, kept in survivors):
            continue
        survivors.append((identifier, value))

    if len(survivors) < DISTRACTORS_NEEDED:
        raise GenerationError(
            f"{blueprint.get('topic')!r}/{mode}: only {len(survivors)} usable "
            f"distractor(s) of {len(pool)} after filtering; need "
            f"{DISTRACTORS_NEEDED}."
        )

    selected = rng.sample(survivors, DISTRACTORS_NEEDED)

    choices: list[Choice] = [
        {"latex": render(correct, blueprint), "is_correct": True, "distractor_id": ""}
    ]
    for identifier, value in selected:
        choices.append(
            {
                "latex": render(value, blueprint),
                "is_correct": False,
                "distractor_id": identifier,
            }
        )

    rng.shuffle(choices)
    return choices
