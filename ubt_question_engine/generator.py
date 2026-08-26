"""
Question assembly for the UBT question engine.

The one public entry point of the deterministic half of the system. Everything
below it is a pure function of `(topic, difficulty, seed)`; everything above it
(localizer, critic, publisher) only ever decorates or stores what comes out of
here.

    generate_question("ubt_roots_expressions", difficulty=2, seed=41)

The whole file exists to keep one promise, the one architecture doc sections 14
and 15 rest on:

    topic + difficulty + seed fully determine the question, its answer, its five
    options, and the order they appear in.

Not "usually". Always -- which is what makes a stored question auditable, a test
deterministic, and a teacher's bug report reproducible. Every rule kept here
serves that sentence: resolve inputs before using them, record what was
resolved, build a fresh RNG per attempt, never reorder the pipeline calls.

Owns: input resolution, the retry loop, statement rendering, the content hash,
and packing the result into EngineState. Does not own: randomness policy
(params.py), mathematics (math_functions.py), disk (loader.py), language
(localize.py), or the database (publisher.py).
"""

from __future__ import annotations

import hashlib
import json
import random
import string
from typing import Any

import sympy

from . import math_functions as M
from . import params
from .latex import tidy
from .loader import load_blueprint, topic_meta
from .params import GenerationError
from .state import Choice, EngineState, SolutionRecord

# Not the same knob as params.MAX_ATTEMPTS. That one re-rolls numbers inside a
# single question, hunting for a draw that satisfies a narrow constraint window.
# This one throws a whole finished draw away because its distractor pool
# collapsed -- measured at 43 events in 2357 questions, about 1.8%. Ten attempts
# takes the residual to 1.8%^10, which is zero in practice. A topic that still
# exhausts ten has a broken pool, and more attempts would only hide it.
MAX_QUESTION_ATTEMPTS = 10

# The full seed space. Wide enough that collisions do not matter, small enough to
# store in any integer column and to read back out of a log line.
SEED_SPACE = 2**31


def _format_values(context: dict[str, Any]) -> dict[str, Any]:
    """The template namespace: math values, LaTeX twins, and int coercion.

    SymPy integers cannot be format-specced. `format(sympy.Integer(-3), "+d")`
    raises TypeError, and 85 template fields across 21 topics use exactly that
    spec to write signed coefficients (`{a}x^2{b:+d}x{c:+d}`). Coercing Integer
    back to a plain Python int is what makes those templates render at all, and
    it changes nothing for the unspecced fields: str(int) == str(Integer).

    Only Integer is coerced. A Rational or a radical formatted bare would print
    "3/2" or "sqrt(2)" into LaTeX; those fields are authored as `{name_latex}`
    instead, which template_context supplies.
    """
    values = M.template_context(context)
    return {
        name: int(value) if isinstance(value, sympy.Integer) else value
        for name, value in values.items()
    }


def render_statement(
    blueprint: dict[str, Any],
    mode: str,
    context: dict[str, Any],
) -> str:
    """The mathematics of the statement, as a LaTeX string.

    Returns "" for the 30 recall modes whose `question.latex` is empty by design
    ("How many faces does a tetrahedron have?" has no formula to display); their
    instruction carries the whole question.
    """
    template = blueprint["modes"][mode].get("question", {}).get("latex", "")
    if not template.strip():
        return ""
    return _render(template, context, blueprint, mode, "question.latex")


def render_text_context(
    blueprint: dict[str, Any],
    mode: str,
    context: dict[str, Any],
) -> dict[str, str]:
    """Named values a word problem must weave into prose, already rendered.

    Two modes in the collection use this (ubt_progression_word_problems). The
    Contextualizer needs `{"a1": "3", "d": "5", "n": "12"}` as words rather than
    a formula block, and must not be trusted to pull them out of LaTeX itself.
    """
    block = blueprint["modes"][mode].get("question", {}).get("text_context", {})
    return {
        name: _render(template, context, blueprint, mode, f"text_context.{name}")
        for name, template in block.items()
    }


def _render(
    template: str,
    context: dict[str, Any],
    blueprint: dict[str, Any],
    mode: str,
    where: str,
) -> str:
    """str.format with an error message that names the blueprint.

    A missing field is a RuntimeError, deliberately not a GenerationError: the
    template is wrong for every possible draw, so the retry loop must not catch
    it and burn ten attempts on the identical failure before reporting
    "exhausted". Failing on the first occurrence, loudly, is the point.
    """
    values = _format_values(context)
    try:
        # tidy() runs after format() and never before: the signs it repairs live
        # inside `{b:+d}` placeholders until format has spliced them in.
        return tidy(template.format(**values))
    except KeyError as error:
        raise RuntimeError(
            f"{blueprint.get('topic')!r}/{mode} {where}: template references "
            f"{error.args[0]!r}, which is neither a parameter nor a derived "
            f"value. Available: {sorted(context)}."
        ) from error
    except (TypeError, ValueError) as error:
        # Almost always an integer spec meeting a non-integer value. Python's own
        # message ("unsupported format string passed to Rational.__format__")
        # names neither the field nor the topic, so the culprit is identified
        # here -- in the error path only, costing nothing on the happy path.
        raise RuntimeError(
            f"{blueprint.get('topic')!r}/{mode} {where}: "
            f"{_diagnose_format(template, values)} ({error})"
        ) from error


def _diagnose_format(template: str, values: dict[str, Any]) -> str:
    """Name the field whose value cannot satisfy its format spec."""
    for _, field, spec, _ in string.Formatter().parse(template):
        if not field or not spec:
            continue
        value = values.get(field)
        if spec.endswith("d") and not isinstance(value, int):
            return (
                f"field {{{field}:{spec}}} demands an integer but the value is "
                f"{value!r} ({type(value).__name__}). Either the template spec "
                f"or the blueprint's parameter range is wrong."
            )
    return f"template {template!r} could not be formatted"


def _jsonable(value: Any) -> Any:
    """One value, in a form Question.solution can actually store.

    No SymPy object is JSON-serialisable -- not Integer, not Rational, not
    FiniteSet. Since solution is a JSONField, the record is written as strings
    and re-derived from the seed rather than parsed back. The stored values are
    for a human reading the row; the seed is what reproduces the item.
    """
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def content_hash(
    topic: str,
    mode: str,
    difficulty: int,
    parameters: dict[str, Any],
) -> str:
    """A stable fingerprint of what makes two questions the same question.

    sha256 of the inputs, not Python's hash(): PYTHONHASHSEED randomises str
    hashing per process, so a hash-based fingerprint would stop matching rows
    written yesterday.

    Deliberately excludes the seed and the option order. Two different seeds that
    happen to roll the same parameters produce the same item and must collide --
    that collision is exactly what Question.content_hash is unique for. Shuffle
    order is presentation, not content.
    """
    payload = json.dumps(
        {
            "topic": topic,
            "mode": mode,
            "difficulty": difficulty,
            "parameters": {k: _jsonable(v) for k, v in sorted(parameters.items())},
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _verify(choices: list[Choice], topic: str, mode: str) -> None:
    """The structural guarantees every blueprint's `validation` block declares.

    Four keys appear in all 58 blueprints -- choice_count, single_correct_answer,
    unique_choices, reject_undefined_choices -- and build_choices already
    enforces them upstream. Re-checking here is cheap and catches the one case it
    cannot: two symbolically distinct values that render to the same LaTeX. That
    is draw-dependent, so it raises GenerationError and the loop re-rolls.

    The other 40 validation keys (respect_log_domain, validate_derivatives, ...)
    are authoring notes, not machine checks; the linter reads them, this does not.
    """
    if len(choices) != M.CHOICE_COUNT:
        raise GenerationError(
            f"{topic!r}/{mode}: {len(choices)} choices, expected {M.CHOICE_COUNT}."
        )
    if sum(choice["is_correct"] for choice in choices) != 1:
        raise GenerationError(f"{topic!r}/{mode}: not exactly one correct option.")

    rendered = [choice["latex"] for choice in choices]
    if any(not text.strip() for text in rendered):
        raise GenerationError(f"{topic!r}/{mode}: an option rendered to nothing.")
    if len(set(rendered)) != len(rendered):
        raise GenerationError(
            f"{topic!r}/{mode}: two options rendered identically: {rendered}."
        )


def generate_question(
    topic: str,
    difficulty: int | None = None,
    seed: int | None = None,
    max_attempts: int = MAX_QUESTION_ATTEMPTS,
) -> EngineState:
    """Build one complete, language-independent question.

    Returns the generator's share of EngineState (see state.py): mode, values,
    statement, answer, five options, the reproduction record and the dedup hash.
    `text` is deliberately absent -- the statement is still English blueprint
    instruction, and localisation is a later, separate stage.

    `difficulty` defaults to the blueprint's own default; only 23 of the 58
    topics support all three levels, so there is no sane engine-wide constant to
    fall back on. `seed` defaults to a fresh draw, which is then recorded.
    """
    meta = topic_meta(topic)
    blueprint = load_blueprint(topic)

    # --- 1. resolve and validate difficulty ------------------------------
    if difficulty is None:
        difficulty = meta["default_difficulty"]
    if difficulty not in meta["supported_difficulties"]:
        # Caught at the boundary on purpose. Left to fall through, this surfaces
        # deeper down as a complaint about the parameter schema, which describes
        # the blueprint rather than the caller's actual mistake.
        raise GenerationError(
            f"Topic {topic!r} does not support difficulty {difficulty}; "
            f"supported: {meta['supported_difficulties']}."
        )

    # --- 2. resolve and record the seed ----------------------------------
    # Drawn into a variable, then used only from that variable. The shortcut --
    # letting the global `random` supply the numbers when no seed is given --
    # produces a question nobody can ever reproduce, which is the one failure
    # this whole module is built to prevent.
    if seed is None:
        seed = random.randrange(SEED_SPACE)

    # --- 3. the retry loop -----------------------------------------------
    failures: list[str] = []
    for attempt in range(max_attempts):
        # A fresh RNG per attempt, keyed by seed + attempt. Reusing one RNG
        # across attempts would also produce different numbers, but attempt n
        # would then only be reachable by replaying attempts 0..n-1 in order:
        # reproducing a question would mean replaying its failure history. This
        # way each attempt is a pure function of two recorded integers.
        rng = random.Random(seed + attempt)

        try:
            # Fixed order, and it is the seed contract itself. choose_mode and
            # build_choices both consume draws; the two middle calls are pure.
            # Moving any of them would make every seed already in the question
            # bank reproduce a different item.
            mode = params.choose_mode(blueprint, difficulty, rng)
            values = params.roll(blueprint, mode, difficulty, rng)
            context = M.build_context(blueprint, mode, values)
            correct = M.compute_answer(blueprint, mode, context)
            choices = M.build_choices(blueprint, mode, context, correct, rng)
            _verify(choices, topic, mode)
        except GenerationError as error:
            # Only GenerationError. A KeyError or TypeError here means this code
            # is wrong, and catching it would turn a bug into ten silent retries
            # followed by a misleading "could not generate" report.
            failures.append(f"attempt {attempt}: {error}")
            continue

        return _assemble(
            blueprint=blueprint,
            meta=meta,
            mode=mode,
            difficulty=difficulty,
            seed=seed,
            values=values,
            context=context,
            correct=correct,
            choices=choices,
        )

    # --- 4. give up, but say why -----------------------------------------
    # The seed is the most useful thing in this message: it turns "it failed in
    # production" into a single reproducing call. The per-attempt reasons
    # separate a genuinely collapsed pool (same mode, same count, every attempt)
    # from bad luck.
    raise GenerationError(
        f"{topic!r} at difficulty {difficulty}: no valid question in "
        f"{max_attempts} attempts (seed {seed}).\n" + "\n".join(failures)
    )


def _assemble(
    *,
    blueprint: dict[str, Any],
    meta: dict[str, Any],
    mode: str,
    difficulty: int,
    seed: int,
    values: dict[str, Any],
    context: dict[str, Any],
    correct: Any,
    choices: list[Choice],
) -> EngineState:
    """Pack a successful draw into EngineState. No mathematics, no randomness."""
    topic = meta["topic"]
    question = blueprint["modes"][mode].get("question", {})
    answer_expression = blueprint["answer"]["expression"][mode]
    answer_latex = M.render(correct, blueprint)

    # Only what the mode derived, not the rolled parameters build_context copied
    # in alongside them -- the record separates "what was drawn" from "what was
    # computed", and merging them loses that.
    derived = {
        name: context[name]
        for name in blueprint["modes"][mode].get("derived", {})
        if name in context
    }

    solution: SolutionRecord = {
        "topic": topic,
        "mode": mode,
        "difficulty": difficulty,  # type: ignore[typeddict-item]
        "seed": seed,
        "parameters": {k: _jsonable(v) for k, v in values.items()},
        "derived": {k: _jsonable(v) for k, v in derived.items()},
        "answer_expression": answer_expression,
        "answer_latex": answer_latex,
    }

    state: EngineState = {
        "topic": topic,
        "difficulty": difficulty,  # type: ignore[typeddict-item]
        "seed": seed,
        "mode": mode,
        "display_name": meta["display_name"],
        "parameters": values,
        "derived": derived,
        "instruction": question.get("instruction", ""),
        "latex": render_statement(blueprint, mode, context),
        "answer_expression": answer_expression,
        "answer_latex": answer_latex,
        "answer_options": choices,
        "solution": solution,
        "tag_slug": meta["tag_slug"],
        "tag_name": meta["tag_name"],
        "content_hash": content_hash(topic, mode, difficulty, values),
    }

    labels = blueprint.get("choice_labels")
    if labels:
        state["choice_labels"] = labels

    text_context = render_text_context(blueprint, mode, context)
    if text_context:
        state["text_context"] = text_context

    return state


def regenerate(solution: SolutionRecord) -> EngineState:
    """Rebuild the exact question a stored `Question.solution` came from.

    The point of recording the seed (architecture doc section 15). The stored
    mode and parameters are not replayed -- they are re-derived from the seed and
    must come out the same, so a mismatch here is proof that a blueprint changed
    under a question already sitting in the bank.
    """
    state = generate_question(
        topic=solution["topic"],
        difficulty=solution["difficulty"],
        seed=solution["seed"],
    )

    if state["mode"] != solution["mode"]:
        raise GenerationError(
            f"{solution['topic']!r} seed {solution['seed']} now yields mode "
            f"{state['mode']!r}, recorded as {solution['mode']!r}. The blueprint "
            "changed after this question was published."
        )
    if state["answer_latex"] != solution["answer_latex"]:
        raise GenerationError(
            f"{solution['topic']!r} seed {solution['seed']} now answers "
            f"{state['answer_latex']!r}, recorded as {solution['answer_latex']!r}. "
            "The blueprint changed after this question was published."
        )
    return state
