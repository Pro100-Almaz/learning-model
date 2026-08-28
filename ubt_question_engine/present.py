"""
Rendering one generated question into its English source statement.

Sits between the generator and everything that reads a question in prose: the
Localizer prompt (part 8), the Contextualizer, and preview.py. All of them go
through `render_question`, so the block a human proof-reads in the terminal is
byte-for-byte the block a model is asked to translate.

Presentation only. Nothing here computes, rounds, or reformats a value -- by the
time state arrives, every string in it is final.
"""

from __future__ import annotations

from pathlib import Path
from string import ascii_uppercase

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .state import EngineState

TEMPLATE_ROOT = Path(__file__).parent / "templates"

# Built once at import, not per call: a preview run over 58 topics would
# otherwise recompile the same template 58 times.
#
# autoescape stays False, and that is not an oversight. HTML escaping would turn
# `<`, `>` and `&` into entities, so `x^2-5x<0` would reach the model as
# `x^2-5x&lt;0`. There is no HTML anywhere in this pipeline.
#
# StrictUndefined turns a typo'd variable into an error instead of an empty
# string -- a silently missing question body is the worst failure available here.
_ENVIRONMENT = Environment(
    loader=FileSystemLoader(TEMPLATE_ROOT),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


def _option_rows(state: EngineState, show_answers: bool) -> list[dict[str, str]]:
    """Options as flat label/latex/marker rows.

    Built here rather than branched on inside the template for two reasons. The
    template stays free of conditionals, and -- less obviously -- a Jinja line
    that ends in `{% endif %}` loses its newline to trim_blocks, which silently
    collapses all five options onto one line.
    """
    rows: list[dict[str, str]] = []
    for index, choice in enumerate(state.get("answer_options") or []):
        marker = ""
        if show_answers:
            marker = (
                "   <-- correct" if choice["is_correct"]
                else f"   [{choice['distractor_id']}]"
            )
        rows.append(
            {
                "label": ascii_uppercase[index],
                "latex": choice["latex"],
                "marker": marker,
            }
        )
    return rows


def render_question(state: EngineState, show_answers: bool = False) -> str:
    """The question as a human (and the Localizer) should read it.

    `show_answers` appends a marker to the correct option and each distractor's
    misconception id. It only ever *appends*: the shape stays identical either
    way, so what a reviewer approves is what the model is later given.
    """
    template = _ENVIRONMENT.get_template("ubt_question.j2")
    return template.render(
        instruction=state.get("instruction", ""),
        latex=state.get("latex", ""),
        text_context=state.get("text_context") or {},
        options=_option_rows(state, show_answers),
    )
