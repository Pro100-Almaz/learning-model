r"""
Cosmetic repair of rendered LaTeX statements.

The only module in the engine allowed to rewrite a string that already contains
mathematics. Keeping it alone in a file is what makes that rule checkable by
reading an import list.

Blueprint templates splice signed values into fixed text -- `{a}x^2{b:+d}x{c:+d}`
-- and a value of 0 or 1 or a negative meeting a literal minus produces LaTeX no
teacher would write:

    f(x)=4x^2-7x+0        instead of   f(x)=4x^2-7x
    \int(1x-6)\,dx        instead of   \int(x-6)\,dx
    y=-x^2+1x--2          instead of   y=-x^2+x+2

Every rewrite here is an *identity*: -(-2) is +2, 1x is x, +0 at the end of an
expression is nothing. None of them changes the value of the expression, and
none of them is allowed to. A transformation that changes what the question asks
does not belong in a presentation module -- it belongs in the blueprint's
constraints, where it can be reviewed as mathematics.

Deliberately NOT handled: a zero coefficient that kills a whole term, as in
`x^2+0x` or `\sin^2x+0\sin x\cos x`. Deleting the `+0` there leaves the term's
variables stranded (`x^2x`), and deleting the whole term is expression
simplification, not display. Those are degenerate questions -- a homogeneous trig
equation with a zero middle coefficient is a different, easier question than the
one intended -- so they are fixed with a `!= 0` constraint in the blueprint.
"""

from __future__ import annotations

import re

# Applied in order; each is (pattern, replacement). An ordered list rather than
# chained .replace() calls so the order is visible and testable.
_RULES: list[tuple[re.Pattern[str], str]] = [
    # 1. Adjacent signs. A literal minus in a template meeting a negative value:
    #    `-{product_roots}` with product_roots = -2 renders `--2`. Rewriting to
    #    `+2` is arithmetic identity, not cosmetics.
    (re.compile(r"--"), "+"),
    (re.compile(r"\+-"), "-"),
    (re.compile(r"-\+"), "-"),
    (re.compile(r"\+\+"), "+"),

    # 2a. Unit coefficient before a variable: `1x` -> `x`.
    #     The lookbehind is the whole safety argument. It must reject a `1` that
    #     is part of a longer number (`21x`), part of an identifier or subscript
    #     (`x_1\le x`, which would otherwise become the nonsense `x_\le x`), a
    #     decimal (`0.1x`), or already inside braces (`x^{1}`, `\frac{1}{2}`).
    (re.compile(r"(?<![0-9A-Za-z}_^.])1(?=[a-zA-Z])"), ""),

    # 2b. Unit coefficient before a macro: `1\sin x` -> `\sin x`.
    #     A whitelist, not a bare `\\`, because a backslash after a 1 is far
    #     more often a relation than a factor: in `-1\le x\le 2` the 1 is the
    #     bound itself, and dropping it silently changes the question. Only
    #     macros that can be multiplied by a coefficient are listed.
    (
        re.compile(
            r"(?<![0-9A-Za-z}_^.])1(?=\\(?:sin|cos|tan|cot|sec|csc|arcsin|arccos"
            r"|arctan|log|ln|exp|sqrt|frac|left|pi)\b)"
        ),
        "",
    ),

    # 3. Zero constant at the end of an expression: `...-7x+0,` -> `...-7x,`.
    #    The 0 must be genuinely the last thing in its expression, so `+0x` (a
    #    killed term) is left for the blueprint to fix, and `+0.5` is untouched.
    #
    #    The trailing delimiter is a whitelist of relations and spacing macros,
    #    NOT a bare `\\`. A bare backslash also matches `\sin`, which turns
    #    `\sin^2x+0\sin x\cos x-\cos^2x=0` into `\sin^2x\sin x\cos x-\cos^2x=0`
    #    -- a different equation, shipped silently. That is the exact failure
    #    this module must never commit, so the macro list is explicit.
    (
        re.compile(
            r"[+-]0(?![0-9.])"
            # `}` closes a group, so `\sqrt{x+0}` and `2^{x+0}` are trailing
            # zeros. `(` is deliberately absent: `+0(x+5)` is a zero coefficient
            # on a parenthesised factor, and deleting only the `+0` would strand
            # the factor as `4(x+5)^3(x+5)`. That one is a blueprint constraint.
            r"(?=$|[,)}\s<>=]|\\\\"
            r"|\\(?:leq|le|geq|ge|neq|ne|lt|gt|qquad|quad|end|right|cup|cap|in)"
            r"(?![a-zA-Z]))"
        ),
        "",
    ),
]


def tidy(latex: str) -> str:
    """Repair the display artefacts of splicing values into a fixed template.

    Must run *after* str.format: before it, the signs live inside `{b:+d}`
    placeholders and none of these patterns can see them.

    Must not run over sympy.latex output: that never emits `1x` or `+0`, and
    running a regex across correct output is how correct output eventually stops
    being correct.
    """
    for pattern, replacement in _RULES:
        latex = pattern.sub(replacement, latex)
    return latex


# The cases that justify each rule, and the traps each rule must not spring.
# Kept next to the regexes rather than in the test file three parts away, so the
# reasoning and the evidence stay in one place. tests/ imports this list.
TIDY_CASES: list[tuple[str, str]] = [
    # --- rule 1: adjacent signs (real statements from the collection) ---
    (r"y=-x^2+1x--2,\qquad -1\le x\le 2", r"y=-x^2+x+2,\qquad -1\le x\le 2"),
    (r"x^2+-5x+6=0", r"x^2-5x+6=0"),

    # --- rule 2: unit coefficient ---
    (r"\int_{-1}^{3}\left(1x-6\right)\,dx", r"\int_{-1}^{3}\left(x-6\right)\,dx"),
    (r"f(x)=1x^{2}-7x", r"f(x)=x^{2}-7x"),
    (r"1\sin x+2\cos x", r"\sin x+2\cos x"),
    (r"\sin^2x-1\cos^2x=0", r"\sin^2x-\cos^2x=0"),

    # --- rule 2: traps it must NOT touch ---
    (r"21x+3", r"21x+3"),                       # part of a longer number
    (r"x_1\le x\le x_2", r"x_1\le x\le x_2"),   # subscript, not a coefficient
    (r"x^{1}+a_{1}", r"x^{1}+a_{1}"),           # already braced
    (r"\frac{1}{2}x", r"\frac{1}{2}x"),         # numerator
    (r"0.1x", r"0.1x"),                         # decimal
    (r"a_1=3,\quad a_{14}=107", r"a_1=3,\quad a_{14}=107"),
    (r"-1\le x\le 2", r"-1\le x\le 2"),         # a bound, not a coefficient
    (r"x\ge 1\quad y=2", r"x\ge 1\quad y=2"),   # \quad is not multiplicable

    # --- rule 3: trailing zero constant ---
    (r"f(x)=4x^2-7x+0,\qquad x_0=3", r"f(x)=4x^2-7x,\qquad x_0=3"),
    (r"3x+0\le 18", r"3x\le 18"),
    (r"x^2-5x+0<0", r"x^2-5x<0"),
    (r"4(x-3)^3-7(x-3)+0", r"4(x-3)^3-7(x-3)"),
    # No word boundary between the `e` of \le and the `0`, which is why the
    # macro list ends in a not-a-letter lookahead rather than \b.
    (r"x^2-2x+0\le0", r"x^2-2x\le0"),
    (r"x^2+0\ge5", r"x^2\ge5"),
    (r"\left(x+0\right)", r"\left(x\right)"),
    (r"\sqrt{x+0}\ge 6", r"\sqrt{x}\ge 6"),
    (r"\frac{x^2+0}{x-2}=9", r"\frac{x^2}{x-2}=9"),
    (r"2^{x+0}>2^{-3}", r"2^{x}>2^{-3}"),
    # a zero coefficient on a parenthesised factor: not this module's to remove
    (r"4(x+5)^3+0(x+5)-7", r"4(x+5)^3+0(x+5)-7"),
    # \left must NOT be read as \le + "ft"
    (r"3x+0\left(y\right)", r"3x+0\left(y\right)"),

    # --- rule 3: traps it must NOT touch ---
    (r"y=0.5x+2", r"y=0.5x+2"),                 # decimal, not a zero term
    (r"f(x)=x^2+0x", r"f(x)=x^2+0x"),           # killed term: blueprint's job
    (r"x^{10}+1", r"x^{10}+1"),                 # zero inside a number
    # The one that must never be "tidied": deleting +0 here strands \sin x\cos x
    # against the previous term and silently changes the equation.
    (r"\sin^2x+0\sin x\cos x-\cos^2x=0", r"\sin^2x+0\sin x\cos x-\cos^2x=0"),
    (r"x^2+0x-16<0 \\ x^2-2x-15<0", r"x^2+0x-16<0 \\ x^2-2x-15<0"),
    (r"3x+0 \\ y=2", r"3x \\ y=2"),             # zero constant before a line break
]
