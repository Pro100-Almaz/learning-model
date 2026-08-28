"""
Exact solver for the quadratic-equation blueprint family.

Several Qadam topics pose the same underlying object — a quadratic with integer
coefficients — but ask for it in ways no single `math_ques_types` answer type can
carry. Depending on the topic and the roll, the answer is two rational roots, one
repeated root, a pair of quadratic surds, the statement that no real root exists,
or a count in words. So, exactly as `inv_trig` does for the symbolic-angle
family, this module owns the arithmetic and hands back rendered LaTeX, with one
dispatch entry per answer type:

    quadratic_discriminant   solve (or count the roots of) a x^2 + b x + c = 0
                             through D = b^2 - 4ac
    quadratic_factorization  solve it by splitting the trinomial into two linear
                             factors, possibly after undoing a presentation dressing
    quadratic_vieta          relate roots to coefficients without solving — report
                             S and P, evaluate a symmetric expression, recover a
                             missing root, or build the equation from its roots

Everything is exact, pure-Python integer / `Fraction` arithmetic. No float ever
touches a root, so the answer key carries the same math-integrity guarantee as
`compute_answer_key`: never delegated to an LLM, never drifting.

Each blueprint hands us its coefficients already reduced to standard form (its
`derived` block computes `a`, `b`, `c`), so this module never needs to know how
they were built — only how to solve them and which mistakes the topic's method
invites.

Public API (consumed by math_ques_types.compute_answer_key,
math_engine.build_solution and nodes_self.architect_node), mirroring inv_trig's:

    ANSWER_TYPES                        -> frozenset of the type strings
    compute_answer(answer_type, spec)   -> str  (LaTeX answer key)
    solution_steps(answer_type, spec)   -> list[{"label", "detail"}]
    build_options(answer_type, spec, n) -> list[{"text","is_correct","misconception"}]
"""
from __future__ import annotations

import random
from fractions import Fraction
from math import gcd, lcm
from typing import Any, Callable, Iterable, NamedTuple

# One implementation of the square-free split, shared with the inverse-trig
# engine rather than duplicated: n = k^2 * m with m square-free.
from .inv_trig import _square_free

# Student-facing answer text, so it follows the curriculum's language (Russian),
# the same way the `static_choice` blueprints spell out "нечетная" / "общего вида".
# Step LABELS below stay English, matching math_engine's other solution builders —
# those are the Tutor's internal ground truth, not something a student reads.
NO_REAL_ROOTS = "нет действительных корней"

# Answer texts for the "how many real roots" question. Spelled out rather than
# left as bare digits so a count option cannot be mistaken for a root value, and
# so the zero case reads identically to the zero case of a `solve` question.
COUNT_TEXT = {0: NO_REAL_ROOTS, 1: "один корень", 2: "два корня"}

# A real quadratic has at most two roots, so this is always wrong — but "an
# equation can have infinitely many solutions" is a live student belief carried
# over from identities like 0 = 0. It exists to fill the fourth slot: the true
# answer space there is only {0, 1, 2}, while the Publisher refuses to store an
# item with fewer than N_ANSWER_OPTIONS choices (apps/assessments/services.py).
COUNT_IMPOSSIBLE = "бесконечно много корней"

# Separator between two roots in a rendered answer. Escaped-space so the two
# values stay visually distinct once the option text is typeset.
ROOT_SEP = ";\\ "


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
def _frac(value: Fraction) -> str:
    """LaTeX for an exact rational: `3`, `-3`, `\\frac{2}{3}`, `-\\frac{2}{3}`."""
    if value.denominator == 1:
        return str(value.numerator)
    sign = "-" if value < 0 else ""
    return f"{sign}\\frac{{{abs(value.numerator)}}}{{{value.denominator}}}"


def _surd_root(numer: int, k: int, m: int, den: int, plus: bool) -> str:
    """LaTeX for `(numer ± k*sqrt(m)) / den`, reduced to lowest terms.

    `den` is required to be positive (callers normalise the leading coefficient
    first), which is what lets the ± stay put: with a positive denominator the
    minus branch is always the smaller root, so the two roots need no sorting.
    """
    divisor = gcd(gcd(abs(numer), k), den)
    numer, k, den = numer // divisor, k // divisor, den // divisor

    radical = f"\\sqrt{{{m}}}" if k == 1 else f"{k}\\sqrt{{{m}}}"
    if numer == 0:
        body = radical if plus else f"-{radical}"
    else:
        body = f"{numer} {'+' if plus else '-'} {radical}"

    if den == 1:
        return body
    # A bare ±sqrt over a denominator reads better with the sign outside the
    # fraction than buried in its numerator.
    if numer == 0:
        return f"{'' if plus else '-'}\\frac{{{radical}}}{{{den}}}"
    return f"\\frac{{{body}}}{{{den}}}"


def _render_exact(values: Iterable[Fraction]) -> str:
    """Render a set of exact rational roots, ascending and de-duplicated.

    The counterpart to `_render_roots` for topics that know their roots directly
    (a factorisation hands them over without any quadratic formula). Collapsing
    duplicates is load-bearing, not tidiness: a repeated factor must present as
    ONE root, which is exactly what distinguishes it from the two-root case.
    """
    return ROOT_SEP.join(_frac(v) for v in sorted(set(values)))


# ---------------------------------------------------------------------------
# The solver
# ---------------------------------------------------------------------------
def _coefficients(spec: dict) -> tuple[int, int, int]:
    """The standard-form coefficients, normalised so `a` is positive.

    Negating all three leaves the roots untouched but guarantees `2a > 0`, which
    every renderer below relies on. A zero leading coefficient is not a quadratic
    at all, so it fails loudly here rather than dividing by zero downstream.
    """
    a, b, c = int(spec["a"]), int(spec["b"]), int(spec["c"])
    if a == 0:
        raise ValueError(f"Not a quadratic: leading coefficient is 0 (spec={spec})")
    return (a, b, c) if a > 0 else (-a, -b, -c)


def _render_roots(numer: int, discriminant: int, den: int) -> tuple[int, list[str]]:
    """Solve `x = (numer ± sqrt(discriminant)) / den` exactly.

    Returns `(count_of_distinct_real_roots, [rendered roots, ascending])`.

    Taking the numerator, the discriminant and the denominator as free arguments
    (rather than deriving all three from a, b, c) is what lets one function serve
    both the correct answer AND every misconception: a student who computes
    `b^2 + 4ac`, or divides by 2 instead of 2a, or forgets to negate b, is
    running this same routine with one argument wrong.
    """
    if discriminant < 0:
        return 0, []
    if discriminant == 0:
        return 1, [_frac(Fraction(numer, den))]

    k, m = _square_free(discriminant)
    if m == 1:  # perfect square -> both roots are exact rationals
        return 2, [_frac(Fraction(numer - k, den)), _frac(Fraction(numer + k, den))]
    return 2, [_surd_root(numer, k, m, den, plus=False),
               _surd_root(numer, k, m, den, plus=True)]


def _solve_standard(spec: dict) -> tuple[int, int, int, int, int, list[str]]:
    """(a, b, c, D, count, roots) for one rolled spec, via the quadratic formula."""
    a, b, c = _coefficients(spec)
    discriminant = b * b - 4 * a * c
    count, roots = _render_roots(-b, discriminant, 2 * a)
    return a, b, c, discriminant, count, roots


# ===========================================================================
# Answer type: quadratic_discriminant
# ===========================================================================
def _discriminant_format(count: int, roots: list[str], query_type: str) -> str:
    """Render a solved result the way this blueprint's `query_type` asks for it."""
    if query_type == "number_of_real_roots":
        return COUNT_TEXT[count]
    return NO_REAL_ROOTS if count == 0 else ROOT_SEP.join(roots)


def _discriminant_answer(spec: dict) -> str:
    *_, count, roots = _solve_standard(spec)
    return _discriminant_format(count, roots, spec["query_type"])


def _discriminant_distractors(spec: dict) -> list[tuple[str, str]]:
    """`(misconception_id, rendered answer)` for every error this roll can express.

    The ids match the `distractors` block of the blueprint, which supplies the
    human-readable `desc` that the Tutor shows; here we supply the value each
    error actually produces. Several are case-specific — you cannot make the
    "D < 0 still has roots" mistake on an equation whose D is positive — so a
    given roll fires only the subset that applies, and `build_options` tops up
    whatever slots are left.
    """
    a, b, c, discriminant, _count, _roots = _solve_standard(spec)
    query_type = spec["query_type"]
    tagged: list[tuple[str, str]] = []

    def add(mid: str, result: tuple[int, list[str]]) -> None:
        tagged.append((mid, _discriminant_format(*result, query_type)))

    # Sign slip inside the discriminant itself: b^2 + 4ac instead of b^2 - 4ac.
    add("discriminant_sign_error", _render_roots(-b, b * b + 4 * a * c, 2 * a))
    # b instead of b^2 in the discriminant.
    add("forgot_square_on_b", _render_roots(-b, b - 4 * a * c, 2 * a))

    if discriminant > 0:
        # Divided by 2 rather than by 2a. Collapses onto the correct answer when
        # a == 1, and is deduped away there.
        add("forgot_denominator_2a", _render_roots(-b, discriminant, 2))
        # Used +b rather than -b in the numerator. Collapses when b == 0.
        add("wrong_minus_b", _render_roots(b, discriminant, 2 * a))
    if discriminant < 0:
        # Pulled a real square root out of a negative discriminant anyway.
        add("negative_d_has_roots", _render_roots(-b, -discriminant, 2 * a))
    if discriminant == 0:
        # Read the ± as producing two distinct roots even though sqrt(D) is 0,
        # so the ± lands on b instead. Collapses when b == 0.
        if query_type == "number_of_real_roots":
            tagged.append(("d_zero_two_roots", COUNT_TEXT[2]))
        else:
            tagged.append(("d_zero_two_roots",
                           _discriminant_format(2, [_frac(Fraction(-b, 2 * a)),
                                                    _frac(Fraction(b, 2 * a))],
                                                query_type)))
    return tagged


def _discriminant_steps(spec: dict) -> list[dict[str, str]]:
    a, b, c, discriminant, count, roots = _solve_standard(spec)
    steps = [
        {"label": "Equation", "detail": f"{a}x^2 + ({b})x + ({c}) = 0"},
        {"label": "Coefficients", "detail": f"a = {a}, b = {b}, c = {c}"},
        {"label": "Discriminant",
         "detail": f"D = b^2 - 4ac = ({b})^2 - 4*{a}*({c}) = {discriminant}"},
    ]
    if discriminant < 0:
        steps.append({"label": "Sign of D",
                      "detail": f"D = {discriminant} < 0 — no real roots exist"})
    elif discriminant == 0:
        steps.append({"label": "Sign of D",
                      "detail": "D = 0 — exactly one (repeated) real root"})
        steps.append({"label": "Root",
                      "detail": f"x = -b/(2a) = -({b})/(2*{a}) = ${roots[0]}$"})
    else:
        steps.append({"label": "Sign of D",
                      "detail": f"D = {discriminant} > 0 — two distinct real roots"})
        steps.append({"label": "Roots",
                      "detail": f"x = (-b ± sqrt(D))/(2a) = (-({b}) ± sqrt({discriminant}))/(2*{a})"
                                f" = ${roots[0]}$, ${roots[1]}$"})
    if spec["query_type"] == "number_of_real_roots":
        steps.append({"label": "Answer",
                      "detail": f"{count} real root(s) — {COUNT_TEXT[count]}"})
    return steps


def _discriminant_pool(spec: dict) -> list[str]:
    a, b, _c, discriminant, _count, _roots = _solve_standard(spec)
    query_type = spec["query_type"]
    # For a count the answer space is tiny and exhaustive.
    if query_type == "number_of_real_roots":
        return [COUNT_TEXT[2], COUNT_TEXT[1], COUNT_TEXT[0], COUNT_IMPOSSIBLE]
    # Nudging the numerator keeps a wrong option the same SHAPE as the right one
    # (a surd stays a surd), so it can't be spotted by formatting alone. But that
    # alone is not enough: when D <= 0 the discriminant, not the numerator, is
    # what decides the shape, so every nudge would re-render the identical "no
    # real roots" sentence and the slate would collapse to two options. So we
    # also vary the discriminant into positive territory, which is what makes a
    # root set available as a wrong answer even when no root exists.
    magnitude = abs(discriminant) or 4
    return [
        *(_discriminant_format(*_render_roots(-b + delta, discriminant, 2 * a), query_type)
          for delta in (1, -1, 2, -2)),
        *(_discriminant_format(*_render_roots(-b, magnitude + step, 2 * a), query_type)
          for step in (0, 4, 9, 12, 21)),
    ]


# ===========================================================================
# Answer type: quadratic_factorization
# ===========================================================================
def _factor_roots(spec: dict) -> tuple[Fraction, Fraction]:
    """The two roots read straight off the hidden factors (p x + q)(r x + s).

    No quadratic formula is involved: the generator BUILT the equation from these
    factors, so the roots are exact rationals by construction. When the factors
    coincide the two values are equal, and `_render_exact` collapses them into
    the single repeated root the student is expected to report.
    """
    return Fraction(-spec["q"], spec["p"]), Fraction(-spec["s"], spec["r"])


def _factorization_answer(spec: dict) -> str:
    return _render_exact(_factor_roots(spec))


def _alternate_factor_pair(b: int, c: int) -> set[Fraction] | None:
    """A pair of integers with the RIGHT product `c` but the wrong sum.

    Models the monic-technique error precisely: the student hunts for m, n with
    m*n = c and m + n = b, finds a pair satisfying only the product, and stops.
    Returns None when `c` admits no second factorisation (e.g. c = ±1), in which
    case the misconception simply does not fire for this roll.
    """
    for divisor in range(1, abs(c) + 1):
        if c % divisor:
            continue
        for first in (divisor, -divisor):
            second = c // first
            if first + second == -b:
                continue  # that IS the correct pair
            return {Fraction(first), Fraction(second)}
    return None


def _factorization_distractors(spec: dict) -> list[tuple[str, str]]:
    """The wrong answers each documented factoring error actually produces.

    Note which id is absent: `forgot_common_factor`. Failing to divide out the
    common numeric factor of a scaled equation is a real process error, but it
    changes no ROOT — k(px+q)(rx+s) = 0 and (px+q)(rx+s) = 0 have the same
    solution set — so it cannot yield a distinct wrong option. It is documented
    in the blueprint and deliberately not answerable here.
    """
    p, q, r, s = spec["p"], spec["q"], spec["r"], spec["s"]
    a, b, c = p * r, p * s + q * r, q * s
    root_1, root_2 = _factor_roots(spec)
    form = spec["presentation_form"]
    tagged: list[tuple[str, str]] = []

    # ORDER MATTERS: build_options fills a fixed number of slots and stops, so
    # the errors this particular roll is DESIGNED to catch have to be offered
    # before the ones any factoring problem would catch. A `both_sides` item
    # exists to punish factoring before transposing; if the two universal sign
    # errors were listed first they would take the slots and the item would stop
    # testing what it was built to test.
    if form == "both_sides":
        # Factored the equation as printed, before moving the right-hand side
        # across — so the trinomial attacked was a x^2 + (b+Δb) x + (c+Δc).
        shifted_b, shifted_c = b + spec["shift_b"], c + spec["shift_c"]
        count, roots = _render_roots(-shifted_b,
                                     shifted_b ** 2 - 4 * a * shifted_c, 2 * a)
        tagged.append(("forgot_standardization",
                       NO_REAL_ROOTS if count == 0 else ROOT_SEP.join(roots)))
    if form == "repeated_root":
        # A perfect square read as if the ± of the quadratic formula applied,
        # turning one repeated root into a symmetric pair.
        tagged.append(("repeated_root_count_error", _render_exact({root_1, -root_1})))

    # Next: errors specific to which factoring TECHNIQUE this roll demands.
    if a == 1:
        # The m*n = c / m + n = b hunt only exists for the monic technique.
        alternate = _alternate_factor_pair(b, c)
        if alternate is not None:
            tagged.append(("correct_product_wrong_sum", _render_exact(alternate)))
    else:
        # Never checked the cross-product sum, so the two constants ended up in
        # the wrong brackets: (p x + s)(r x + q) reproduces a and c but not b.
        tagged.append(("wrong_cross_sum",
                       _render_exact({Fraction(-s, p), Fraction(-q, r)})))
    if c < 0:
        # A negative constant term forces opposite-signed factors; this student
        # picked the same sign for both.
        tagged.append(("wrong_signs_for_negative_c",
                       _render_exact({root_1, -root_2})))

    # Finally the two universal slips, which any factoring item can express.
    # Solved k*x + m = 0 as x = m/k, keeping the constant's sign instead of
    # flipping it.
    tagged.append(("root_sign_not_changed", _render_exact({-root_1, -root_2})))
    # Set only the first factor to zero and stopped, reporting a single root.
    tagged.append(("only_one_factor_zero", _render_exact({root_1})))
    return tagged


def _factorization_steps(spec: dict) -> list[dict[str, str]]:
    p, q, r, s = spec["p"], spec["q"], spec["r"], spec["s"]
    a, b, c = p * r, p * s + q * r, q * s
    form = spec["presentation_form"]
    root_1, root_2 = _factor_roots(spec)

    steps: list[dict[str, str]] = []
    if form == "scaled":
        k = spec["scale"]
        steps.append({"label": "As posed",
                      "detail": f"{k * a}x^2 + ({k * b})x + ({k * c}) = 0"})
        steps.append({"label": "Divide out the common factor",
                      "detail": f"every coefficient is divisible by {k} -> "
                                f"{a}x^2 + ({b})x + ({c}) = 0"})
    elif form == "both_sides":
        db, dc = spec["shift_b"], spec["shift_c"]
        steps.append({"label": "As posed",
                      "detail": f"{a}x^2 + ({b + db})x + ({c + dc}) = ({db})x + ({dc})"})
        steps.append({"label": "Collect into standard form",
                      "detail": f"move every term to the left -> {a}x^2 + ({b})x + ({c}) = 0"})
    else:
        steps.append({"label": "Equation", "detail": f"{a}x^2 + ({b})x + ({c}) = 0"})

    steps.append({"label": "Factor the trinomial",
                  "detail": f"({p}x + ({q}))({r}x + ({s})) = 0"
                            + ("  — the two factors coincide" if root_1 == root_2 else "")})
    steps.append({"label": "Zero-product rule",
                  "detail": f"{p}x + ({q}) = 0  or  {r}x + ({s}) = 0"})
    if root_1 == root_2:
        steps.append({"label": "Root",
                      "detail": f"one repeated root: $ {_frac(root_1)} $"})
    else:
        steps.append({"label": "Roots",
                      "detail": f"$ {_frac(min(root_1, root_2))} $, $ {_frac(max(root_1, root_2))} $"})
    return steps


def _factorization_pool(spec: dict) -> list[str]:
    """Near-misses that keep the shape of a rational root set."""
    root_1, root_2 = _factor_roots(spec)
    return [
        *(_render_exact({root_1 + delta, root_2}) for delta in (1, -1)),
        *(_render_exact({root_1, root_2 + delta}) for delta in (1, -1)),
        *(_render_exact({root_1 + delta, root_2 + delta}) for delta in (1, -1, 2)),
    ]


# ===========================================================================
# Answer type: quadratic_vieta
# ===========================================================================
# Vieta topics ask five different things about the same relationship, and the
# answer SHAPE changes with the ask: a pair of values, a single rational, or a
# whole equation. `task_type` selects; everything below routes on it.
#
#   sum_product                    report x1+x2 and x1*x2 without solving
#   root_expression                evaluate a symmetric expression in the roots
#   find_second_root               one root given, recover the other
#   construct_equation             roots given, build the equation (inverse Vieta)
#   construct_from_sum_difference  sum and difference given, build the equation
_VIETA_CONSTRUCT = ("construct_equation", "construct_from_sum_difference")

# Each symmetric expression, rewritten in terms of S = x1+x2 and P = x1*x2 alone.
# That rewrite IS the technique being taught: the individual roots are never
# computed, which is why an irrational-root equation still yields a clean answer.
_VIETA_EXPRESSION = {
    "x1_sq_x2_plus_x1_x2_sq": lambda total, product: product * total,
    "reciprocal_sum": lambda total, product: total / product,
    "sum_squares": lambda total, product: total * total - 2 * product,
    "ratio_sum": lambda total, product: (total * total - 2 * product) / product,
}


def _render_equation(a: int, b: int, c: int) -> str:
    """`a x^2 + b x + c = 0` as LaTeX, with unit and zero coefficients folded."""
    parts = ["x^2" if a == 1 else f"{a}x^2"]
    if b:
        magnitude = "" if abs(b) == 1 else str(abs(b))
        parts.append(f"{'+' if b > 0 else '-'} {magnitude}x")
    if c:
        parts.append(f"{'+' if c > 0 else '-'} {abs(c)}")
    return " ".join(parts) + " = 0"


def _integer_equation(root_1: Fraction, root_2: Fraction) -> tuple[int, int, int]:
    """The minimal integer-coefficient quadratic with exactly these two roots.

    Inverse Vieta: start from the monic `x^2 - S x + P`, clear the denominators of
    S and P together, then divide out the common factor. Returning the REDUCED
    form matters — `2x^2 - 6x + 4 = 0` and `x^2 - 3x + 2 = 0` are the same answer,
    and a student who reduced correctly must not be marked wrong for it.
    """
    total, product = root_1 + root_2, root_1 * root_2
    multiplier = lcm(total.denominator, product.denominator)
    a = multiplier
    b = int(-total * multiplier)
    c = int(product * multiplier)
    divisor = gcd(gcd(a, abs(b)), abs(c)) or 1
    return a // divisor, b // divisor, c // divisor


def _vieta_roots(spec: dict) -> tuple[Fraction, Fraction]:
    """The two roots a `construct_*` task hands the student, as exact rationals.

    Only meaningful for the rational/integer families and for the sum-difference
    task; the conjugate-radical family has irrational roots and is handled
    separately, straight from its (S, P), which are rational even though the
    roots are not.
    """
    if spec["task_type"] == "construct_from_sum_difference":
        total, difference = spec["roots_sum"], spec["roots_difference"]
        return Fraction(total + difference, 2), Fraction(total - difference, 2)
    if spec["root_family"] == "integer":
        return Fraction(spec["root_int_1"]), Fraction(spec["root_int_2"])
    return Fraction(spec["num_1"], spec["den_1"]), Fraction(spec["num_2"], spec["den_2"])


def _conjugate_sum_product(spec: dict) -> tuple[int, int]:
    """(S, P) for the conjugate pair `rad_center ± rad_coeff*sqrt(rad_base)`.

    The radical cancels in both: the sum doubles the rational part, and the
    product is a difference of squares. That cancellation is the whole point of
    the conjugate family — it is why irrational roots can still produce an
    equation with integer coefficients.
    """
    centre, coefficient, base = spec["rad_center"], spec["rad_coeff"], spec["rad_base"]
    return 2 * centre, centre * centre - coefficient * coefficient * base


def _vieta_sum_product(spec: dict) -> tuple[Fraction, Fraction]:
    """S and P read off the posed equation, exactly as the student must."""
    a, b, c = _coefficients(spec)
    return Fraction(-b, a), Fraction(c, a)


def _vieta_known_and_missing(spec: dict) -> tuple[Fraction, Fraction]:
    """(the root the student is given, the root they must find)."""
    first = Fraction(-spec["q"], spec["p"])
    second = Fraction(-spec["s"], spec["r"])
    return (first, second) if spec["known_root_index"] == 1 else (second, first)


def _vieta_pair_text(total: Fraction, product: Fraction) -> str:
    return f"x_1 + x_2 = {_frac(total)}{ROOT_SEP}x_1 x_2 = {_frac(product)}"


def _vieta_answer(spec: dict) -> str:
    task = spec["task_type"]
    if task == "sum_product":
        return _vieta_pair_text(*_vieta_sum_product(spec))
    if task == "root_expression":
        total, product = _vieta_sum_product(spec)
        return _frac(_VIETA_EXPRESSION[spec["expression_type"]](total, product))
    if task == "find_second_root":
        return _frac(_vieta_known_and_missing(spec)[1])
    if task == "construct_equation" and spec["root_family"] == "conjugate_radical":
        total, product = _conjugate_sum_product(spec)
        return _render_equation(1, -total, product)
    return _render_equation(*_integer_equation(*_vieta_roots(spec)))


def _vieta_distractors(spec: dict) -> list[tuple[str, str]]:
    """The wrong answers each documented Vieta error actually produces.

    Ordered task-specific first, then the two universal sign slips — the same
    reasoning as the factorization list: `build_options` fills a fixed number of
    slots and stops, so an item must offer the error it was built to catch before
    the errors any Vieta item could catch.
    """
    task = spec["task_type"]
    tagged: list[tuple[str, str]] = []

    if task in ("sum_product", "root_expression"):
        a, b, c = _coefficients(spec)
        total, product = Fraction(-b, a), Fraction(c, a)
        if task == "root_expression":
            expression = _VIETA_EXPRESSION[spec["expression_type"]]
            # Each of these three is the specific slip its expression invites.
            if spec["expression_type"] == "sum_squares":
                tagged.append(("sum_squares_missing_2p", _frac(total * total)))
            elif spec["expression_type"] == "reciprocal_sum":
                tagged.append(("reciprocal_sum_inverted", _frac(product / total)))
            elif spec["expression_type"] == "ratio_sum":
                tagged.append(("ratio_sum_wrong_denominator",
                               _frac(total * total - 2 * product)))
            if a != 1:
                # Never divided by the leading coefficient: used -b and c raw.
                tagged.append(("ignored_leading_coefficient",
                               _frac(expression(Fraction(-b), Fraction(c)))))
            tagged.append(("wrong_sum_sign", _frac(expression(-total, product))))
            tagged.append(("wrong_product_sign", _frac(expression(total, -product))))
        else:
            if a != 1:
                tagged.append(("ignored_leading_coefficient",
                               _vieta_pair_text(Fraction(-b), Fraction(c))))
            tagged.append(("wrong_sum_sign", _vieta_pair_text(-total, product)))
            tagged.append(("wrong_product_sign", _vieta_pair_text(total, -product)))

    elif task == "find_second_root":
        a, b, c = _coefficients(spec)
        known, missing = _vieta_known_and_missing(spec)
        product = Fraction(c, a)
        # Subtracted from the product instead of dividing into it.
        tagged.append(("second_root_wrong_operation", _frac(product - known)))
        # Used the SUM rule with the wrong sign, then subtracted.
        tagged.append(("wrong_sum_sign", _frac(Fraction(b, a) - known)))
        tagged.append(("wrong_product_sign", _frac(-product / known)))

    else:  # the two construct_* tasks
        if task == "construct_equation" and spec["root_family"] == "conjugate_radical":
            total_i, product_i = _conjugate_sum_product(spec)
            centre, coefficient, base = spec["rad_center"], spec["rad_coeff"], spec["rad_base"]
            # Added the squares instead of subtracting: lost the difference-of-squares.
            tagged.append(("conjugate_product_error",
                           _render_equation(1, -total_i,
                                            centre * centre + coefficient * coefficient * base)))
            a, b, c = 1, -total_i, product_i
        else:
            root_1, root_2 = _vieta_roots(spec)
            if task == "construct_from_sum_difference":
                # Took S+d and S-d as the roots, forgetting to halve them.
                tagged.append(("sum_difference_halving_error",
                               _render_equation(*_integer_equation(
                                   Fraction(spec["roots_sum"] + spec["roots_difference"]),
                                   Fraction(spec["roots_sum"] - spec["roots_difference"])))))
            a, b, c = _integer_equation(root_1, root_2)
        # Wrote x^2 + Sx + P: the middle term carries a MINUS in inverse Vieta.
        tagged.append(("inverse_vieta_wrong_x_sign", _render_equation(a, -b, c)))
        tagged.append(("wrong_product_sign", _render_equation(a, b, -c)))
        tagged.append(("wrong_sum_sign", _render_equation(a, -b, -c)))
    return tagged


def _vieta_steps(spec: dict) -> list[dict[str, str]]:
    task = spec["task_type"]
    if task in ("sum_product", "root_expression", "find_second_root"):
        a, b, c = _coefficients(spec)
        total, product = Fraction(-b, a), Fraction(c, a)
        steps = [
            {"label": "Equation", "detail": f"{a}x^2 + ({b})x + ({c}) = 0"},
            {"label": "Vieta: sum", "detail": f"x1 + x2 = -b/a = -({b})/{a} = {_frac(total)}"},
            {"label": "Vieta: product", "detail": f"x1 * x2 = c/a = ({c})/{a} = {_frac(product)}"},
        ]
        if task == "root_expression":
            steps.append({"label": "Rewrite via S and P",
                          "detail": f"{spec['expression_type']} in terms of "
                                    f"S = {_frac(total)}, P = {_frac(product)}"})
            steps.append({"label": "Value",
                          "detail": f"${_vieta_answer(spec)}$"})
        elif task == "find_second_root":
            known, missing = _vieta_known_and_missing(spec)
            steps.append({"label": "Given root", "detail": f"x1 = {_frac(known)}"})
            steps.append({"label": "Second root",
                          "detail": f"x2 = P / x1 = {_frac(product)} / {_frac(known)}"
                                    f" = ${_frac(missing)}$"})
        return steps

    steps = []
    if task == "construct_from_sum_difference":
        total_v, difference = spec["roots_sum"], spec["roots_difference"]
        root_1, root_2 = _vieta_roots(spec)
        steps.append({"label": "Given", "detail": f"x1 + x2 = {total_v}, x1 - x2 = {difference}"})
        steps.append({"label": "Recover the roots",
                      "detail": f"x1 = (S+d)/2 = {_frac(root_1)}, x2 = (S-d)/2 = {_frac(root_2)}"})
    elif spec["root_family"] == "conjugate_radical":
        centre, coefficient, base = spec["rad_center"], spec["rad_coeff"], spec["rad_base"]
        total_i, product_i = _conjugate_sum_product(spec)
        radical = f"sqrt({base})" if coefficient == 1 else f"{coefficient}sqrt({base})"
        steps.append({"label": "Given roots",
                      "detail": f"{centre} ± {radical} — a conjugate pair"})
        steps.append({"label": "Sum", "detail": f"the radicals cancel: S = 2*{centre} = {total_i}"})
        steps.append({"label": "Product",
                      "detail": f"difference of squares: P = {centre}^2 - {coefficient}^2*{base}"
                                f" = {product_i}"})
    else:
        root_1, root_2 = _vieta_roots(spec)
        steps.append({"label": "Given roots",
                      "detail": f"x1 = {_frac(root_1)}, x2 = {_frac(root_2)}"})
        steps.append({"label": "Sum and product",
                      "detail": f"S = {_frac(root_1 + root_2)}, P = {_frac(root_1 * root_2)}"})
    steps.append({"label": "Inverse Vieta", "detail": "x^2 - S*x + P = 0, then clear denominators"})
    steps.append({"label": "Equation", "detail": f"${_vieta_answer(spec)}$"})
    return steps


def _vieta_pool(spec: dict) -> list[str]:
    """Near-misses that keep the SHAPE of this task's answer."""
    task = spec["task_type"]
    if task == "sum_product":
        total, product = _vieta_sum_product(spec)
        return [_vieta_pair_text(total + d, product + e)
                for d, e in ((1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, -1))]
    if task in ("root_expression", "find_second_root"):
        value = Fraction(_vieta_known_and_missing(spec)[1]) if task == "find_second_root" else \
            _VIETA_EXPRESSION[spec["expression_type"]](*_vieta_sum_product(spec))
        return [_frac(value + delta) for delta in (1, -1, 2, -2, 3, -3)]
    if task == "construct_equation" and spec["root_family"] == "conjugate_radical":
        total_i, product_i = _conjugate_sum_product(spec)
        a, b, c = 1, -total_i, product_i
    else:
        a, b, c = _integer_equation(*_vieta_roots(spec))
    return [_render_equation(a, b + delta, c) for delta in (1, -1, 2, -2)] + \
           [_render_equation(a, b, c + delta) for delta in (1, -1)]


# ===========================================================================
# Dispatch
# ===========================================================================
class _Solver(NamedTuple):
    """The four things every answer type in this family must be able to do."""
    answer: Callable[[dict], str]
    distractors: Callable[[dict], list[tuple[str, str]]]
    steps: Callable[[dict], list[dict[str, str]]]
    pool: Callable[[dict], list[str]]


_DISPATCH: dict[str, _Solver] = {
    "quadratic_discriminant": _Solver(
        _discriminant_answer, _discriminant_distractors,
        _discriminant_steps, _discriminant_pool,
    ),
    "quadratic_factorization": _Solver(
        _factorization_answer, _factorization_distractors,
        _factorization_steps, _factorization_pool,
    ),
    "quadratic_vieta": _Solver(
        _vieta_answer, _vieta_distractors, _vieta_steps, _vieta_pool,
    ),
}
ANSWER_TYPES = frozenset(_DISPATCH)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_answer(answer_type: str, spec: dict) -> str:
    """The exact correct answer, as a LaTeX fragment (or the no-roots sentence)."""
    return _DISPATCH[answer_type].answer(spec)


def solution_steps(answer_type: str, spec: dict) -> list[dict[str, str]]:
    """The deterministic derivation the Tutor reverse-engineers a mistake against."""
    return _DISPATCH[answer_type].steps(spec)


def build_options(answer_type: str, spec: dict, n_options: int = 4) -> list[dict[str, Any]]:
    """One correct option plus distractors, each tagged with its misconception.

    Mirrors `math_engine.build_answer_options`' contract (same dict shape, dedup
    by rendered text, shuffled) but computes every wrong value exactly instead of
    shifting numbers around. Named misconceptions go in first, then any leftover
    slots are topped up from the type's pool carrying an empty tag — the Tutor
    infers those, the same convention inv_trig uses.
    """
    solver = _DISPATCH[answer_type]
    correct = solver.answer(spec)
    options = [{"text": correct, "is_correct": True, "misconception": ""}]
    seen = {correct}

    for mid, text in solver.distractors(spec):
        if len(options) >= n_options:
            break
        if text in seen:
            continue  # collapsed onto the correct answer or an earlier distractor
        seen.add(text)
        options.append({"text": text, "is_correct": False, "misconception": mid})

    for text in solver.pool(spec):
        if len(options) >= n_options:
            break
        if text in seen:
            continue
        seen.add(text)
        options.append({"text": text, "is_correct": False, "misconception": ""})

    random.shuffle(options)
    return options
