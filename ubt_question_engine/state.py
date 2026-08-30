"""
Shared state for the UBT question engine.

One dict threads through the pipeline; every module imports it from here so no
engine module has to import another engine module just to name a shape.

Not a LangGraph state: this engine has no cyclic agent graph. Generation is a
straight deterministic call (roll -> compute -> validate -> render). The only
loop is Localizer <-> Critic, and it runs once per (topic, mode, language), not
per question. Hence `EngineState`, not `GraphState`.

Pipeline that fills it:

    generator   -> mode, parameters, derived, instruction, latex, answer_*,
                   answer_options, difficulty, tag_*, solution, content_hash
    localizer   -> text            (plain topics)
    contextualizer -> text         (word-problem modes only)
    critic      -> passed, rewrite_text, tries
    publisher   -> question_id, was_duplicate, lesson_id, test_id

Mapping onto the Django backend (apps/assessments/models.py), consumed by
`assessments.services.publish_generated_question`:

    EngineState key   ->  Django field
    ---------------       ------------------------------------------
    text              ->  Question.text
    language          ->  Question.language
    difficulty        ->  Question.difficulty  (1-3)
    answer_options    ->  AnswerOption rows (text / is_correct / misconception)
    solution          ->  Question.solution  (JSON; reproduction record)
    content_hash      ->  Question.content_hash  (unique; dedup)
    tag_slug/tag_name ->  Question.tags  (M2M to content.Tag; >=1 required)
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

# ISO codes accepted engine-wide. Must stay in sync with prompts.LANGUAGE_LABELS
# or prompts._label() raises.
Language = Literal["kk", "ru", "en"]

# 1 = one known rule applied directly, 2 = recognise + transform, 3 = several
# connected transformations (architecture doc section 3).
Difficulty = Literal[1, 2, 3]

# How the student left a question in a finished attempt. Selects the branch of
# the single Tutor prompt.
Outcome = Literal["wrong", "skipped", "correct"]


class Choice(TypedDict):
    """One of the five options, already rendered. -> assessments.AnswerOption."""

    latex: str
    is_correct: bool
    # The blueprint's name for the mistake that produces this option
    # ("forgot_half", "confused_with_edge_count"); "" on the correct one.
    # Becomes AnswerOption.misconception and is the Tutor's ground truth.
    distractor_id: str


class SolutionRecord(TypedDict):
    """-> Question.solution (JSON).

    NOT a worked solution: no blueprint carries one. This is the reproduction
    record from the architecture doc sections 14-15 — enough to regenerate this
    exact item from its stored row — plus the answer the Tutor must land on.
    """

    topic: str
    mode: str
    difficulty: Difficulty
    seed: int
    parameters: dict[str, Any]
    derived: dict[str, Any]
    answer_expression: str  # blueprint expression, e.g. "(k+k2)*sqrt(m)"
    answer_latex: str       # its rendered value, e.g. "8\\sqrt{2}"


class EngineState(TypedDict, total=False):
    # --- inputs you provide before running the pipeline ---
    topic: str                     # blueprint filename stem, e.g. "ubt_roots_expressions"
    difficulty: Difficulty         # asked for explicitly; not inferred
    language: Language
    seed: int                      # same seed -> same question, always (doc section 14)
    student_profile: dict[str, Any]  # serialized accounts.StudentProfile; unused until
                                     # difficulty is picked adaptively

    # --- what the generator produces (pure Python + SymPy; no LLM) ---
    mode: str                      # blueprint mode = the method under test
    display_name: str              # human topic name, for prompts and admin
    parameters: dict[str, Any]     # what the RNG rolled
    derived: dict[str, Any]        # what was computed from the roll
    instruction: str               # English, from modes[mode].question.instruction
    latex: str                     # rendered statement block; models copy it verbatim
    text_context: dict[str, str]   # word-problem modes only: named values as words,
                                   # so the Contextualizer never reads them out of LaTeX
    choice_labels: dict[str, str]  # literal-answer topics only (\text{...} labels)
    answer_expression: str         # the blueprint expression that was evaluated
    answer_latex: str              # authoritative rendered answer
    answer_options: list[Choice]   # exactly 5, exactly one is_correct
    solution: SolutionRecord       # reproduction record -> Question.solution
    tag_slug: str                  # maps to a content.Tag (Question.tags M2M)
    tag_name: str                  # human label of that Tag
    content_hash: str              # sha256 of topic|mode|difficulty|params -> dedup

    # --- localizer / contextualizer produce (exactly one of them runs) ---
    text: str                      # localized statement -> Question.text
    used_contextualizer: bool      # True when the word-problem path wrote `text`

    # --- critic produces ---
    passed: bool
    rewrite_text: str              # concrete numbered fixes; input to the redraft
    tries: int                     # revision counter; breaks the redraft loop

    # --- publisher produces ---
    explanation: str               # -> Question.explanation (optional; may stay "")
    question_id: int               # PK of the assessments.Question
    was_duplicate: bool            # content_hash already in the bank; no new row
    lesson_id: int                 # content.Lesson linked (None if none teaches the tag)
    test_id: int                   # the lesson's micro Test (None if no lesson)


class TutorRequest(TypedDict, total=False):
    """Input to the Tutor. Deliberately outside EngineState: the Tutor runs on
    demand after a finished attempt, not inside the generation pipeline.

    `student_answer_latex` and `chosen_distractor_id` are present only when
    `outcome` is "wrong" — the other branches have no mistake to diagnose.
    """

    topic: str
    display_name: str
    mode: str
    difficulty: Difficulty
    language: Language
    text: str                      # the localized question the student saw
    context: dict[str, Any]        # parameters + derived, merged
    answer_latex: str
    outcome: Outcome
    student_answer_latex: str
    chosen_distractor_id: str
