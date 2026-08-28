r"""LaTeX tidying and symbol preservation.

Both modules rewrite or inspect strings that contain live mathematics, which
makes them the two places in the engine where a bug is silent: a wrong regex here
does not crash, it ships a different equation. The evidence lives beside the code
(latex.TIDY_CASES) and is imported rather than duplicated, so a new rule and its
proof are always added together.
"""

from __future__ import annotations

import pytest

from ubt_question_engine.latex import TIDY_CASES, tidy
from ubt_question_engine.symbols import check, describe, symbols

# --- tidy -------------------------------------------------------------------


@pytest.mark.parametrize("source,expected", TIDY_CASES, ids=range(len(TIDY_CASES)))
def test_tidy_cases(source, expected):
    assert tidy(source) == expected


def test_tidy_is_idempotent():
    """Applying it twice must change nothing the first pass did not.

    Rendering runs tidy once, but a non-idempotent rule is a rule that is eating
    something it should not, and this catches that cheaply.
    """
    for source, _ in TIDY_CASES:
        once = tidy(source)
        assert tidy(once) == once, source


def test_tidy_never_touches_a_term_killing_zero():
    r"""`+0x` and `+0\sin x` stay: removing them strands the rest of the term.

    Those are degenerate questions, fixed by a `!= 0` constraint in the
    blueprint. Deleting only the `+0` would turn `x^2+0x` into `x^2x`, and
    `\sin^2x+0\sin x\cos x-\cos^2x=0` into a different equation entirely.
    """
    assert tidy(r"f(x)=x^2+0x") == r"f(x)=x^2+0x"
    assert tidy(r"\sin^2x+0\sin x\cos x-\cos^2x=0") == r"\sin^2x+0\sin x\cos x-\cos^2x=0"
    assert tidy(r"4(x+5)^3+0(x+5)-7") == r"4(x+5)^3+0(x+5)-7"


def test_tidy_leaves_bounds_and_subscripts_alone():
    """A 1 is not always a coefficient."""
    assert tidy(r"-1\le x\le 2") == r"-1\le x\le 2"
    assert tidy(r"x_1\le x\le x_2") == r"x_1\le x\le x_2"
    assert tidy(r"a_1=3,\quad a_{14}=107") == r"a_1=3,\quad a_{14}=107"


# --- symbols ----------------------------------------------------------------


def test_identity_translation_always_passes(topics):
    """A string checked against itself can never report a missing symbol.

    Any false positive here would make the build script reject perfectly good
    translations forever, so it is worth asserting over the whole real corpus.
    """
    from ubt_question_engine import i18n

    for source, meta in i18n.translatable().items():
        label = meta["kind"] == i18n.KIND_ANSWER_LABEL
        assert check(source, source, label=label) == [], source


@pytest.mark.parametrize(
    "source,bad_translation,expected",
    [
        # a Cyrillic Р swapped in for a Latin R
        ("Use S=abc/(4R) to find the circumradius R.",
         "S=abc/(4Р) арқылы R табыңыз.",
         "S=abc/(4R)"),
        # braces dropped from an exponent
        (r"Find the inverse function f^{-1}(x)", "f^-1(x) кері функциясын табыңыз",
         "f^{-1}"),
        # a whole formula dropped
        ("Find the ratio of their areas S_2/S_1.", "Аудандарының қатынасын табыңыз.",
         "S_2"),
    ],
)
def test_symbol_check_catches_real_corruptions(source, bad_translation, expected):
    problems = check(source, bad_translation)
    assert expected in problems
    assert describe(problems)


def test_degree_signs_are_mathematics_not_words():
    source = "In a 30°–60°–90° triangle, find the side."
    localized_degrees = "Үшбұрышта 30 градус"
    assert "30°" in check(source, localized_degrees)


def test_answer_label_translates_its_words_but_keeps_its_wrapper():
    r"""The whole point of a label is that the English inside it changes."""
    source = r"\text{parallel lines}"
    good = r"\text{параллель түзулер}"
    assert check(source, good, label=True) == []

    # ...but the \text{...} wrapper is LaTeX and must survive.
    assert check(source, "параллель түзулер", label=True) == [r"\text{...} wrapper"]
    assert "wrapper" in describe([r"\text{...} wrapper"])


def test_symbols_finds_nothing_in_plain_prose():
    assert symbols("Simplify") == []
    assert symbols("Find the area of the triangle") == []
