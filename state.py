"""
Shared state for the MAIQE question-generation graph.

`GraphState` is the single dict that threads through every agent. It lives in
its own module because *every* node imports it — keeping it here avoids each
agent file importing from another agent file.
"""

from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    """The single dict that flows through every agent.

    `total=False` makes every key optional: early agents fill the first keys,
    later agents add the rest. Today only the Architect's fields are populated.

    Mapping of Architect outputs to the Django backend (apps/assessments/models.py):

        GraphState key      ->  Django field
        -----------------       ------------------------------------------
        constraints_payload ->  (input to Storyteller; becomes Question.text)
        answer_key          ->  drives which AnswerOption.is_correct = True
        difficulty          ->  Question.difficulty  (PositiveSmallInt, 1-3)
        tag_slug / tag_name ->  Question.tags  (M2M to content.Tag; >=1 required)
    """

    # --- input you provide before running the graph ---
    topic: str                       # blueprint name, e.g. "quadratic_equations"
    student_profile: dict[str, Any]  # serialized accounts.StudentProfile (optional)

    # --- what the Architect produces ---
    math_spec: dict[str, Any]        # the concrete numbers rolled for this problem
    answer_key: Any                  # the correct answer, computed natively in Python
    constraints_payload: str         # rendered rigid spec; the Storyteller's input
    difficulty: int                  # 1-3, maps to assessments.Question.difficulty
    tag_slug: str                    # maps to a content.Tag (Question.tags M2M)
    tag_name: str                    # human label of that Tag

    # --- Storyteller / Critic loop ---
    draft_text: str                  # current problem draft (LaTeX in $...$); -> Question.text
    rewrite_notes: str               # Critic feedback the Storyteller must address (optional)

    # Critic Agent
    critic_passed: bool
    revision_count: int

    # --- what the Publisher produces ---
    explanation: str                 # solution explanation -> Question.explanation (optional input)
    question_id: int                 # PK of the assessments.Question the Publisher created

