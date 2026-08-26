"""The higher-degree substitution blueprint must generate end-to-end, exactly.

This blueprint builds every equation BACKWARDS from integer x values, so the
strongest thing that can be asserted is the round trip: take the coefficients the
student is actually shown, evaluate that polynomial over the integers, and check
its real roots are precisely the answer key. `test_displayed_equation_has_exactly_the_claimed_roots`
does that for all five structures, which is the one check that cannot pass while
the template and `higher_degree.py` disagree about which equation was posed.

The rest mirrors test_quad_eq_blueprints: hand-computed spot checks, the
identity-hash property, option-slate publishability, and one guard specific to
this blueprint -- `//` must never appear in an expression, because `_eval` treats
it as a comment delimiter and would silently truncate the formula.

Pure math -- no LLM, no DB.
"""

import random
import re

import pytest

from agents_and_engine import answer_modules, higher_degree
from agents_and_engine.math_engine import (
    available_topics,
    build_solution,
    compute_content_hash,
    generate_math_spec,
    load_blueprint,
    render_constraints,
    _abs_number_set,
)
from agents_and_engine.math_ques_types import compute_answer_key

TOPIC = "higher_degree_equations_substitution"
ANSWER_TYPE = "higher_degree_substitution"
N_OPTIONS = 4

POWER = "power_substitution"
LINEAR_SQUARE = "linear_square_substitution"
REPEATED = "repeated_quadratic_product"
PAIRED = "paired_products"
HIDDEN = "hidden_shift_substitution"

#: Which parameters each structure actually consumes. Everything outside its own
#: group must be pinned by the blueprint's `derived` block -- see
#: `test_inactive_parameters_are_canonicalised` for why that matters.
GROUPS = {
    POWER: ["power", "leading_t", "x_base_1", "x_base_2", "t_sign_2"],
    LINEAR_SQUARE: ["leading_t", "inner_a", "inner_b", "x_base_1", "x_base_2", "t_sign_2"],
    REPEATED: ["quad_p", "quad_q", "x_base_1", "x_base_2", "second_branch", "gap"],
    HIDDEN: ["quad_p", "x_base_1", "x_base_2", "second_branch", "gap"],
    PAIRED: ["pair_center", "pair_offset_1", "pair_offset_2", "pair_k"],
}

BASE = dict(
    structure=POWER, query_type="solve", power=2, leading_t=1,
    x_base_1=1, x_base_2=2, t_sign_2=1, inner_a=1, inner_b=0,
    quad_p=0, quad_q=0, second_branch="real", gap=1,
    pair_center=0, pair_offset_1=1, pair_offset_2=2, pair_k=3,
    w_1=1, w_2=2, q_1=1, q_2=4, q_none=0,
    t_1=1, t_2=4, red_b=-5, red_c=4, delta=-5, rhs=-4,
)


@pytest.fixture(scope="module")
def blueprint():
    return load_blueprint(TOPIC)


def spec_of(**overrides):
    return {**BASE, **overrides}


def test_blueprint_is_wired(blueprint):
    assert TOPIC in available_topics()
    assert blueprint["topic"] == TOPIC
    assert blueprint["answer"]["type"] == ANSWER_TYPE
    assert blueprint["constraints_template"] == f"{TOPIC}.j2"


def test_registry_owns_the_answer_type():
    """The three dispatch sites now share one lookup; it must resolve this type."""
    assert answer_modules.module_for(ANSWER_TYPE) is higher_degree
    assert ANSWER_TYPE in answer_modules.ANSWER_TYPES
    assert answer_modules.module_for("static_choice") is None


# =========================================================================== #
# hand-computed spot checks
# =========================================================================== #
@pytest.mark.parametrize(
    "expected, overrides",
    [
        # 2x^4 - 6x^2 - 8 = 0  ->  2t^2 - 6t - 8 = 0  ->  t = 4, t = -1.
        # t = -1 is a square, so it is discarded; t = 4 gives x = +-2.
        (r"-2;\ 2", dict(structure=POWER, power=2, leading_t=2, t_1=4, t_2=-1)),
        # Both t admissible -> four roots, the case that exercises the +- twice.
        (r"-2;\ -1;\ 1;\ 2", dict(structure=POWER, power=2, t_1=1, t_2=4)),
        # x^6 - ... : an ODD power, so t = -27 is admissible and gives one root.
        (r"-3;\ 2", dict(structure=POWER, power=3, t_1=8, t_2=-27)),
        # (x-2)^4 - 16(x-2)^2 - 225 = 0 -> t = 25, t = -9. Only t = 25 survives:
        # (x-2)^2 = 25 -> x = 7 or x = -3.
        (r"-3;\ 7", dict(structure=LINEAR_SQUARE, inner_a=1, inner_b=-2, t_1=25, t_2=-9)),
        # inner_a = 2: back-substitution divides, so the roots are (2 +- 4)/2.
        (r"-1;\ 3", dict(structure=LINEAR_SQUARE, inner_a=2, inner_b=-2, t_1=16, t_2=-4)),
        # (x^2-3x+10)(x^2-3x-8) = 40 -> t(t-18) = 40 -> t = 20, t = -2.
        # t = 20: x^2-3x-10 = 0 -> x = 5, -2.   t = -2: D = 9-48 < 0, nothing.
        (r"-2;\ 5", dict(structure=REPEATED, quad_p=-3, quad_q=10, t_1=20, t_2=-2,
                         delta=-18, rhs=40)),
        # x^4+4x^3-6x^2-20x = 75 -> t = x^2+2x -> t(t-10) = 75 -> t = 15, t = -5.
        (r"-5;\ 3", dict(structure=HIDDEN, quad_p=2, quad_q=0, t_1=15, t_2=-5,
                         delta=-10, rhs=75)),
        # x(x-2)(x-4)(x-6) = 945.  Outer pair -> t-9, inner pair -> t-1 with
        # t = (x-3)^2.  (t-9)(t-1) = 945 -> t = 36, t = -26; only t = 36 counts.
        (r"-3;\ 9", dict(structure=PAIRED, pair_center=3, pair_offset_1=1,
                         pair_offset_2=3, pair_k=6, t_1=36, t_2=-26, rhs=945)),
        # Same equation, asked two other ways: 9 + (-3) = 6 and 9 * (-3) = -27.
        ("6", dict(structure=PAIRED, query_type="root_sum", pair_center=3,
                   pair_offset_1=1, pair_offset_2=3, pair_k=6, t_1=36, t_2=-26, rhs=945)),
        ("-27", dict(structure=PAIRED, query_type="root_product", pair_center=3,
                     pair_offset_1=1, pair_offset_2=3, pair_k=6, t_1=36, t_2=-26, rhs=945)),
        # Four real roots -2, 1, ... : sum over BOTH branches, not just the first.
        ("6", dict(structure=REPEATED, query_type="root_sum", quad_p=-3, quad_q=10,
                   t_1=20, t_2=14, delta=-34, rhs=-280)),
    ],
)
def test_exact_answer(expected, overrides):
    assert higher_degree.compute_answer(ANSWER_TYPE, spec_of(**overrides)) == expected


def test_odd_power_keeps_a_negative_t_but_even_power_discards_it():
    """The two opposite errors about the same sign are what these structures teach."""
    odd = spec_of(structure=POWER, power=3, t_1=8, t_2=-27)
    even = spec_of(structure=POWER, power=2, t_1=4, t_2=-9)
    assert higher_degree._back_substitute(odd, -27) == [-3]
    assert higher_degree._back_substitute(even, -9) == []


def test_inexact_back_substitution_raises_rather_than_rounding():
    """A root that is not an integer means a constraint broke -- fail loudly.

    Silently returning an approximation here would ship a question whose answer
    key is wrong by a rounding error, which is the one failure mode that survives
    every other check in this file.
    """
    # t = 5 is not a perfect square, so (x-0)^2 = 5 has no integer solution.
    bad = spec_of(structure=PAIRED, pair_center=0, t_1=5, t_2=-1)
    assert higher_degree._back_substitute(bad, 5) is None
    with pytest.raises(ValueError, match="constraints should have excluded"):
        higher_degree.compute_answer(ANSWER_TYPE, bad)


# =========================================================================== #
# the round trip: what is SHOWN must have the roots that are CLAIMED
# =========================================================================== #
def _displayed_value(spec, x):
    """Evaluate the polynomial the template prints, as LHS - RHS.

    Written from the template's printed form rather than from the solver, so that
    a template drifting away from `higher_degree.py` shows up as a failure here.
    """
    structure = spec["structure"]
    if structure == POWER:
        p = spec["power"]
        return spec["leading_t"] * x ** (2 * p) + spec["red_b"] * x ** p + spec["red_c"]
    if structure == LINEAR_SQUARE:
        u = spec["inner_a"] * x + spec["inner_b"]
        return spec["leading_t"] * u ** 4 + spec["red_b"] * u ** 2 + spec["red_c"]
    if structure == REPEATED:
        q = x * x + spec["quad_p"] * x + spec["quad_q"]
        return q * (q + spec["delta"]) - spec["rhs"]
    if structure == PAIRED:
        c, o1, o2 = spec["pair_center"], spec["pair_offset_1"], spec["pair_offset_2"]
        product = 1
        for root in (c - o1, c + o1, c - o2, c + o2):
            product *= x - root
        return product - spec["rhs"]
    p, delta = spec["quad_p"], spec["delta"]
    return (x ** 4 + 2 * p * x ** 3 + (p * p + delta) * x * x
            + delta * p * x - spec["rhs"])


@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_displayed_equation_has_exactly_the_claimed_roots(blueprint, difficulty):
    """Substitute back into the equation the student sees.

    Every root this blueprint can produce is an integer by construction, so
    scanning the integers is a complete search: any x that zeroes the polynomial
    must be in the answer, and every x in the answer must zero it.
    """
    seen = set()
    for seed in range(200):
        random.seed(seed * 3 + difficulty)
        spec = generate_math_spec(blueprint, difficulty)
        seen.add(spec["structure"])
        roots = set(higher_degree._real_roots(spec))
        found = {x for x in range(-40, 41) if _displayed_value(spec, x) == 0}
        assert found == roots, (spec["structure"], spec, sorted(found), sorted(roots))
    assert seen, "no structures generated"


@pytest.mark.parametrize("structure", sorted(GROUPS))
def test_reduced_equation_identity_holds(blueprint, structure):
    """t_1 and t_2 must really be the roots of the reduced equation in t.

    That identity is what lets the template print coefficients while the solver
    reads roots -- the two only agree because both are true of the same spec.
    """
    checked = 0
    for difficulty in (1, 2, 3):
        for seed in range(300):
            random.seed(seed * 5 + difficulty)
            spec = generate_math_spec(blueprint, difficulty)
            if spec["structure"] != structure:
                continue
            checked += 1
            for t in (spec["t_1"], spec["t_2"]):
                if structure in (POWER, LINEAR_SQUARE):
                    value = spec["leading_t"] * t * t + spec["red_b"] * t + spec["red_c"]
                elif structure == PAIRED:
                    o1, o2 = spec["pair_offset_1"], spec["pair_offset_2"]
                    value = (t - o1 * o1) * (t - o2 * o2) - spec["rhs"]
                else:
                    value = t * (t + spec["delta"]) - spec["rhs"]
                assert value == 0, (structure, t, spec)
    assert checked > 20, f"only {checked} rolls of {structure}; sample too thin"


# =========================================================================== #
# pipeline invariants
# =========================================================================== #
@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_generates_end_to_end(blueprint, difficulty):
    expected = {
        1: {POWER},
        2: {POWER, LINEAR_SQUARE, REPEATED},
        3: {REPEATED, PAIRED, HIDDEN},
    }[difficulty]
    declared = {d["id"] for d in blueprint["distractors"]}
    for seed in range(200):
        random.seed(seed * 11 + difficulty)
        spec = generate_math_spec(blueprint, difficulty)
        assert spec["structure"] in expected, spec
        if difficulty < 3:
            assert spec["query_type"] == "solve", spec

        answer = compute_answer_key(blueprint, spec)
        assert isinstance(answer, str) and answer

        payload = render_constraints(blueprint, spec)
        assert "{{" not in payload and "{%" not in payload

        solution = build_solution(blueprint, spec, answer)
        assert solution["answer_type"] == ANSWER_TYPE
        assert solution["steps"]

        options = higher_degree.build_options(ANSWER_TYPE, spec, n_options=N_OPTIONS)
        assert len(options) == N_OPTIONS, (spec, options)
        assert sum(o["is_correct"] for o in options) == 1
        texts = [o["text"] for o in options]
        assert len(set(texts)) == len(texts), options
        assert answer in texts
        assert {o["misconception"] for o in options if o["misconception"]} <= declared


@pytest.mark.parametrize("structure", sorted(GROUPS))
def test_inactive_parameters_are_canonicalised(blueprint, structure):
    """A parameter this structure does not use must hold ONE value across rolls.

    `compute_content_hash` hashes the whole spec, so a parameter left rolling in
    a branch that ignores it fabricates distinct hashes for identical problems and
    bank dedup silently stops working. `difficulty_overrides` cannot fix it here:
    bands 2 and 3 each hold three structures.
    """
    inactive = ({name for group, names in GROUPS.items() if group != structure
                 for name in names} - set(GROUPS[structure]))
    seen = {name: set() for name in inactive}
    rolls = 0
    for difficulty in (1, 2, 3):
        for seed in range(300):
            random.seed(seed * 5 + difficulty)
            spec = generate_math_spec(blueprint, difficulty)
            if spec["structure"] != structure:
                continue
            rolls += 1
            for name in inactive:
                seen[name].add(spec[name])
    assert rolls > 20, f"only {rolls} rolls of {structure}; sample too thin to conclude"
    varying = {name: values for name, values in seen.items() if len(values) > 1}
    assert not varying, f"{structure} leaves these unused params rolling: {varying}"


def test_one_problem_one_hash(blueprint):
    by_problem = {}
    for difficulty in (1, 2, 3):
        for seed in range(400):
            random.seed(seed * 17 + difficulty)
            spec = generate_math_spec(blueprint, difficulty)
            answer = compute_answer_key(blueprint, spec)
            key = (spec["structure"], spec["query_type"], spec["t_1"], spec["t_2"],
                   spec["leading_t"], spec["power"], spec["inner_a"], spec["inner_b"],
                   spec["quad_p"], spec["quad_q"], spec["pair_center"],
                   spec["pair_offset_1"], spec["pair_offset_2"], answer)
            by_problem.setdefault(key, set()).add(
                compute_content_hash(TOPIC, spec, "ru")
            )
    collisions = {k: v for k, v in by_problem.items() if len(v) > 1}
    assert not collisions, f"same problem, different hashes: {list(collisions)[:3]}"


def test_structure_specific_misconception_gets_a_slot(blueprint):
    """A structure's signature error must survive slot competition.

    `build_options` fills four slots and stops, so this is what keeps the generic
    slips (which apply everywhere) from crowding out the error the item exists to
    catch. The keys are (structure, precondition) because two of these describe a
    sign mistake that only arises when a value of t was actually inadmissible.
    """
    required = {
        PAIRED: "rhs_treated_as_zero",
        LINEAR_SQUARE: "forgot_square_domain",
    }
    hit = {key: 0 for key in required}
    for difficulty in (2, 3):
        for seed in range(400):
            random.seed(seed * 23 + difficulty)
            spec = generate_math_spec(blueprint, difficulty)
            structure = spec["structure"]
            if structure not in required:
                continue
            if structure == LINEAR_SQUARE and min(spec["t_1"], spec["t_2"]) >= 0:
                continue  # nothing was inadmissible, so there is no error to make
            hit[structure] += 1
            options = higher_degree.build_options(ANSWER_TYPE, spec, n_options=N_OPTIONS)
            tags = {o["misconception"] for o in options}
            assert required[structure] in tags, (spec, options)
    assert all(hit.values()), f"some structures never generated: {hit}"


def test_every_declared_misconception_fires_somewhere(blueprint):
    fired = set()
    for difficulty in (1, 2, 3):
        for seed in range(400):
            random.seed(seed * 29 + difficulty)
            spec = generate_math_spec(blueprint, difficulty)
            options = higher_degree.build_options(ANSWER_TYPE, spec, n_options=N_OPTIONS)
            fired |= {o["misconception"] for o in options if o["misconception"]}
    declared = {d["id"] for d in blueprint["distractors"]}
    assert declared - fired == set(), f"declared but never fires: {declared - fired}"


def test_lost_plus_minus_is_withheld_where_there_is_no_plus_minus():
    """An odd power is one-to-one, so this belief yields the CORRECT answer.

    Letting it through and relying on dedup would hide the fact that the
    distractor does not apply; returning None says so.
    """
    odd = spec_of(structure=POWER, power=3, t_1=8, t_2=27)
    even = spec_of(structure=POWER, power=2, t_1=4, t_2=9)
    assert higher_degree._plus_branch_only(odd) is None
    assert higher_degree._plus_branch_only(even) == [2, 3]


def test_dropped_distractors_are_documented_and_absent(blueprint):
    """Three declared misconceptions lead to a dead end, not to a wrong number.

    They are kept in the blueprint with the reason rather than deleted, so the
    next person does not re-add them.
    """
    dropped = {d["id"] for d in blueprint["_dropped_distractors"]}
    assert dropped == {"wrong_power_substitution", "bad_grouping", "full_expansion"}
    assert dropped & {d["id"] for d in blueprint["distractors"]} == set()
    for entry in blueprint["_dropped_distractors"]:
        assert entry["why_dropped"].strip()


# =========================================================================== #
# blueprint-dialect guards
# =========================================================================== #
def _expression_strings(blueprint):
    return list(blueprint["derived"].values()) + list(blueprint["constraints"])


def test_no_floor_division_in_any_expression(blueprint):
    """`_eval` strips everything after `//`, treating it as a comment.

    So a formula containing floor division is not a syntax error -- it silently
    evaluates a TRUNCATED expression, which is far worse. This blueprint deals
    with halves and quarters in several places and was written to avoid `//`
    entirely; this test keeps it that way.
    """
    offenders = [expr for expr in _expression_strings(blueprint) if "//" in expr]
    assert not offenders, f"floor division would be silently truncated by _eval: {offenders}"


def test_derived_block_holds_only_expressions(blueprint):
    """Every `derived` value is eval'd, so prose notes must live elsewhere."""
    for name, expr in blueprint["derived"].items():
        assert not name.startswith("_"), f"{name} would be evaluated as an expression"
        compile(expr.split("//")[0].replace("->", " or "), "<derived>", "eval")


def test_student_facing_section_shows_only_the_equation(blueprint):
    """The problem half may contain the coefficients -- and nothing else.

    Stated as a whitelist of the numbers the template is allowed to print, rather
    than as "the answer must not appear": a root can legitimately coincide with a
    coefficient, and testing the weaker property would fail on honest arithmetic.
    """
    for difficulty in (1, 2, 3):
        for seed in range(120):
            random.seed(seed * 31 + difficulty)
            spec = generate_math_spec(blueprint, difficulty)
            student = render_constraints(blueprint, spec).split("=== INTERNAL")[0]
            structure = spec["structure"]
            if structure == POWER:
                allowed = {spec["leading_t"], abs(spec["red_b"]), abs(spec["red_c"]),
                           spec["power"], 2 * spec["power"]}
            elif structure == LINEAR_SQUARE:
                allowed = {spec["leading_t"], abs(spec["red_b"]), abs(spec["red_c"]),
                           spec["inner_a"], abs(spec["inner_b"]), 2, 4}
            elif structure == REPEATED:
                allowed = {abs(spec["quad_p"]), abs(spec["quad_q"]),
                           abs(spec["quad_q"] + spec["delta"]), abs(spec["rhs"]), 2}
            elif structure == PAIRED:
                c, o1, o2 = spec["pair_center"], spec["pair_offset_1"], spec["pair_offset_2"]
                allowed = {abs(c - o1), abs(c + o1), abs(c - o2), abs(c + o2),
                           abs(spec["rhs"])}
            else:
                p, delta = spec["quad_p"], spec["delta"]
                allowed = {abs(2 * p), abs(p * p + delta), abs(delta * p),
                           abs(spec["rhs"]), 2, 3, 4}
            shown = _abs_number_set(student)
            assert shown <= allowed | {0, 1}, (structure, spec, sorted(shown - allowed))
            # The auxiliary variable must never be named to the student.
            assert not re.search(r"\bt\s*=", student), student
