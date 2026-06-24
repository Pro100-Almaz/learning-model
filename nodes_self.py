"""
MAIQE — graph nodes, built one agent at a time.

A "node" is a function that takes the shared GraphState and returns a partial
update dict (LangGraph merges it back into the state). Nodes stay thin: the
real work lives in the modules they call.

    state.py        -> GraphState (the shared base)
    math_engine.py  -> the deterministic math + verdict parsing (pure, testable)
    prompts.py      -> the LLM system prompts (Storyteller, Critic)

This file contains Agents 1-4 (Architect, Storyteller, Critic, Publisher) plus
the Critic's routing edge. The Tutor (Agent 5) is NOT here: it runs live and
on-demand from the Django request path (apps.assessments.services), not as a
node in this offline generation graph.

HOW THIS CONNECTS TO THE DJANGO BACKEND
---------------------------------------
The graph's job is to manufacture one `assessments.Question` (plus its
`AnswerOption`s) tagged with one `content.Tag`. The Architect does NOT touch
the database — it only computes the payload the later Publisher node persists.
See GraphState in state.py for the field-by-field mapping.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

import config
from llm import chat_openai_structured
from math_ques_types import compute_answer_key
from math_engine import (
    build_answer_options,
    build_solution,
    compute_content_hash,
    deterministic_review,
    generate_math_spec,
    load_blueprint,
    render_constraints,
    resolve_difficulty,
)
from prompts import CRITIC_SYSTEM, STORYTELLER_SYSTEM
from state import GraphState

# Every published Question carries exactly this many answer options (one correct
# + distractors). The Architect builds them; the Publisher enforces the count.
N_ANSWER_OPTIONS = 4


class StoryDraft(TypedDict):
    """Schema the Storyteller is forced to return — just the finished problem.

    Constraining the output to one field is what guarantees clean Question.text:
    no "Here is your problem:" preamble, no markdown code fences, no commentary.
    """

    problem_statement: Annotated[
        str,
        ...,
        "The complete word problem in Kazakh, formulas as LaTeX in $...$. Only "
        "the statement a student reads — no preamble, no answer, no commentary.",
    ]


class CriticVerdict(TypedDict):
    """Schema the Critic model is forced to return (see chat_openai_structured)."""

    passed: Annotated[bool, ..., "True only if the draft passes every semantic check."]
    notes: Annotated[
        str,
        ...,
        "If failed: concrete, numbered rewrite instructions. If passed: empty string.",
    ]


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
      6. Build the deterministic worked solution (the Tutor's ground truth).
      7. Build the answer options, each wrong one tagged with the misconception
         that produces it (so a student's pick names their error).
    """

    blueprint = load_blueprint(state["topic"])
    difficulty = resolve_difficulty(state.get("student_profile", {}), blueprint)
    math_spec = generate_math_spec(blueprint, difficulty)
    answer_key = compute_answer_key(blueprint, math_spec)
    constraints_payload = render_constraints(blueprint, math_spec)
    solution = build_solution(blueprint, math_spec, answer_key)

    blueprint_distractors = blueprint.get("distractors", [])
    answer_options = build_answer_options(
        answer_key, blueprint_distractors, math_spec, n_options=N_ANSWER_OPTIONS
    )
    # Carry the human description for only the misconceptions that actually
    # became options (some collapse to duplicates on a given roll). The Tutor
    # maps a wrong option's tag -> this text without reloading the blueprint.
    used = {o["misconception"] for o in answer_options if o["misconception"]}
    solution["misconceptions"] = {
        d["id"]: d["desc"] for d in blueprint_distractors if d["id"] in used
    }

    tag = blueprint["tag"]
    # Return only the keys this agent owns; LangGraph merges them into State.
    return {
        "math_spec": math_spec,
        "answer_key": answer_key,
        "constraints_payload": constraints_payload,
        "solution": solution,
        "answer_options": answer_options,
        "difficulty": difficulty,
        "tag_slug": tag["slug"],
        "tag_name": tag["name"],
        # Dedup key for the bank, computed from the rolled numbers (the problem's
        # math identity, before any storytelling). The Publisher enforces it.
        "content_hash": compute_content_hash(state["topic"], math_spec),
    }


# ---------------------------------------------------------------------------
# Agent 2 — The Storyteller (fast, cheap LLM)
# ---------------------------------------------------------------------------
def storyteller_node(state: GraphState) -> dict[str, Any]:
    """Draft (or redraft) the problem text from the Architect's constraints.

    Reads `constraints_payload` (the rigid math spec). If the Critic looped back
    with `rewrite_notes`, this is a REVISION: the Storyteller is shown its own
    previous draft and told to fix only the flagged issues, so it edits rather
    than regenerating from scratch — that's what stops a redraft from silently
    reintroducing problems an earlier round had already fixed. The reply is
    constrained to the StoryDraft schema, so `draft_text` is always clean text.
    """
    user_prompt = state["constraints_payload"]
    if state.get("rewrite_notes"):
        previous = state.get("draft_text", "")
        user_prompt += (
            f"\n\n--- REVISION REQUESTED ---\n"
            f"The editor REJECTED your previous draft:\n{previous}\n\n"
            f"Revise THAT draft to fix only the issues below. Keep everything "
            f"else — numbers, setting, phrasing — exactly as it was, so you do "
            f"not undo anything that was already correct:\n{state['rewrite_notes']}"
        )

    draft = chat_openai_structured(
        STORYTELLER_SYSTEM,
        user_prompt,
        model=config.STORYTELLER_MODEL,
        schema=StoryDraft,
        temperature=0.7,  # creative writing, unlike the Critic's verification
    )
    return {"draft_text": draft["problem_statement"].strip()}

# ---------------------------------------------------------------------------
# Agent 3 — The Critic (reasoning LLM, Reflection Pattern)
# ---------------------------------------------------------------------------
def critic_node(state: GraphState) -> dict[str, Any]:
    """Cross-check the Storyteller's draft against the Architect's math (arch.md §3).

    Two passes, cheap-to-expensive:
      1. `deterministic_review` decides number fidelity and answer-leak in pure
         Python — the integrity checks an LLM must not be trusted with. If it
         fails, we short-circuit (no model spend) and return its notes.
      2. Only if that passes do we call the reasoning model for the judgement
         calls it's actually good at (logic, reading level, language, semantic
         leaks), with the reply constrained to the CriticVerdict schema.

    On a fail it writes `rewrite_notes` for the Storyteller's next pass; either
    way it increments `revision_count` (the count of reviews done) so the router
    can break the loop once redrafts are exhausted (see `critic_router`).
    """
    notes: list[str] = []

    gate = deterministic_review(
        state["constraints_payload"], state["answer_key"], state["draft_text"]
    )
    passed = gate["passed"]
    if gate["notes"]:
        notes.append(gate["notes"])

    if passed:
        user_prompt = (
            f"CONSTRAINTS:\n{state['constraints_payload']}\n\n"
            f"CORRECT ANSWER (answer_key, for leak-checking only): {state['answer_key']}\n\n"
            f"STORYTELLER DRAFT TO REVIEW:\n{state['draft_text']}"
        )
        verdict: CriticVerdict = chat_openai_structured(
            CRITIC_SYSTEM,
            user_prompt,
            model=config.CRITIC_MODEL,
            schema=CriticVerdict,
        )
        passed = bool(verdict["passed"])
        if not passed and verdict["notes"].strip():
            notes.append(verdict["notes"].strip())

    update: dict[str, Any] = {
        "critic_passed": passed,
        "revision_count": state.get("revision_count", 0) + 1,
    }
    if not passed:
        update["rewrite_notes"] = "\n".join(notes)
    return update


def critic_router(state: GraphState) -> str:
    """Conditional edge after the Critic (arch.md flow + §5.4 breakout).

    Returns a routing label (mapped to a destination at graph-build time):
      - "publisher"   : draft approved.
      - "fallback"    : failed too many times. Per arch.md §5.4 this should
                        pull a pre-approved question, but that node isn't built
                        yet -> for now wire it straight to END.
      - "storyteller" : failed but under the limit; loop back for a redraft.

    `revision_count` is the number of Critic reviews so far (= drafts produced).
    The first draft is not a revision, so the Storyteller is allowed
    config.MAX_REVISIONS *redrafts* on top of it: we break to the fallback only
    once a draft has failed MORE than MAX_REVISIONS times (revision_count >
    MAX_REVISIONS), i.e. after 1 + MAX_REVISIONS total attempts. At the default
    of 2 that's "fails more than twice", exactly as arch.md §5.4 specifies.

    Wire with (END from `from langgraph.graph import END`):
        graph.add_conditional_edges("critic", critic_router, {
            "publisher":   "publisher",
            "storyteller": "storyteller",
            "fallback":    END,           # TODO: replace with fallback_node
        })
    """
    if state.get("critic_passed"):
        return "publisher"
    if state.get("revision_count", 0) > config.MAX_REVISIONS:
        return "fallback"
    return "storyteller"


# ---------------------------------------------------------------------------
# Agent 4 — The Publisher (deterministic Python + DB write, no LLM)
# ---------------------------------------------------------------------------
def _assert_publishable(
    draft_text: str, options: list[dict[str, Any]], *, expected_options: int
) -> None:
    """Fail loudly if a question would violate the bank's core invariants.

    The Publisher is the last gate before the database, and downstream automated
    grading assumes EXACTLY ONE correct option. None of these conditions should
    happen on the Critic's "pass" branch, so a violation is a data/logic bug — we
    raise (the batch worker logs and skips) instead of persisting a broken item.
    """
    if not draft_text:
        raise ValueError("Publisher: draft_text is empty; refusing to persist a blank question.")

    if len(options) != expected_options:
        raise ValueError(
            f"Publisher: expected {expected_options} answer options, got {len(options)}."
        )

    n_correct = sum(1 for o in options if o["is_correct"])
    if n_correct != 1:
        raise ValueError(f"Publisher: expected exactly 1 correct option, got {n_correct}.")

    texts = [o["text"] for o in options]
    if len(set(texts)) != len(texts):
        raise ValueError(f"Publisher: answer options are not distinct: {texts}.")


def _resolve_content_hash(state: GraphState) -> str | None:
    """The dedup key for this run, or None if there's nothing to hash.

    Normally the Architect already put it on the state; recompute it from the
    spec if a caller invoked the Publisher directly. None only when there's no
    spec at all (e.g. a hand-built test state) — dedup is then simply skipped.
    """
    content_hash = state.get("content_hash")
    if content_hash:
        return content_hash
    if state.get("topic") and state.get("math_spec") is not None:
        return compute_content_hash(state["topic"], state["math_spec"])
    return None


def publisher_node(state: GraphState) -> dict[str, Any]:
    """Persist the Critic-approved draft as an assessments.Question.

    The ONLY node that touches the database. Reached on the Critic's "pass"
    branch, so the state is complete and verified. It:
      0. DEDUP: if a Question with this run's content_hash already exists, reuse
         it and write nothing (returns was_duplicate=True). This is what stops a
         batch that re-rolls the same numbers from filling the bank with the same
         problem — possibly wrapped in a different story. The unique constraint on
         Question.content_hash is the real guarantee: the pre-check is the fast,
         common path; the IntegrityError handler covers the race where a
         concurrent worker inserts the same hash between our check and our write.
      1. get_or_creates the content.Tag (by slug).
      2. creates the Question (text + explanation + difficulty + solution + hash).
      3. attaches the tag (Question.tags M2M).
      4. bulk-creates the AnswerOptions (one correct + distractors).
    Steps 1-4 run inside one transaction so a half-written question can't survive
    a crash.

    Returns {"question_id", "was_duplicate"}: on a fresh write question_id is the
    new row and was_duplicate is False; on a dedup hit question_id points at the
    EXISTING row and was_duplicate is True (no new row, no new options).

    Django models are imported lazily so the pure nodes above stay importable
    (and unit-testable) without Django configured. This node, by contrast, must
    run inside a Django context (settings loaded / `django.setup()`).
    """
    from django.db import IntegrityError, transaction

    from apps.assessments.models import AnswerOption, Question
    from apps.content.models import Tag

    content_hash = _resolve_content_hash(state)

    # Fast path: this exact problem is already in the bank -> reuse it, write
    # nothing. (Skipped when there's no hash to dedup on.)
    if content_hash:
        existing = (
            Question.objects.filter(content_hash=content_hash)
            .values_list("pk", flat=True)
            .first()
        )
        if existing is not None:
            return {"question_id": existing, "was_duplicate": True}

    draft_text = (state.get("draft_text") or "").strip()
    # The Architect builds the misconception-tagged options; fall back to plain
    # numeric distractors only if a caller invoked the Publisher without them.
    options = state.get("answer_options") or build_answer_options(state["answer_key"])

    # Last gate before the DB — refuse to persist anything that would break the
    # bank's invariants (see _assert_publishable).
    _assert_publishable(draft_text, options, expected_options=N_ANSWER_OPTIONS)

    correct_text = next(o["text"] for o in options if o["is_correct"])
    explanation = state.get("explanation") or f"Правильный ответ: {correct_text}."
    # The structured worked solution the Architect built — the Tutor's ground
    # truth for diagnosing a wrong answer (arch.md §5). Default to {} so a
    # question is still publishable if it somehow arrived without one.
    solution = state.get("solution") or {}

    try:
        with transaction.atomic():
            tag, _ = Tag.objects.get_or_create(
                slug=state["tag_slug"],
                defaults={"name": state["tag_name"]},
            )
            question = Question.objects.create(
                text=draft_text,
                explanation=explanation,
                difficulty=state.get("difficulty", 1),
                solution=solution,
                content_hash=content_hash,
            )
            question.tags.add(tag)
            AnswerOption.objects.bulk_create(
                [
                    AnswerOption(
                        question=question,
                        text=opt["text"],
                        is_correct=opt["is_correct"],
                        misconception=opt.get("misconception", ""),
                    )
                    for opt in options
                ]
            )
    except IntegrityError:
        # We lost a race: a concurrent batch worker inserted the same hash
        # between our pre-check and our write. The unique constraint did its job
        # — adopt the winner's row instead of failing the run.
        if content_hash:
            existing = (
                Question.objects.filter(content_hash=content_hash)
                .values_list("pk", flat=True)
                .first()
            )
            if existing is not None:
                return {"question_id": existing, "was_duplicate": True}
        raise  # not our dedup constraint -> a genuine error, don't swallow it

    return {"question_id": question.pk, "was_duplicate": False}
