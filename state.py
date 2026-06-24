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
        solution            ->  Question.solution  (worked steps; the Tutor's ground truth)
        answer_key          ->  drives which AnswerOption.is_correct = True
        answer_options      ->  the AnswerOption rows (text + is_correct + misconception tag)
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
    solution: dict[str, Any]         # structured worked solution -> Question.solution; the Tutor's ground truth
    answer_options: list[dict[str, Any]]  # {text, is_correct, misconception} -> assessments.AnswerOption rows
    difficulty: int                  # 1-3, maps to assessments.Question.difficulty
    tag_slug: str                    # maps to a content.Tag (Question.tags M2M)
    tag_name: str                    # human label of that Tag

    # --- Storyteller / Critic loop ---
    draft_text: str                  # current problem draft (LaTeX in $...$); -> Question.text
    rewrite_notes: str               # Critic feedback the Storyteller must address (optional)

    # Critic Agent
    critic_passed: bool
    revision_count: int

    # --- dedup (Architect computes it; Publisher enforces it) ---
    content_hash: str                # sha256 of topic+math_spec -> Question.content_hash (unique)

    # --- what the Publisher produces ---
    explanation: str                 # solution explanation -> Question.explanation (optional input)
    question_id: int                 # PK of the assessments.Question (existing one if was_duplicate)
    was_duplicate: bool              # True if this problem was already in the bank; no new row written
    lesson_id: int                   # content.Lesson the question was linked to (None if none teaches its tag)
    test_id: int                     # the lesson's micro Test the question joined (None if no lesson)

