"""Registry of the dedicated exact-answer engines.

Three call sites used to carry the same `if kind in X.ANSWER_TYPES / elif kind in
Y.ANSWER_TYPES / else` chain — `math_ques_types.compute_answer_key`,
`math_engine.build_solution` and `nodes_self.architect_node`. Every new answer
family meant editing all three in the same shape, and forgetting one produces a
question whose answer, worked solution and option slate disagree with each other.

The modules below all expose the identical surface:

    ANSWER_TYPES    frozenset[str]  -- the answer.type values it owns
    compute_answer(answer_type, spec) -> str
    solution_steps(answer_type, spec) -> list[dict[str, str]]
    build_options(answer_type, spec, n_options=...) -> list[dict]

so one lookup replaces the chain. Adding a family is now: write the module, add
it here, done.
"""

from __future__ import annotations

from types import ModuleType

from . import higher_degree, inv_trig, quad_eq

#: Ordered only for determinism; the ANSWER_TYPES sets are disjoint.
MODULES: tuple[ModuleType, ...] = (inv_trig, quad_eq, higher_degree)

ANSWER_TYPES = frozenset().union(*(m.ANSWER_TYPES for m in MODULES))

_BY_TYPE: dict[str, ModuleType] = {
    kind: module for module in MODULES for kind in module.ANSWER_TYPES
}

if len(_BY_TYPE) != sum(len(m.ANSWER_TYPES) for m in MODULES):  # pragma: no cover
    raise RuntimeError("two answer engines claim the same answer.type")


def module_for(answer_type: str) -> ModuleType | None:
    """The engine owning this answer type, or None if a declarative one handles it."""
    return _BY_TYPE.get(answer_type)
