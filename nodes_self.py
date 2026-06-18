"""
MAIQE — graph nodes, built one agent at a time.

A "node" is a function that takes the shared GraphState and returns a partial
update dict (LangGraph merges it back into the state). Nodes stay thin: the
real work lives in the modules they call.

    state.py        -> GraphState (the shared base)
    math_engine.py  -> the deterministic math (pure, testable, no LLM)

This file currently contains Agent 1 (Architect) and Agent 2 (Storyteller).
The Critic, Publisher, and Tutor nodes will be added here later.

HOW THIS CONNECTS TO THE DJANGO BACKEND
---------------------------------------
The graph's job is to manufacture one `assessments.Question` (plus its
`AnswerOption`s) tagged with one `content.Tag`. The Architect does NOT touch
the database — it only computes the payload the later Publisher node persists.
See GraphState in state.py for the field-by-field mapping.
"""

from __future__ import annotations

import os
from typing import Any

from llm import chat_openai
from math_engine import (
    compute_answer_key,
    generate_math_spec,
    load_blueprint,
    render_constraints,
    resolve_difficulty,
)
from state import GraphState


# ---------------------------------------------------------------------------
# Agent 1 — The Architect (deterministic Python, no LLM)
# ---------------------------------------------------------------------------
def architect_node(state: GraphState) -> dict[str, Any]:
    """Turn a topic + student profile into the full math payload for a Question.

    Steps:
      1. Load the blueprint JSON for the requested topic.
      2. Decide difficulty from the student's ENT target score.
      3. Roll random-but-valid numbers at that difficulty.
      4. Compute the correct answer natively.
      5. Render the numbers into the Jinja template -> rigid text spec.
    """
    blueprint = load_blueprint(state["topic"])

    difficulty = resolve_difficulty(state.get("student_profile", {}), blueprint)
    math_spec = generate_math_spec(blueprint, difficulty)
    answer_key = compute_answer_key(blueprint, math_spec)
    constraints_payload = render_constraints(blueprint, math_spec)

    tag = blueprint["tag"]
    # Return only the keys this agent owns; LangGraph merges them into State.
    return {
        "math_spec": math_spec,
        "answer_key": answer_key,
        "constraints_payload": constraints_payload,
        "difficulty": difficulty,
        "tag_slug": tag["slug"],
        "tag_name": tag["name"],
    }


# ---------------------------------------------------------------------------
# Agent 2 — The Storyteller (fast, cheap LLM)
# ---------------------------------------------------------------------------
STORYTELLER_SYSTEM = (
    "You translate rigid math constraints into an engaging, original word "
    "problem for Kazakhstani 10-11th graders (ЕНТ/UNT prep). Rules:\n"
    "- Keep every number EXACTLY as given; never invent or change a value.\n"
    "- Pick a fresh, concrete real-world setting (e.g. Astana construction, "
    "space logistics, a local cafe) so students cannot pattern-match.\n"
    "- Vary sentence structure between problems.\n"
    "- Render all formulas as native LaTeX wrapped in Markdown ($...$).\n"
    "- Output ONLY the problem statement. Never reveal or hint at the answer."
)


def storyteller_node(state: GraphState) -> dict[str, Any]:
    """Draft (or redraft) the problem text from the Architect's constraints.

    Reads `constraints_payload` (the rigid math spec). If the Critic has looped
    back with `rewrite_notes`, those are appended so the redraft addresses them.
    Produces `draft_text` -> later becomes assessments.Question.text.
    """
    user_prompt = state["constraints_payload"]
    if state.get("rewrite_notes"):
        user_prompt += f"\n\nEditor rewrite notes to address:\n{state['rewrite_notes']}"

    draft = chat_openai(
        STORYTELLER_SYSTEM,
        user_prompt,
        model=os.getenv("STORYTELLER_MODEL", "gpt-5-mini"),
    )
    return {"draft_text": draft.strip()}
