"""
The Tutor: the one agent that runs while a student is waiting.

Everything else in this engine is deterministic or cached. This is the exception
-- a live model call, per (question, chosen option), reviewing a finished attempt.

It is also the part that spends the misconception work. The bank stores the
blueprint's own name for the mistake behind every wrong option:

    student picked "84"   ->   misconception = "doubled_instead_of_squared"

so the Tutor is not guessing what went wrong. It is told. That is the difference
between "let me see where you might have slipped" and "you squared 7 to get 14
instead of 49" -- and it is why the 206 bare d1..d6 ids were renamed.

Django-free on purpose, like the rest of the engine except publisher.py. It takes
plain dicts and strings; apps/assessments/services.py does the model mapping and
owns the TutorNote cache.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from config import TUTOR_MODEL

from . import prompts
from .llm import chat_anthropic
from .loader import BlueprintError, topic_meta
from .state import Outcome, TutorRequest

# The keys a UBT reproduction record always has and a MAIQE one never does.
# Used to route a question to the right Tutor rather than feeding a UBT record to
# a builder that would report its answer as "(unknown)".
_UBT_SOLUTION_KEYS = frozenset({"topic", "mode", "seed", "answer_latex"})

# A wrong or skipped answer earns a full walkthrough; a correct one earns two or
# three sentences, because the student only needs to confirm the method.
MAX_TOKENS_FULL = 600
MAX_TOKENS_RECAP = 250


def owns(solution: Mapping[str, Any] | None) -> bool:
    """True when this question came from the UBT blueprint engine."""
    return bool(solution) and _UBT_SOLUTION_KEYS <= set(solution)


def outcome_for(*, answered: bool, is_correct: bool) -> Outcome:
    """Which branch of the single Tutor prompt this attempt needs.

    An unanswered question is "skipped", not "wrong". The distinction matters:
    there is no mistake to diagnose, and opening a review by naming an error the
    student never made is worse than saying nothing.
    """
    if not answered:
        return "skipped"
    return "correct" if is_correct else "wrong"


def build_request(
    *,
    solution: Mapping[str, Any],
    text: str,
    language: str,
    outcome: Outcome,
    student_answer_latex: str = "",
    chosen_distractor_id: str = "",
) -> TutorRequest:
    """Assemble the Tutor's input from the STORED record.

    Reads Question.solution; never regenerates the question. The record carries
    the seed, so regenerating would work -- and would introduce a way for the
    explanation and the question a student actually saw to drift apart. The row
    is the truth.
    """
    topic = solution.get("topic", "")
    try:
        display_name = topic_meta(topic)["display_name"] if topic else ""
    except BlueprintError:
        # The topic was renamed or removed after this question was published.
        # Not worth failing a review over; the mode still names the method.
        display_name = topic

    request: TutorRequest = {
        "topic": topic,
        "display_name": display_name,
        "mode": solution.get("mode", ""),
        "difficulty": solution.get("difficulty", 1),
        "language": language,
        "text": text,
        "context": {
            **(solution.get("parameters") or {}),
            **(solution.get("derived") or {}),
        },
        "answer_latex": solution.get("answer_latex", ""),
        "outcome": outcome,
    }
    if outcome == "wrong":
        request["student_answer_latex"] = student_answer_latex
        request["chosen_distractor_id"] = chosen_distractor_id
    return request


def render_request(request: TutorRequest) -> str:
    """The user turn.

    `mode` is labelled as the method under test because that is exactly what it
    is: the blueprint mode IS the technique the item examines, and naming it
    saves the model from having to infer the intended approach from the numbers.
    """
    lines = [
        f"TOPIC: {request.get('display_name') or request.get('topic')}",
        f"MODE (the method this item tests): {request.get('mode')}",
        f"DIFFICULTY: {request.get('difficulty')}",
        "",
        "QUESTION AS THE STUDENT SAW IT:",
        request.get("text", ""),
        "",
        f"VALUES USED: {json.dumps(request.get('context') or {}, ensure_ascii=False)}",
        f"CORRECT ANSWER (authoritative, computed symbolically): "
        f"{request.get('answer_latex')}",
        "",
        f"OUTCOME: {request.get('outcome')}",
    ]

    if request.get("outcome") == "wrong":
        lines.append(f"STUDENT'S ANSWER: {request.get('student_answer_latex') or '(unknown)'}")
        misconception = request.get("chosen_distractor_id") or ""
        if misconception:
            lines.append(
                f"MISCONCEPTION BEHIND THAT OPTION (the item bank's own name for "
                f"it): {misconception}"
            )
        else:
            lines.append(
                "MISCONCEPTION: not recorded for this option — infer the error by "
                "comparing the student's answer with the correct one."
            )

    return "\n".join(lines)


def explain(request: TutorRequest, *, model: str = TUTOR_MODEL) -> str:
    """One review, as prose for the student. The only live model call here."""
    max_tokens = (
        MAX_TOKENS_RECAP if request.get("outcome") == "correct" else MAX_TOKENS_FULL
    )
    return chat_anthropic(
        prompts.tutor_system(request["language"]),
        render_request(request),
        model=model,
        max_tokens=max_tokens,
    ).strip()
