"""Higher-degree equations solved by introducing a new variable.

Every structure this module handles hides the SAME skeleton: a quadratic in some
expression ``t`` whose two roots are carried on the spec as ``t_1`` and ``t_2``.
The structures differ only in what ``t`` is and in how well the repetition is
disguised:

    power_substitution          t = x^power                 (biquadratic-style)
    linear_square_substitution  t = (inner_a*x + inner_b)^2
    repeated_quadratic_product  t = x^2 + quad_p*x + quad_q, shown as Q(Q+delta)=N
    paired_products             t = (x - pair_center)^2, hidden behind four
                                linear factors that only pair up into a common
                                quadratic
    hidden_shift_substitution   t = x^2 + quad_p*x, hidden behind an expanded
                                quartic

So there is one solver, not five: `_t_roots` reads the reduced equation off the
spec, `_back_substitute` turns one value of t into the x values it yields, and
everything else -- the correct answer, every misconception, the worked solution --
is a different way of walking those two steps.

EXACTNESS. The blueprint builds each equation BACKWARDS from integer x values, so
every root is an integer and no float ever touches one. `_back_substitute`
returns None rather than an approximation when a root would be irrational; the
correct answer treats that as a blueprint bug and raises, while a distractor that
hits it is simply dropped. That way a constraint regression is loud, never a
silently wrong answer key.

Public API mirrors inv_trig.py and quad_eq.py: compute_answer / solution_steps /
build_options.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable, NamedTuple

from .quad_eq import NO_REAL_ROOTS, ROOT_SEP

POWER = "power_substitution"
LINEAR_SQUARE = "linear_square_substitution"
REPEATED = "repeated_quadratic_product"
PAIRED = "paired_products"
HIDDEN = "hidden_shift_substitution"

#: Structures whose substitution is a SQUARE, so a negative t is inadmissible.
#: The distinction matters twice: it drives the domain filter, and it is the
#: difference between the `forgot_square_domain` and `reject_negative_odd_power`
#: misconceptions, which are opposite errors about the same sign.
SQUARE_LIKE = frozenset({LINEAR_SQUARE, PAIRED})


# --------------------------------------------------------------------------- #
# exact integer roots
# --------------------------------------------------------------------------- #
def _int_root(value: int, degree: int) -> int | None:
    """Exact non-negative integer `degree`-th root of `value`, else None.

    `math.isqrt` covers the square case exactly. For higher degrees the float
    estimate is only a starting point -- the neighbours are checked with integer
    arithmetic, so the answer never depends on the float being right.
    """
    if value < 0:
        return None
    if degree == 2:
        root = math.isqrt(value)
        return root if root * root == value else None
    estimate = round(value ** (1.0 / degree)) if value else 0
    for candidate in range(max(estimate - 1, 0), estimate + 2):
        if candidate ** degree == value:
            return candidate
    return None


def _t_roots(spec: dict) -> tuple[int, int]:
    """The two roots of the reduced equation in t, as the blueprint derived them."""
    return spec["t_1"], spec["t_2"]


def _back_substitute(spec: dict, t: int) -> list[int] | None:
    """Every real x satisfying `substitution(x) == t`.

    Returns [] when t is inadmissible (a square that came out negative, or a
    quadratic branch with a negative discriminant) -- that is a normal outcome,
    and the whole point of the domain-filter step. Returns None when a root
    exists but is not an integer, which the blueprint's constraints are supposed
    to rule out.
    """
    structure = spec["structure"]

    if structure == POWER:
        degree = spec["power"]
        if degree % 2 == 0:
            if t < 0:
                return []
            root = _int_root(t, degree)
            if root is None:
                return None
            return [0] if root == 0 else [-root, root]
        # An odd power is a bijection: negative t is admissible and gives exactly
        # one real x, which is the trap `reject_negative_odd_power` walks into.
        root = _int_root(abs(t), degree)
        if root is None:
            return None
        return [root if t >= 0 else -root]

    if structure == LINEAR_SQUARE:
        if t < 0:
            return []
        inner = _int_root(t, 2)
        if inner is None:
            return None
        scale, shift = spec["inner_a"], spec["inner_b"]
        found = set()
        for numerator in {-shift + inner, -shift - inner}:
            if numerator % scale:
                return None
            found.add(numerator // scale)
        return sorted(found)

    if structure == PAIRED:
        if t < 0:
            return []
        offset = _int_root(t, 2)
        if offset is None:
            return None
        centre = spec["pair_center"]
        return [centre] if offset == 0 else [centre - offset, centre + offset]

    # REPEATED / HIDDEN: back-substitution is itself a quadratic, x^2 + p x + (q - t) = 0.
    p, q = spec["quad_p"], spec["quad_q"]
    discriminant = p * p - 4 * (q - t)
    if discriminant < 0:
        return []
    root = _int_root(discriminant, 2)
    if root is None:
        return None
    if (-p + root) % 2:
        return None
    return sorted({(-p + root) // 2, (-p - root) // 2})


def _roots_from(spec: dict, t_values: list[int], *, plus_only: bool = False) -> list[int] | None:
    """Collect, dedupe and sort the x values produced by a list of t values."""
    found: set[int] = set()
    for t in t_values:
        branch = _back_substitute(spec, t)
        if branch is None:
            return None
        if plus_only and branch:
            # "Lost the +-": only the larger of the two back-substitution branches.
            branch = [max(branch)]
        found.update(branch)
    return sorted(found)


def _real_roots(spec: dict) -> list[int]:
    roots = _roots_from(spec, list(_t_roots(spec)))
    if roots is None:
        raise ValueError(
            f"Non-integer root for {spec['structure']} with t={_t_roots(spec)}; "
            "the blueprint's constraints should have excluded this roll"
        )
    return roots


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _render_set(values: list[int]) -> str:
    return ROOT_SEP.join(str(v) for v in values) if values else NO_REAL_ROOTS


def _aggregate(values: list[int], query_type: str) -> str:
    """Turn a root set into whatever `query_type` actually asked for."""
    if not values:
        return NO_REAL_ROOTS
    if query_type == "root_sum":
        return str(sum(values))
    if query_type == "root_product":
        return str(math.prod(values))
    return _render_set(values)


def _answer(spec: dict) -> str:
    return _aggregate(_real_roots(spec), spec["query_type"])


# --------------------------------------------------------------------------- #
# misconceptions
# --------------------------------------------------------------------------- #
# Each helper returns the root set a student holding that specific belief would
# arrive at, or None when the belief leads to a dead end rather than to a wrong
# number (see `_dropped_distractors` in the blueprint). `_distractors` renders
# them, and `build_options` drops any that collapse onto the correct answer --
# but the ones that CAN collapse are ruled out here explicitly instead, because
# a distractor that silently disappears has stopped testing anything.
def _stopped_at_t(spec: dict) -> list[int]:
    """forgot_back_substitution: reported the values of t as if they were x."""
    return sorted(set(_t_roots(spec)))


def _kept_inadmissible_t(spec: dict) -> list[int] | None:
    """forgot_square_domain: back-substituted a negative t as if it were positive.

    Only meaningful where the substitution is a square (or an even power), which
    is exactly where a negative t has to be thrown away.
    """
    structure = spec["structure"]
    if structure not in SQUARE_LIKE and not (structure == POWER and spec["power"] % 2 == 0):
        return None
    t_1, t_2 = _t_roots(spec)
    if min(t_1, t_2) >= 0:
        return None  # nothing was inadmissible, so there is no error to make
    return _roots_from(spec, [abs(t_1), abs(t_2)])


def _rejected_negative_t(spec: dict) -> list[int] | None:
    """reject_negative_odd_power: threw away a negative t that was admissible."""
    if spec["structure"] != POWER or spec["power"] % 2 == 0:
        return None
    kept = [t for t in _t_roots(spec) if t >= 0]
    if len(kept) == 2:
        return None
    return _roots_from(spec, kept)


def _plus_branch_only(spec: dict) -> list[int] | None:
    """lost_plus_minus: took one branch of each back-substitution instead of two.

    An ODD power is one-to-one -- x^3 = t has a single real solution -- so there
    is no +- to lose and this belief produces the correct answer. Returning None
    keeps it off the slate deliberately rather than letting it collapse and get
    silently deduped, which is the difference between a distractor that does not
    apply and one that quietly stopped working.
    """
    if spec["structure"] == POWER and spec["power"] % 2:
        return None
    return _roots_from(spec, list(_t_roots(spec)), plus_only=True)


def _first_branch_only(spec: dict) -> list[int] | None:
    """incomplete_root_aggregation: solved for one value of t and stopped."""
    t_1, t_2 = _t_roots(spec)
    if not _back_substitute(spec, t_2):
        return None  # the second branch contributes nothing; this would be correct
    return _roots_from(spec, [t_1])


def _wrong_delta(spec: dict) -> list[int] | None:
    """wrong_delta_sign: read the second bracket as t - delta instead of t + delta.

    The partner root then comes out as `delta - t_1` rather than `-delta - t_1`.
    Whether that lands on an integer depends on the roll, so the distractor is
    simply skipped when it does not -- `_roots_from` reports that as None.
    """
    if spec["structure"] not in (REPEATED, HIDDEN):
        return None
    t_1 = spec["t_1"]
    return _roots_from(spec, [t_1, spec["delta"] - t_1])


def _rhs_treated_as_zero(spec: dict) -> list[int] | None:
    """rhs_treated_as_zero: read `(..)(..)(..)(..) = N` as though N were 0.

    The signature error of the factored display form -- the four brackets are
    right there, so the temptation is to set each one to zero and ignore the
    right-hand side entirely.
    """
    if spec["structure"] != PAIRED:
        return None
    centre = spec["pair_center"]
    return sorted({centre - spec["pair_offset_1"], centre + spec["pair_offset_1"],
                   centre - spec["pair_offset_2"], centre + spec["pair_offset_2"]})


# Structure-specific misconceptions come FIRST: `build_options` fills four slots
# and stops, so a universal slip listed ahead of them would crowd out the very
# error the item was built to catch.
_MISCONCEPTIONS: tuple[tuple[str, Callable[[dict], "list[int] | None"]], ...] = (
    ("rhs_treated_as_zero", _rhs_treated_as_zero),
    ("reject_negative_odd_power", _rejected_negative_t),
    ("forgot_square_domain", _kept_inadmissible_t),
    ("wrong_delta_sign", _wrong_delta),
    ("incomplete_root_aggregation", _first_branch_only),
    ("lost_plus_minus", _plus_branch_only),
    ("forgot_back_substitution", _stopped_at_t),
)


def _distractors(spec: dict) -> list[tuple[str, str]]:
    query_type = spec["query_type"]
    out: list[tuple[str, str]] = []
    for mid, build in _MISCONCEPTIONS:
        roots = build(spec)
        if roots:
            out.append((mid, _aggregate(roots, query_type)))
    return out


def _pool(spec: dict) -> list[str]:
    """Plausible untagged fillers, used only when the tagged ones leave slots free.

    Perturbations of the correct answer rather than free-floating numbers: a
    slate whose wrong options are obviously unrelated teaches the student to
    pick by shape instead of by solving.
    """
    query_type = spec["query_type"]
    roots = _real_roots(spec)
    candidates: list[list[int]] = [
        [r + 1 for r in roots],
        [r - 1 for r in roots],
        [-r for r in roots],
        sorted(set(roots) | {max(roots) + 1}),
        sorted(set(roots) - {max(roots)}),
        sorted(set(roots) - {min(roots)}),
        [2 * r for r in roots],
    ]
    out = [_aggregate(sorted(set(c)), query_type) for c in candidates if c]
    if query_type in ("root_sum", "root_product"):
        exact = sum(roots) if query_type == "root_sum" else math.prod(roots)
        out += [str(exact + d) for d in (1, -1, 2, -2, 3, -3)] + [str(-exact)]
    return out


# --------------------------------------------------------------------------- #
# worked solution
# --------------------------------------------------------------------------- #
_SUBSTITUTION_TEXT = {
    POWER: "t = x^{degree}",
    LINEAR_SQUARE: "t = ({inner})^2",
    REPEATED: "t = {quad}",
    HIDDEN: "t = {quad}",
    PAIRED: "t = (x - ({centre}))^2",
}


def _substitution_label(spec: dict) -> str:
    quad = f"x^2 + ({spec['quad_p']})x + ({spec['quad_q']})"
    inner = f"{spec['inner_a']}x + ({spec['inner_b']})"
    return _SUBSTITUTION_TEXT[spec["structure"]].format(
        degree=spec["power"], inner=inner, quad=quad, centre=spec["pair_center"]
    )


def _steps(spec: dict) -> list[dict[str, str]]:
    t_1, t_2 = _t_roots(spec)
    structure, query_type = spec["structure"], spec["query_type"]
    admissible = [t for t in (t_1, t_2) if _back_substitute(spec, t)]
    dropped = [t for t in (t_1, t_2) if not _back_substitute(spec, t)]

    steps = [
        {"label": "Найти повторяющееся выражение",
         "detail": "Уравнение не раскрываем: одно и то же выражение встречается "
                   "в двух степенях, отличающихся вдвое, поэтому его можно "
                   "обозначить новой переменной."},
        {"label": "Ввести замену",
         "detail": f"Обозначим {_substitution_label(spec)}. Относительно t "
                   "уравнение становится квадратным."},
        {"label": "Решить уравнение относительно t",
         "detail": f"Корни вспомогательного уравнения: t = {t_1} и t = {t_2}."},
    ]

    if dropped:
        if structure == POWER and spec["power"] % 2:
            reason = ("степень нечётная, поэтому отрицательное значение t допустимо; "
                      "отбрасывать его нельзя")
        elif structure in SQUARE_LIKE or structure == POWER:
            reason = "t — квадрат (чётная степень), поэтому отрицательное значение недопустимо"
        else:
            reason = "при этом t дискриминант обратной замены отрицателен, действительных x нет"
        steps.append({
            "label": "Проверить допустимость значений t",
            "detail": f"Значение t = {dropped[0]} отбрасываем: {reason}. "
                      f"Остаётся t = {admissible[0] if admissible else '—'}.",
        })
    else:
        steps.append({
            "label": "Проверить допустимость значений t",
            "detail": "Оба значения t допустимы, поэтому обратную замену нужно "
                      "выполнить для каждого из них.",
        })

    for t in admissible:
        branch = _back_substitute(spec, t) or []
        steps.append({
            "label": f"Обратная замена при t = {t}",
            "detail": f"Получаем x из множества {{{_render_set(branch)}}}.",
        })

    roots = _real_roots(spec)
    if query_type == "root_sum":
        final = (f"Действительные корни: {_render_set(roots)}. "
                 f"Их сумма равна {sum(roots)}.")
    elif query_type == "root_product":
        final = (f"Действительные корни: {_render_set(roots)}. "
                 f"Их произведение равно {math.prod(roots)}.")
    else:
        final = f"Все действительные корни: {_render_set(roots)}."
    steps.append({"label": "Собрать ответ", "detail": final})
    return steps


# --------------------------------------------------------------------------- #
# dispatch / public API
# --------------------------------------------------------------------------- #
class _Solver(NamedTuple):
    answer: Callable[[dict], str]
    distractors: Callable[[dict], list[tuple[str, str]]]
    steps: Callable[[dict], list[dict[str, str]]]
    pool: Callable[[dict], list[str]]


_DISPATCH: dict[str, _Solver] = {
    "higher_degree_substitution": _Solver(_answer, _distractors, _steps, _pool),
}
ANSWER_TYPES = frozenset(_DISPATCH)


def compute_answer(answer_type: str, spec: dict) -> str:
    """The exact correct answer: a root set, or the sum/product of the real roots."""
    return _DISPATCH[answer_type].answer(spec)


def solution_steps(answer_type: str, spec: dict) -> list[dict[str, str]]:
    """The deterministic derivation the Tutor reverse-engineers a mistake against."""
    return _DISPATCH[answer_type].steps(spec)


def build_options(answer_type: str, spec: dict, n_options: int = 4) -> list[dict[str, Any]]:
    """One correct option plus distractors, each tagged with its misconception.

    Same contract as quad_eq.build_options: dedup by rendered text, named
    misconceptions first, pool top-up carrying an empty tag, shuffled.
    """
    solver = _DISPATCH[answer_type]
    correct = solver.answer(spec)
    options = [{"text": correct, "is_correct": True, "misconception": ""}]
    seen = {correct}

    for mid, text in solver.distractors(spec):
        if len(options) >= n_options:
            break
        if text in seen:
            continue
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
