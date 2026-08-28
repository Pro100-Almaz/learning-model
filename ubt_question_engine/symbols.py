r"""
The deterministic half of translation QA.

13 of the 179 instructions carry live mathematics inside their prose:

    Use S=abc/(4R) to find the circumradius R.
    Given \vec e_1=(1,1) and \vec e_2=(1,-1), find (m,n) if \vec v=m\vec e_1+n\vec e_2.
    In a 30 deg-60 deg-90 deg triangle, the side opposite 30 deg is given.
    Two similar figures have similarity coefficient k. Find the ratio S_2/S_1.

A translation model will cheerfully "fix" those: `4R` acquires a Cyrillic Р
because the surrounding sentence is Cyrillic, `f^{-1}` loses its braces, a degree
sign gets localized. Each of those silently changes the mathematics of every
question of that mode.

So the symbols are checked here, in Python, character for character, BEFORE the
Critic is asked anything. Two reasons. It is free and exact, where a model asked
to grade its own output is neither. And it lets the Critic prompt say "do not
report symbol mismatches, the code catches those", which keeps the model on the
judgements only it can make.
"""

from __future__ import annotations

import re

# Ordered longest-construct-first: a run like `\vec e_1=(1,1)` must be captured
# whole, not shredded into `\vec` and a stray `1`.
_SYMBOL_PATTERNS = [
    r"\\[a-zA-Z]+(?:\{[^{}]*\}|\[[^\[\]]*\])*",     # \vec, \text{...}, \sqrt[3]{...}
    r"[A-Za-z][_^]\{[^{}]*\}",                      # f^{-1}, a_{14}
    r"[A-Za-z][_^]-?[A-Za-z0-9]+",                  # S_2, b^t, P_0
    r"[A-Za-z0-9)]+\s*=\s*[A-Za-z0-9(][A-Za-z0-9()/*+^_.-]*",  # S=abc/(4R), P(t)=P_0
    r"\d+\s*[°]",                                   # 30°
    r"\d+\s*:\s*\d+",                               # 2:1
    r"\d+",                                         # any bare number
]

_SYMBOL_RE = re.compile("|".join(f"(?:{p})" for p in _SYMBOL_PATTERNS))


def symbols(text: str) -> list[str]:
    """Every run of `text` that is mathematics rather than prose."""
    return [match.group(0).strip() for match in _SYMBOL_RE.finditer(text)]


# An answer label is `\text{parallel lines}`: the wrapper is mathematics and must
# survive, the words inside are the thing being translated and must not.
_LABEL_RE = re.compile(r"^\s*\\text\{(?P<inner>.*)\}\s*$", re.DOTALL)


def check(source: str, translation: str, *, label: bool = False) -> list[str]:
    """Symbol runs from `source` that did not survive into `translation`.

    Empty list means the mathematics came through untouched. Compares by
    containment rather than by position: word order legitimately changes between
    English and Kazakh, but a formula does not change its characters.

    With `label=True` the \\text{...} wrapper is checked structurally and only
    its contents are compared -- otherwise the check would demand that the
    English words survive translation, which is the opposite of the job.
    """
    if label:
        match = _LABEL_RE.match(translation)
        if match is None:
            return [r"\text{...} wrapper"]
        source_match = _LABEL_RE.match(source)
        source = source_match.group("inner") if source_match else source
        translation = match.group("inner")

    return [symbol for symbol in symbols(source) if symbol not in translation]


def describe(problems: list[str]) -> str:
    """The rewrite instruction handed back to the model on a failed check."""
    if problems == [r"\text{...} wrapper"]:
        return (
            r"Your output must be exactly one \text{...} group: the wrapper is "
            r"LaTeX and is copied unchanged, and only the words inside it are "
            r"translated. Output \text{<translated words>} and nothing else."
        )
    listed = ", ".join(f"`{p}`" for p in problems)
    return (
        f"These exact character sequences from the source are missing from your "
        f"output: {listed}. Reproduce every one of them verbatim, in Latin "
        f"letters and Arabic numerals, exactly as they appear in the source. Do "
        f"not translate, re-typeset or substitute look-alike characters."
    )
