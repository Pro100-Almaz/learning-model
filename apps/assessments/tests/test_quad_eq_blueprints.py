"""The quadratic-family blueprints must generate end-to-end, and be exactly right.

Two kinds of check live here:

  * SPOT CHECKS on hand-computed equations. These are the math-integrity tests —
    a quadratic whose roots you can verify on paper, asserted against the exact
    rendered answer. If `quad_eq` ever starts drifting (a float creeping in, a
    surd left unreduced, a sign lost) these fail first and loudest.
  * PIPELINE INVARIANTS over many seeds, mirroring test_static_choice_blueprints:
    the sampler must not crash, the difficulty lever must actually bind, the
    option slate must be publishable, and one equation must map to exactly one
    content_hash.

Pure math -- no LLM, no DB.
"""

import random
from fractions import Fraction

import pytest

from agents_and_engine import quad_eq
from agents_and_engine.math_engine import (
    available_topics,
    build_solution,
    compute_content_hash,
    generate_math_spec,
    load_blueprint,
    render_constraints,
    _abs_number_set,
    _desugar_implication,
    _eval,
)
from agents_and_engine.math_ques_types import compute_answer_key

TOPIC = "quadratic_equations_discriminant"
ANSWER_TYPE = "quadratic_discriminant"

FACTOR_TOPIC = "quadratic_equations_factorization"
FACTOR_ANSWER_TYPE = "quadratic_factorization"

VIETA_TOPIC = "quadratic_equations_vieta"
VIETA_ANSWER_TYPE = "quadratic_vieta"

N_OPTIONS = 4

# Each difficulty band pins exactly one construction; that binding is the whole
# point of moving `difficulty` out of the rolled parameters.
STRUCTURE_BY_DIFFICULTY = {
    1: "monic_integer_roots",
    2: "non_monic_factors",
    3: "controlled_discriminant",
}


@pytest.fixture(scope="module")
def blueprint():
    return load_blueprint(TOPIC)


@pytest.fixture(scope="module")
def factor_blueprint():
    return load_blueprint(FACTOR_TOPIC)


@pytest.fixture(scope="module")
def vieta_blueprint():
    return load_blueprint(VIETA_TOPIC)


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_blueprint_is_discoverable_and_named_after_its_file(blueprint):
    assert TOPIC in available_topics()
    assert blueprint["topic"] == TOPIC  # filename == topic (the #1 alignment)
    assert blueprint["answer"]["type"] == ANSWER_TYPE
    assert blueprint["constraints_template"] == f"{TOPIC}.j2"


# --------------------------------------------------------------------------- #
# Exact answers -- verified by hand, not by re-running the implementation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "a, b, c, query_type, expected",
    [
        # D > 0, perfect square -> two integers.
        (1, 7, 10, "solve", "-5;\\ -2"),
        (1, 0, -4, "solve", "-2;\\ 2"),
        # D > 0, perfect square, a != 1 -> two exact rationals, reduced.
        (20, 19, -6, "solve", "-\\frac{6}{5};\\ \\frac{1}{4}"),
        # D == 0 -> ONE repeated root, not a pair.
        (4, -4, 1, "solve", "\\frac{1}{2}"),
        # D < 0 -> no real roots, stated rather than faked.
        (1, 0, 1, "solve", quad_eq.NO_REAL_ROOTS),
        # D > 0, non-square -> exact surds, denominator cleared where it divides.
        (1, -6, 7, "solve", "3 - \\sqrt{2};\\ 3 + \\sqrt{2}"),
        (2, -4, 1, "solve", "\\frac{2 - \\sqrt{2}}{2};\\ \\frac{2 + \\sqrt{2}}{2}"),
        # sqrt(24) = 2*sqrt(6), then (24 +- 2sqrt6)/6 reduces by 2 to (12 +- sqrt6)/3.
        (3, -24, 46, "solve", "\\frac{12 - \\sqrt{6}}{3};\\ \\frac{12 + \\sqrt{6}}{3}"),
        # A bare +-sqrt keeps its sign outside the fraction.
        (1, 0, -2, "solve", "-\\sqrt{2};\\ \\sqrt{2}"),
        # Counting questions answer in words, sharing the zero-case sentence.
        (1, 7, 10, "number_of_real_roots", "два корня"),
        (4, -4, 1, "number_of_real_roots", "один корень"),
        (1, 0, 1, "number_of_real_roots", quad_eq.NO_REAL_ROOTS),
    ],
)
def test_exact_answer(a, b, c, query_type, expected):
    spec = {"a": a, "b": b, "c": c, "query_type": query_type}
    assert quad_eq.compute_answer(ANSWER_TYPE, spec) == expected


def test_negative_leading_coefficient_is_normalised():
    """-x^2 - 7x - 10 = 0 has the same roots as x^2 + 7x + 10 = 0."""
    negated = {"a": -1, "b": -7, "c": -10, "query_type": "solve"}
    assert quad_eq.compute_answer(ANSWER_TYPE, negated) == "-5;\\ -2"


def test_zero_leading_coefficient_fails_loudly():
    with pytest.raises(ValueError, match="Not a quadratic"):
        quad_eq.compute_answer(ANSWER_TYPE, {"a": 0, "b": 1, "c": 2, "query_type": "solve"})


# --------------------------------------------------------------------------- #
# The expression-language extensions the blueprint leans on
# --------------------------------------------------------------------------- #
def test_implication_desugars_right_associatively():
    assert _desugar_implication("x == 1 -> y == 2") == "(not (x == 1 )) or ( y == 2)"
    # Implication binds loosest, so the whole left side is negated as one unit.
    assert _eval("a == 1 and b == 2 -> c == 3", {"a": 1, "b": 2, "c": 3}) is True
    assert _eval("a == 1 and b == 2 -> c == 3", {"a": 1, "b": 2, "c": 9}) is False
    # A false antecedent makes the implication vacuously true.
    assert _eval("a == 1 -> c == 3", {"a": 5, "c": 9}) is True


def test_eval_exposes_only_the_whitelisted_callables():
    assert _eval("gcd(abs(-12), 18)", {}) == 6
    assert _eval("max(min(3, 5), 1)", {}) == 3
    # __builtins__ stays empty: nothing outside the whitelist is reachable.
    with pytest.raises(NameError):
        _eval("__import__('os').getcwd()", {})
    # A parameter shadows a helper rather than colliding with it.
    assert _eval("min + 1", {"min": 41}) == 42


def test_caret_is_not_redefined_as_exponentiation():
    """`^` must keep meaning XOR -- blueprints spell powers `**`."""
    assert _eval("3 ^ 1", {}) == 2  # XOR, emphatically not 3
    assert _eval("3 ** 2", {}) == 9


# --------------------------------------------------------------------------- #
# Pipeline invariants
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_blueprint_generates_end_to_end(blueprint, difficulty):
    for seed in range(120):
        random.seed(seed)
        spec = generate_math_spec(blueprint, difficulty)

        # The difficulty lever binds: the band picks the construction.
        assert spec["structure"] == STRUCTURE_BY_DIFFICULTY[difficulty], spec
        # `derived` reduced the roll to standard form, and D agrees with it.
        assert spec["D"] == spec["b"] ** 2 - 4 * spec["a"] * spec["c"], spec
        assert spec["a"] != 0, spec

        answer = compute_answer_key(blueprint, spec)
        assert isinstance(answer, str) and answer, spec

        payload = render_constraints(blueprint, spec)
        assert "{{" not in payload and "{%" not in payload, payload

        solution = build_solution(blueprint, spec, answer)
        assert solution["answer_type"] == ANSWER_TYPE
        assert solution["answer_key"] == answer
        assert solution["steps"], spec

        options = quad_eq.build_options(ANSWER_TYPE, spec, n_options=N_OPTIONS)
        # Exactly the shape apps.assessments.services._assert_publishable demands.
        assert len(options) == N_OPTIONS, (spec, options)
        assert sum(o["is_correct"] for o in options) == 1, options
        texts = [o["text"] for o in options]
        assert len(set(texts)) == len(texts), options
        assert answer in texts, (answer, options)
        # Every tagged distractor names a misconception the blueprint documents.
        declared = {d["id"] for d in blueprint["distractors"]}
        assert {o["misconception"] for o in options if o["misconception"]} <= declared


@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_discriminant_case_matches_the_requested_one(blueprint, difficulty):
    """The controlled band must actually deliver the D-sign it advertises."""
    expected_sign = {"zero": 0, "negative": -1, "positive_nonsquare": 1}
    for seed in range(120):
        random.seed(seed)
        spec = generate_math_spec(blueprint, difficulty)
        if spec["structure"] != "controlled_discriminant":
            continue
        want = expected_sign[spec["discriminant_case"]]
        got = (spec["D"] > 0) - (spec["D"] < 0)
        assert got == want, spec
        if spec["discriminant_case"] == "positive_nonsquare":
            root = round(spec["D"] ** 0.5)
            assert root * root != spec["D"], spec  # genuinely irrational roots


def test_one_equation_maps_to_exactly_one_content_hash(blueprint):
    """Two rolls of the same equation must dedup, not double up in the bank.

    Without a canonical ordering, (root_1, root_2) = (-5, -2) and (-2, -5) build
    the identical equation from different specs -- and content_hash reads the
    spec, so the bank would store the same problem twice.
    """
    by_equation: dict[tuple, set] = {}
    for difficulty in (1, 2, 3):
        for seed in range(400):
            random.seed(seed * 7 + difficulty)
            spec = generate_math_spec(blueprint, difficulty)
            key = (spec["a"], spec["b"], spec["c"], spec["query_type"])
            by_equation.setdefault(key, set()).add(
                compute_content_hash(TOPIC, spec, "ru")
            )
    collisions = {k: v for k, v in by_equation.items() if len(v) > 1}
    assert not collisions, f"same equation, different hashes: {list(collisions)[:3]}"


@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_student_facing_section_shows_only_the_coefficients(blueprint, difficulty):
    """The posed problem may print a, b, c -- and nothing else the roll knows.

    Deliberately not phrased as "no root appears in the text": that is not
    provable here, because a root's magnitude legitimately coincides with |c|
    whenever the other root is +-1. What IS provable, and what actually guards
    against a template regression, is the converse — that the only magnitudes
    reaching the student are the three coefficients plus the structural `x^2`
    and `= 0`. A template that started printing root_1, p, or axis_shift would
    break this immediately.
    """
    structural = {0.0, 2.0}  # the exponent in x^2 and the right-hand side
    for seed in range(150):
        random.seed(seed)
        spec = generate_math_spec(blueprint, difficulty)
        student_facing = render_constraints(blueprint, spec).split("=== INTERNAL")[0]
        allowed = structural | {
            float(abs(spec["a"])), float(abs(spec["b"])), float(abs(spec["c"]))
        }
        assert _abs_number_set(student_facing) <= allowed, (spec, student_facing)


# =========================================================================== #
# quadratic_factorization
# =========================================================================== #
def test_factorization_blueprint_is_wired(factor_blueprint):
    assert FACTOR_TOPIC in available_topics()
    assert factor_blueprint["topic"] == FACTOR_TOPIC
    assert factor_blueprint["answer"]["type"] == FACTOR_ANSWER_TYPE
    assert factor_blueprint["constraints_template"] == f"{FACTOR_TOPIC}.j2"


@pytest.mark.parametrize(
    "p, q, r, s, expected",
    [
        # Raw strings throughout: these are LaTeX literals, so one backslash in
        # the source must mean one backslash in the value.
        # Monic: (x - 2)(x + 3) -> roots 2 and -3, reported ascending.
        (1, -2, 1, 3, r"-3;\ 2"),
        # Non-monic: (2x - 1)(3x + 4) -> 1/2 and -4/3, exact, never rounded.
        (2, -1, 3, 4, r"-\frac{4}{3};\ \frac{1}{2}"),
        (3, -2, 5, 7, r"-\frac{7}{5};\ \frac{2}{3}"),
        # Difference of squares still has two roots.
        (1, 5, 1, -5, r"-5;\ 5"),
        # Coincident factors -> ONE repeated root, not a pair.
        (2, 3, 2, 3, r"-\frac{3}{2}"),
    ],
)
def test_factorization_exact_answer(p, q, r, s, expected):
    spec = {"p": p, "q": q, "r": r, "s": s}
    assert quad_eq.compute_answer(FACTOR_ANSWER_TYPE, spec) == expected


def test_forgot_common_factor_is_documented_as_unanswerable(factor_blueprint):
    """Scaling cannot change a root, so that error yields no distinct option.

    It stays recorded in the blueprint (under _dropped_distractors) rather than
    sitting in `distractors` as an id that could never fire.
    """
    declared = {d["id"] for d in factor_blueprint["distractors"]}
    assert "forgot_common_factor" not in declared
    assert "forgot_common_factor" in factor_blueprint["_dropped_distractors"]


@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_factorization_generates_end_to_end(factor_blueprint, difficulty):
    expected_forms = {
        1: {"standard"}, 2: {"standard"},
        3: {"scaled", "both_sides", "repeated_root"},
    }[difficulty]
    for seed in range(150):
        random.seed(seed)
        spec = generate_math_spec(factor_blueprint, difficulty)
        assert spec["presentation_form"] in expected_forms, spec

        # The hidden factors really do factor the standard-form trinomial.
        a, b, c = spec["a"], spec["b"], spec["c"]
        for root in (Fraction(-spec["q"], spec["p"]), Fraction(-spec["s"], spec["r"])):
            assert a * root * root + b * root + c == 0, (spec, root)
        # D is a perfect square by construction -- factoring always succeeds.
        assert spec["D"] == (spec["p"] * spec["s"] - spec["q"] * spec["r"]) ** 2
        root = round(spec["D"] ** 0.5)
        assert root * root == spec["D"], spec

        answer = compute_answer_key(factor_blueprint, spec)
        assert isinstance(answer, str) and answer
        # A repeated factor must present as one root, and only then.
        is_square = spec["p"] == spec["r"] and spec["q"] == spec["s"]
        assert (quad_eq.ROOT_SEP not in answer) == is_square, (spec, answer)

        payload = render_constraints(factor_blueprint, spec)
        assert "{{" not in payload and "{%" not in payload

        solution = build_solution(factor_blueprint, spec, answer)
        assert solution["answer_type"] == FACTOR_ANSWER_TYPE
        assert solution["steps"]

        options = quad_eq.build_options(FACTOR_ANSWER_TYPE, spec, n_options=N_OPTIONS)
        assert len(options) == N_OPTIONS, (spec, options)
        assert sum(o["is_correct"] for o in options) == 1
        texts = [o["text"] for o in options]
        assert len(set(texts)) == len(texts), options
        assert answer in texts
        declared = {d["id"] for d in factor_blueprint["distractors"]}
        assert {o["misconception"] for o in options if o["misconception"]} <= declared


def test_form_specific_misconception_always_gets_a_slot(factor_blueprint):
    """The error a dressed form exists to catch must survive slot competition.

    build_options fills a fixed number of slots and stops, so ordering decides
    whether a `both_sides` item actually tests transposing or just re-tests the
    two universal sign slips.
    """
    required = {"both_sides": "forgot_standardization",
                "repeated_root": "repeated_root_count_error"}
    seen = {form: 0 for form in required}
    for seed in range(400):
        random.seed(seed)
        spec = generate_math_spec(factor_blueprint, 3)
        form = spec["presentation_form"]
        if form not in required:
            continue
        seen[form] += 1
        options = quad_eq.build_options(FACTOR_ANSWER_TYPE, spec, n_options=N_OPTIONS)
        tags = {o["misconception"] for o in options}
        assert required[form] in tags, (spec, options)
    assert all(seen.values()), seen  # both forms were actually exercised


def test_factorization_one_equation_one_hash(factor_blueprint):
    by_equation: dict[tuple, set] = {}
    for difficulty in (1, 2, 3):
        for seed in range(400):
            random.seed(seed * 7 + difficulty)
            spec = generate_math_spec(factor_blueprint, difficulty)
            key = (spec["a"], spec["b"], spec["c"], spec["presentation_form"])
            by_equation.setdefault(key, set()).add(
                compute_content_hash(FACTOR_TOPIC, spec, "ru")
            )
    collisions = {k: v for k, v in by_equation.items() if len(v) > 1}
    assert not collisions, f"same equation, different hashes: {list(collisions)[:3]}"


# =========================================================================== #
# quadratic_vieta
# =========================================================================== #
# The five task types draw on five DISJOINT parameter groups. `difficulty_overrides`
# cannot reach inside a band (d2 holds two task types, d3 two plus three root
# families), so the blueprint canonicalises every inactive parameter in `derived`.
# These groups are what that claim gets tested against below.
VIETA_GROUPS = {
    "sum_product": ["a_scale", "S", "P"],
    "root_expression": ["a_scale", "S", "P", "expression_type"],
    "find_second_root": ["p", "q", "r", "s", "known_root_index"],
    "construct_equation": ["root_family", "root_int_1", "root_int_2", "num_1", "den_1",
                           "num_2", "den_2", "rad_center", "rad_coeff", "rad_base"],
    "construct_from_sum_difference": ["roots_sum", "roots_difference"],
}

VIETA_BASE = dict(
    a_scale=1, S=1, P=1, expression_type="sum_squares", p=1, q=1, r=2, s=1,
    known_root_index=1, root_family="integer", root_int_1=1, root_int_2=2,
    num_1=1, den_1=2, num_2=1, den_2=3, rad_center=1, rad_coeff=1, rad_base=2,
    roots_sum=3, roots_difference=1, a=1, b=0, c=0,
)


def test_vieta_blueprint_is_wired(vieta_blueprint):
    assert VIETA_TOPIC in available_topics()
    assert vieta_blueprint["topic"] == VIETA_TOPIC
    assert vieta_blueprint["answer"]["type"] == VIETA_ANSWER_TYPE
    assert vieta_blueprint["constraints_template"] == f"{VIETA_TOPIC}.j2"


@pytest.mark.parametrize(
    "expected, overrides",
    [
        # 2x^2 - 10x + 12 = 0 has roots 2 and 3, so S = 5 and P = 6 -- read off the
        # coefficients, never by solving.
        (r"x_1 + x_2 = 5;\ x_1 x_2 = 6",
         dict(task_type="sum_product", a=2, b=-10, c=12)),
        # Symmetric expressions over S = 5, P = 6.
        ("13", dict(task_type="root_expression", a=1, b=-5, c=6,
                    expression_type="sum_squares")),                      # S^2 - 2P
        (r"\frac{5}{6}", dict(task_type="root_expression", a=1, b=-5, c=6,
                              expression_type="reciprocal_sum")),         # S/P
        (r"\frac{13}{6}", dict(task_type="root_expression", a=1, b=-5, c=6,
                               expression_type="ratio_sum")),             # (S^2-2P)/P
        ("30", dict(task_type="root_expression", a=1, b=-5, c=6,
                    expression_type="x1_sq_x2_plus_x1_x2_sq")),           # P*S
        # (2x - 1)(3x + 4) = 6x^2 + 5x - 4; given one root, recover the other.
        (r"-\frac{4}{3}", dict(task_type="find_second_root", p=2, q=-1, r=3, s=4,
                               known_root_index=1, a=6, b=5, c=-4)),
        (r"\frac{1}{2}", dict(task_type="find_second_root", p=2, q=-1, r=3, s=4,
                              known_root_index=2, a=6, b=5, c=-4)),
        # Inverse Vieta, one case per root family.
        ("x^2 - 5x + 6 = 0", dict(task_type="construct_equation", root_family="integer",
                                  root_int_1=2, root_int_2=3)),
        # Roots 3/4 and -5/2: monic x^2 + (7/4)x - 15/8, cleared by 8.
        ("8x^2 + 14x - 15 = 0", dict(task_type="construct_equation", root_family="rational",
                                     num_1=-5, den_1=2, num_2=3, den_2=4)),
        # 3 +- 2sqrt5: the radical cancels, S = 6 and P = 9 - 20 = -11.
        ("x^2 - 6x - 11 = 0", dict(task_type="construct_equation",
                                   root_family="conjugate_radical",
                                   rad_center=3, rad_coeff=2, rad_base=5)),
        # S = 10, d = 4 -> roots 7 and 3, both integers.
        ("x^2 - 10x + 21 = 0", dict(task_type="construct_from_sum_difference",
                                    roots_sum=10, roots_difference=4)),
        # S = 3, d = 6 -> roots 9/2 and -3/2: half-integers, so clear by 4.
        ("4x^2 - 12x - 27 = 0", dict(task_type="construct_from_sum_difference",
                                     roots_sum=3, roots_difference=6)),
    ],
)
def test_vieta_exact_answer(expected, overrides):
    assert quad_eq.compute_answer(VIETA_ANSWER_TYPE, {**VIETA_BASE, **overrides}) == expected


def test_constructed_equation_is_reduced():
    """Any multiple of the equation has the same roots; the answer is the smallest.

    A student who reduced correctly must not be marked wrong, so `_integer_equation`
    divides out the common factor rather than emitting whatever the denominators
    happened to produce.
    """
    # Roots 1 and 2 would clear to 2x^2 - 6x + 4 before reduction.
    assert quad_eq._integer_equation(Fraction(1), Fraction(2)) == (1, -3, 2)
    # Roots 1/2 and 1/3 -> 6x^2 - 5x + 1, already primitive.
    assert quad_eq._integer_equation(Fraction(1, 2), Fraction(1, 3)) == (6, -5, 1)
    # The leading coefficient is always positive.
    for a, _b, _c in (quad_eq._integer_equation(Fraction(-7, 3), Fraction(5, 6)),
                      quad_eq._integer_equation(Fraction(-4), Fraction(-9))):
        assert a > 0


@pytest.mark.parametrize("task_type", sorted(VIETA_GROUPS))
def test_inactive_parameters_are_canonicalised(vieta_blueprint, task_type):
    """A parameter this task does not use must hold ONE value across every roll.

    This is the property `content_hash` depends on: it hashes the whole spec, so a
    parameter left rolling in a branch that ignores it fabricates distinct hashes
    for identical problems, and bank dedup silently stops working.
    """
    difficulty = {"sum_product": 1, "root_expression": 2, "find_second_root": 2,
                  "construct_equation": 3, "construct_from_sum_difference": 3}[task_type]
    inactive = {name for group, names in VIETA_GROUPS.items() if group != task_type
                for name in names} - set(VIETA_GROUPS[task_type])
    seen: dict[str, set] = {name: set() for name in inactive}
    rolls = 0
    for seed in range(500):
        random.seed(seed)
        spec = generate_math_spec(vieta_blueprint, difficulty)
        if spec["task_type"] != task_type:
            continue
        rolls += 1
        for name in inactive:
            seen[name].add(spec[name])
    assert rolls > 20, f"only {rolls} rolls of {task_type}; sample too thin to conclude"
    varying = {name: values for name, values in seen.items() if len(values) > 1}
    assert not varying, f"{task_type} leaves these unused params rolling: {varying}"


@pytest.mark.parametrize("difficulty", [1, 2, 3])
def test_vieta_generates_end_to_end(vieta_blueprint, difficulty):
    expected_tasks = {
        1: {"sum_product"},
        2: {"root_expression", "find_second_root"},
        3: {"construct_equation", "construct_from_sum_difference"},
    }[difficulty]
    for seed in range(150):
        random.seed(seed)
        spec = generate_math_spec(vieta_blueprint, difficulty)
        assert spec["task_type"] in expected_tasks, spec

        answer = compute_answer_key(vieta_blueprint, spec)
        assert isinstance(answer, str) and answer

        # Where an equation is posed, its roots must be real -- otherwise talking
        # about "the roots x1 and x2" is meaningless.
        if spec["task_type"] in ("sum_product", "root_expression"):
            assert spec["b"] ** 2 - 4 * spec["a"] * spec["c"] >= 0, spec

        payload = render_constraints(vieta_blueprint, spec)
        assert "{{" not in payload and "{%" not in payload

        solution = build_solution(vieta_blueprint, spec, answer)
        assert solution["answer_type"] == VIETA_ANSWER_TYPE
        assert solution["steps"]

        options = quad_eq.build_options(VIETA_ANSWER_TYPE, spec, n_options=N_OPTIONS)
        assert len(options) == N_OPTIONS, (spec, options)
        assert sum(o["is_correct"] for o in options) == 1
        texts = [o["text"] for o in options]
        assert len(set(texts)) == len(texts), options
        assert answer in texts
        declared = {d["id"] for d in vieta_blueprint["distractors"]}
        assert {o["misconception"] for o in options if o["misconception"]} <= declared


def test_vieta_task_specific_misconception_gets_a_slot(vieta_blueprint):
    """Each task variant's signature error must survive slot competition."""
    required = {
        ("root_expression", "sum_squares"): "sum_squares_missing_2p",
        ("root_expression", "reciprocal_sum"): "reciprocal_sum_inverted",
        ("root_expression", "ratio_sum"): "ratio_sum_wrong_denominator",
        ("find_second_root", None): "second_root_wrong_operation",
        ("construct_from_sum_difference", None): "sum_difference_halving_error",
        ("construct_equation", "conjugate_radical"): "conjugate_product_error",
    }
    hit = {key: 0 for key in required}
    for difficulty in (2, 3):
        for seed in range(500):
            random.seed(seed)
            spec = generate_math_spec(vieta_blueprint, difficulty)
            task = spec["task_type"]
            variant = (spec["expression_type"] if task == "root_expression"
                       else spec["root_family"] if task == "construct_equation" else None)
            key = (task, variant)
            if key not in required:
                continue
            hit[key] += 1
            options = quad_eq.build_options(VIETA_ANSWER_TYPE, spec, n_options=N_OPTIONS)
            assert required[key] in {o["misconception"] for o in options}, (spec, options)
    assert all(hit.values()), f"some variants never generated: {hit}"


def test_vieta_one_problem_one_hash(vieta_blueprint):
    by_problem: dict[tuple, set] = {}
    for difficulty in (1, 2, 3):
        for seed in range(400):
            random.seed(seed * 7 + difficulty)
            spec = generate_math_spec(vieta_blueprint, difficulty)
            answer = compute_answer_key(vieta_blueprint, spec)
            key = (spec["task_type"], spec["a"], spec["b"], spec["c"],
                   spec["expression_type"], spec["known_root_index"], answer)
            by_problem.setdefault(key, set()).add(
                compute_content_hash(VIETA_TOPIC, spec, "ru")
            )
    collisions = {k: v for k, v in by_problem.items() if len(v) > 1}
    assert not collisions, f"same problem, different hashes: {list(collisions)[:3]}"
