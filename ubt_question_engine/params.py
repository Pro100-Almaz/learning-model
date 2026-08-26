"""
Parameter generation for the UBT question engine.

Turns a blueprint's declared search space into one concrete draw:

    mode = "extract_square_factor", {"k": 4, "m": 3}

Difficulty gates which mathematical structures may appear (architecture doc
section 3) — it is not a knob for bigger numbers. Constraints reject draws the
blueprint declares invalid, and the whole roll is retried.

This module owns randomness and nothing else: no SymPy, no derived values, no
answers. Everything here is plain Python arithmetic on ints and strings.

Reproducibility (doc section 14) rests on two rules kept throughout:
  * every function takes the caller's `random.Random`; none creates one, and
    the process-global `random` is never touched;
  * draws happen in a fixed order — mode first, then the mode's `uses` in
    declaration order — so a stored seed keeps reproducing its question.
"""

from __future__ import annotations

import ast
import copy
import random
from typing import Any

from .loader import load_blueprint, modes_for_difficulty, topic_meta

# A stuck-detector, not a tuning knob. The tightest constraint set in the topic
# collection (ubt_powers/scientific_divide, which needs coef1 % coef2 == 0)
# still lands far inside this. Reaching it means the blueprint is unsatisfiable,
# so raising beats quietly trying harder.
MAX_ATTEMPTS = 200


# Names mentioned by a constraint string, cached: the same handful of
# expressions is re-checked on every retry of every question.
_CONSTRAINT_NAMES: dict[str, frozenset[str]] = {}


def _constraint_names(expression: str) -> frozenset[str]:
    if expression not in _CONSTRAINT_NAMES:
        tree = ast.parse(expression, mode="eval")
        _CONSTRAINT_NAMES[expression] = frozenset(
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        )
    return _CONSTRAINT_NAMES[expression]


class GenerationError(RuntimeError):
    """A blueprint could not produce a valid draw.

    Signals a broken or over-constrained blueprint, not a bad call: every
    argument the caller passed was legal. Re-raised by generator.py.
    """


def apply_difficulty_overrides(
    blueprint: dict[str, Any],
    difficulty: int,
) -> dict[str, dict[str, Any]]:
    """Return the parameter schema in force at `difficulty`.

    Only the `parameters` block, with the difficulty's patches merged in.
    """
    schema = copy.deepcopy(blueprint.get("parameters", {}))

    # JSON object keys are strings: difficulty_overrides[1] silently misses and
    # would hand back an ungated schema, so every difficulty could draw every
    # mode. That failure produces plausible-looking wrong questions instead of
    # an exception, which makes it the worst bug available in this file.
    override = blueprint.get("difficulty_overrides", {}).get(str(difficulty))
    if override is None:
        topic = blueprint.get("topic", "<unknown>")
        supported = topic_meta(topic)["supported_difficulties"] if topic else []
        raise GenerationError(
            f"Topic {topic!r} has no difficulty {difficulty}; supported: {supported}."
        )

    for name, patch in override.items():
        if name not in schema:
            raise GenerationError(
                f"Topic {blueprint.get('topic')!r} difficulty {difficulty} "
                f"overrides undeclared parameter {name!r}."
            )
        # Key-by-key update, never wholesale replacement: 46 of the overrides in
        # the topic set patch `max` alone, and replacing the spec would strip
        # that parameter of its `type` and `min`.
        schema[name].update(patch)

    return schema


def choose_mode(
    blueprint: dict[str, Any],
    difficulty: int,
    rng: random.Random,
) -> str:
    """Draw the question structure this item will have.

    Kept separate from `roll` so the ordering stays visible: the mode is always
    the first draw off the RNG. If it ever moved after a parameter draw, every
    seed already stored in the question bank would reproduce a different item.
    """
    modes = modes_for_difficulty(blueprint["topic"], difficulty)
    if not modes:
        raise GenerationError(
            f"Topic {blueprint['topic']!r} difficulty {difficulty} allows no modes."
        )
    return rng.choice(modes)


def roll_value(name: str, spec: dict[str, Any], rng: random.Random) -> int | str:
    """Draw one parameter value from its declared space.

    Two types exist across the topic collection: bounded ints and explicit
    choices. `name` is carried only so an unsupported type names the culprit.
    """
    kind = spec.get("type")

    if kind == "int":
        # Both bounds are present in every int spec in the collection; indexing
        # rather than .get() makes a malformed blueprint fail loudly here.
        return rng.randint(spec["min"], spec["max"])

    if kind == "choice":
        return rng.choice(spec["values"])

    raise GenerationError(f"Parameter {name!r} has unsupported type {kind!r}.")


def roll_params(
    schema: dict[str, dict[str, Any]],
    uses: list[str],
    rng: random.Random,
) -> dict[str, Any]:
    """Draw exactly the parameters a mode uses, in declaration order.

    Both halves of that sentence are load-bearing:

    * Only `uses`. ubt_roots_expressions declares nine parameters and its
      simplest mode uses two. Rolling all nine would consume RNG draws for the
      other seven, so adding an unused parameter to a blueprint later would
      silently change the output of every seed already stored.
    * In the given order. sorted() or a set would reorder the draws and break
      the same guarantee.
    """
    values: dict[str, Any] = {}
    for name in uses:
        if name not in schema:
            raise GenerationError(f"Mode uses undeclared parameter {name!r}.")
        values[name] = roll_value(name, schema[name], rng)
    return values


def constraints_pass(expressions: list[str], context: dict[str, Any]) -> bool:
    """True when every constraint holds for this draw.
    Constraints are evaluated with Python, not SymPy: sympify("k != k2") builds
    a symbolic Ne object that is truthy whatever the values are, so every
    constraint would silently pass and the engine would emit sqrt(50)+sqrt(50).

    eval is appropriate here — these strings are the project's own configuration
    files, the builtins are stripped, and the entire constraint vocabulary in
    the topic collection is comparison operators plus one `or`, with no function
    calls at all.

    A constraint naming a parameter this mode does not roll is skipped, not
    failed. Blueprint-level constraints are guards that apply to a parameter
    *when it is used*: ubt_roots_expressions declares `k2 > 0` globally, while
    extract_square_factor rolls only k and m. 88 mode/constraint pairs across
    four blueprints work this way, so this is the declared semantics, not a
    typo — and the linter separately proves every constraint name is a declared
    parameter, so a skip can never be hiding a misspelling.
    """
    for expression in expressions:
        if not _constraint_names(expression) <= context.keys():
            continue
        try:
            if not eval(expression, {"__builtins__": {}}, context):
                # Returning on the first failure is correctness, not speed:
                # ubt_powers/scientific_divide declares ["coef2 != 0",
                # "coef1 % coef2 == 0"], and stopping here is what keeps the
                # second expression from dividing by zero. Do not rewrite this
                # as all(...) over the whole list.
                return False
        except ZeroDivisionError:
            # A blueprint ordered less carefully than the one above should cost
            # a re-roll, not crash the run. Any other exception (NameError from
            # a typo the linter missed) is a real bug and propagates.
            return False
    return True


def roll(
    blueprint: dict[str, Any],
    mode: str,
    difficulty: int,
    rng: random.Random,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Draw a parameter set that satisfies every constraint for `mode`.

    Returns the rolled parameters only — derived values and the answer need
    SymPy and belong to math_functions.
    """
    schema = apply_difficulty_overrides(blueprint, difficulty)

    mode_config = blueprint.get("modes", {}).get(mode)
    if mode_config is None:
        raise GenerationError(
            f"Topic {blueprint.get('topic')!r} has no mode {mode!r}."
        )

    uses: list[str] = mode_config.get("uses", [])
    global_constraints: list[str] = blueprint.get("constraints", [])
    mode_constraints: list[str] = mode_config.get("constraints", [])

    # 30 modes in the collection are pure recall facts with no parameters. With
    # nothing to re-roll, a failing constraint fails identically 200 times, so
    # decide it once and report honestly instead of blaming exhaustion.
    if not uses:
        if constraints_pass(global_constraints, {}) and constraints_pass(
            mode_constraints, {}
        ):
            return {}
        raise GenerationError(
            f"{blueprint.get('topic')!r}/{mode}: mode uses no parameters yet its "
            "constraints fail; the constraints cannot ever be satisfied."
        )

    last_draw: dict[str, Any] = {}
    for _ in range(max_attempts):
        last_draw = roll_params(schema, uses, rng)
        if not constraints_pass(global_constraints, last_draw):
            continue
        if not constraints_pass(mode_constraints, last_draw):
            continue
        return last_draw

    raise GenerationError(
        f"{blueprint.get('topic')!r}/{mode} at difficulty {difficulty}: no draw "
        f"satisfied the constraints in {max_attempts} attempts. "
        f"Constraints: {global_constraints + mode_constraints}. "
        f"Last rejected draw: {last_draw}."
    )


def roll_question_params(
    topic: str,
    difficulty: int,
    rng: random.Random,
) -> tuple[str, dict[str, Any]]:
    """Convenience entry point: load, gate, pick a mode, roll it.

    Returns (mode, parameters). The RNG is consumed in exactly this order —
    mode first, then the mode's `uses` — which is the ordering the seed
    contract depends on.
    """
    blueprint = load_blueprint(topic)
    mode = choose_mode(blueprint, difficulty, rng)
    return mode, roll(blueprint, mode, difficulty, rng)
