"""
Static checks over every topic blueprint.

The blueprints are hand-written configuration that Python executes. A typo in a
distractor transform is not a syntax error anywhere — it becomes a crash on one
seed of one mode, weeks later. This module finds all of them in one pass, before
the generator exists, and is meant to run in CI.

Checks only what can be decided without rolling numbers: names resolve,
expressions parse, pools are big enough and distinct, placeholders have
something to fill them. Whether a formula is mathematically *correct* is not
decidable here — that is what the generator's own validation and human review
are for.

Run:  python -m ubt_question_engine.lint_blueprints
Exit: 0 clean, 1 findings.
"""

from __future__ import annotations

import ast
import re
import string
import sys
from typing import Any, Iterable, NamedTuple

from .loader import list_topics, load_blueprint, topic_meta

# Expressions are checked with Python's own parser, not with sympy.sympify.
# Many legal blueprint expressions -- Rational(k-k2, m), root(m, degree) --
# raise inside SymPy while their arguments are still symbols, and only become
# valid once the generator substitutes concrete integers. Parsing with `ast`
# separates the two questions: this module answers "is it well-formed and are
# its names defined", the generator answers "does it evaluate".
ALLOWED_NAMES = {
    "sqrt", "root", "Rational", "FiniteSet", "Tuple", "abs", "Abs", "pi", "E",
    "I", "log", "ln", "exp", "sin", "cos", "tan", "cot", "asin", "acos",
    "atan", "Interval", "oo", "S", "Union", "Intersection", "Complement",
    "EmptySet", "factorial", "binomial", "floor", "ceiling", "sign", "Max",
    "Min", "Eq", "Piecewise", "simplify", "Symbol", "x", "y",
}

# A question template may ask for `{r_latex}` where the context defines `r`:
# the renderer is expected to publish a LaTeX rendering of every value under
# this suffix, because a raw surd or fraction prints badly.
LATEX_SUFFIX = "_latex"

# Distractor ids that name nothing. These were all renamed; the check keeps them
# from creeping back in.
BARE_ID = re.compile(r"d\d+|distractor_?\d+|[a-z]\d+|wrong\d*|option\d+")

# Minimum distinct wrong answers a pool must offer: four are used, and one is
# routinely dropped for being symbolically equal to the correct answer.
MIN_POOL = 6


class Finding(NamedTuple):
    topic: str
    where: str
    message: str
    # "error" breaks generation and fails the run; "warn" is a blueprint that
    # still works but is weaker than it claims (a pool that offers five real
    # options while declaring six). Fixing a warning is a mathematical
    # judgement call, so it must not block CI.
    level: str = "error"

    def __str__(self) -> str:
        return f"[{self.level:5}] {self.topic}: {self.where}: {self.message}"


def _placeholders(template: str) -> set[str]:
    """Field names a str.format template would demand."""
    return {
        field
        for _, field, _, _ in string.Formatter().parse(template)
        if field
    }


def _symbols(expression: str) -> set[str] | None:
    """Every name mentioned in `expression`, or None if it does not parse."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return None
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _resolvable(placeholders: Iterable[str], known: set[str]) -> list[str]:
    """Placeholders with nothing to fill them, honouring the _latex suffix."""
    missing = []
    for field in placeholders:
        base = field[: -len(LATEX_SUFFIX)] if field.endswith(LATEX_SUFFIX) else field
        if base not in known:
            missing.append(field)
    return sorted(missing)


def _check_expression(
    topic: str,
    where: str,
    expression: str,
    known: Iterable[str],
) -> list[Finding]:
    """Parse one expression and confirm every name in it is defined."""
    symbols = _symbols(expression)
    if symbols is None:
        return [Finding(topic, where, f"does not parse: {expression!r}")]

    unknown = sorted(symbols - set(known) - ALLOWED_NAMES)
    if unknown:
        return [
            Finding(
                topic,
                where,
                f"uses undefined name(s) {unknown} in {expression!r}",
            )
        ]
    return []


def _check_mode(
    topic: str,
    blueprint: dict[str, Any],
    mode: str,
    config: dict[str, Any],
) -> list[Finding]:
    findings: list[Finding] = []
    parameters = blueprint.get("parameters", {})
    answer = blueprint.get("answer", {})
    labels = blueprint.get("choice_labels", {})

    # Whether this mode's answer is a named literal or a computed expression.
    # `mixed_by_mode` topics decide per mode, via answer_render, so the mode's
    # own render kind outranks the blueprint-level type.
    render = blueprint.get("answer_render", {}).get(mode)
    literal_answer = (
        render == "literal_label"
        if render is not None
        else answer.get("type") == "literal_by_mode"
    )

    # --- parameters the mode claims to use -------------------------------
    uses = list(config.get("uses", []))
    for name in uses:
        if name not in parameters:
            findings.append(
                Finding(topic, mode, f"uses undeclared parameter {name!r}")
            )

    # --- derived values, resolved in declaration order --------------------
    known = set(uses)
    for name, expression in config.get("derived", {}).items():
        findings += _check_expression(topic, f"{mode}.derived.{name}", expression, known)
        known.add(name)

    for expression in config.get("constraints", []):
        findings += _check_expression(topic, f"{mode}.constraints", expression, known)

    # --- the rendered question -------------------------------------------
    question = config.get("question", {})
    if "instruction" not in question:
        findings.append(Finding(topic, mode, "question has no instruction"))

    latex = question.get("latex", "")
    unfillable = _resolvable(_placeholders(latex), known)
    if unfillable:
        findings.append(
            Finding(topic, mode, f"question.latex has unfillable placeholder(s) {unfillable}")
        )

    # --- the correct answer ----------------------------------------------
    expression = answer.get("expression", {}).get(mode)
    if expression is None:
        findings.append(Finding(topic, mode, "no answer expression"))
    elif literal_answer:
        if labels and expression not in labels:
            findings.append(
                Finding(topic, mode, f"answer literal {expression!r} has no choice_labels entry")
            )
    else:
        findings += _check_expression(topic, f"{mode}.answer", expression, known)

    # --- the distractor pool ---------------------------------------------
    pool = blueprint.get("distractors", {}).get(mode)
    if not pool:
        findings.append(Finding(topic, mode, "no distractor pool"))
        return findings

    if len(pool) < MIN_POOL:
        findings.append(
            Finding(topic, mode, f"pool has {len(pool)} distractors, want >= {MIN_POOL}")
        )

    ids: list[str] = []
    values: list[str] = []
    for item in pool:
        identifier = str(item.get("id", ""))
        ids.append(identifier)
        if not identifier:
            findings.append(Finding(topic, mode, "distractor has no id"))
        elif BARE_ID.fullmatch(identifier):
            findings.append(
                Finding(topic, mode, f"distractor id {identifier!r} names no mistake")
            )

        if "literal" in item:
            value = str(item["literal"])
            if labels and value not in labels:
                findings.append(
                    Finding(topic, mode, f"literal {value!r} has no choice_labels entry")
                )
        elif "transform" in item:
            value = str(item["transform"])
            findings += _check_expression(
                topic, f"{mode}.distractors.{identifier}", value, known
            )
        else:
            value = ""
            findings.append(
                Finding(topic, mode, f"distractor {identifier!r} has no transform or literal")
            )
        values.append(value)

    duplicate_ids = {i for i in ids if ids.count(i) > 1}
    if duplicate_ids:
        findings.append(Finding(topic, mode, f"duplicate distractor id(s) {sorted(duplicate_ids)}"))

    duplicate_values = {v for v in values if v and values.count(v) > 1}
    if duplicate_values:
        distinct = len(set(values))
        findings.append(
            Finding(
                topic,
                mode,
                f"pool offers the same value twice: {sorted(duplicate_values)} "
                f"({distinct} distinct of {len(values)})",
                # Only fatal once dedup leaves too few options to fill the item.
                level="error" if distinct < 4 else "warn",
            )
        )

    return findings


def lint_topic(topic: str) -> list[Finding]:
    """Every finding for one blueprint. Never raises on bad content."""
    findings: list[Finding] = []
    blueprint = load_blueprint(topic)

    if blueprint.get("topic") != topic:
        findings.append(
            Finding(topic, "topic", f"`topic` field is {blueprint.get('topic')!r}, filename says {topic!r}")
        )

    tag = blueprint.get("tag", {})
    if not tag.get("slug") or not tag.get("name"):
        findings.append(Finding(topic, "tag", "missing tag.slug or tag.name"))

    try:
        meta = topic_meta(topic)
    except Exception as error:  # loader refuses to resolve the difficulties
        findings.append(Finding(topic, "difficulty", str(error)))
        return findings

    parameters = blueprint.get("parameters", {})
    declared_modes = blueprint.get("modes", {})
    if not declared_modes:
        findings.append(Finding(topic, "modes", "blueprint declares no modes"))
        return findings

    # --- difficulty overrides --------------------------------------------
    for level, override in blueprint.get("difficulty_overrides", {}).items():
        where = f"difficulty_overrides.{level}"
        if int(level) not in meta["supported_difficulties"]:
            findings.append(Finding(topic, where, "level is not in supported_difficulties"))
        for name, patch in override.items():
            if name not in parameters:
                findings.append(Finding(topic, where, f"overrides undeclared parameter {name!r}"))
                continue
            if name == "mode":
                unknown = sorted(set(patch.get("values", [])) - set(declared_modes))
                if unknown:
                    findings.append(Finding(topic, where, f"allows undeclared mode(s) {unknown}"))

    mode_values = parameters.get("mode", {}).get("values", [])
    orphans = sorted(set(declared_modes) - set(mode_values))
    if mode_values and orphans:
        findings.append(
            Finding(topic, "parameters.mode", f"mode(s) {orphans} are defined but never selectable")
        )

    # --- global constraints ----------------------------------------------
    for expression in blueprint.get("constraints", []):
        findings += _check_expression(topic, "constraints", expression, parameters)

    for mode, config in declared_modes.items():
        findings += _check_mode(topic, blueprint, mode, config)

    return findings


def lint_all() -> list[Finding]:
    findings: list[Finding] = []
    for topic in list_topics():
        findings += lint_topic(topic)
    return findings


def main() -> int:
    findings = lint_all()
    topics = list_topics()

    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level != "error"]

    for finding in errors + warnings:
        print(finding)

    affected = len({f.topic for f in findings})
    print(
        f"\n{len(errors)} error(s), {len(warnings)} warning(s) "
        f"across {affected} of {len(topics)} blueprints"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
