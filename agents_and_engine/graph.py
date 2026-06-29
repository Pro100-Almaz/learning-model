"""
MAIQE — LangGraph assembly.

Wires the nodes from nodes_self.py into the stateful, cyclic graph described in
arch.md §2. This module only *builds* the graph; it does not run Django. The
Publisher node imports the ORM lazily, so `build_graph()` stays import-safe in
any context — but actually *invoking* the graph reaches the Publisher and so
must happen inside a Django context (see `generate_question` below).

Flow (arch.md):

    START -> architect -> storyteller -> critic -> ┬─ pass        -> publisher -> END
                              ^                     ├─ fail (<max) -> storyteller (loop)
                              └─────────────────────┘
                                                    └─ fail (>=max)-> END   (TODO: fallback_node)

The critic -> storyteller edge is the Reflection loop; `critic_router` breaks it
after config.MAX_REVISIONS rounds (arch.md §5.4).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .nodes_self import (
    architect_node,
    critic_node,
    critic_router,
    publisher_node,
    storyteller_node,
)
from .state import GraphState


def build_graph():
    """Construct and compile the question-generation graph.

    Pure and Django-free: building never imports the ORM. Returns a compiled
    LangGraph you invoke with `.invoke(initial_state)`.
    """
    builder = StateGraph(GraphState)

    builder.add_node("architect", architect_node)
    builder.add_node("storyteller", storyteller_node)
    builder.add_node("critic", critic_node)
    builder.add_node("publisher", publisher_node)

    # Linear spine: math -> draft -> review.
    builder.add_edge(START, "architect")
    builder.add_edge("architect", "storyteller")
    builder.add_edge("storyteller", "critic")

    # The Critic decides where the token goes next (Reflection loop + breakout).
    builder.add_conditional_edges(
        "critic",
        critic_router,
        {
            "publisher": "publisher",   # approved
            "storyteller": "storyteller",  # rejected, redraft
            "fallback": END,            # gave up; TODO: swap for a fallback_node
        },
    )

    builder.add_edge("publisher", END)

    return builder.compile()


# Built once at import time; the compiled graph is reusable across invocations.
graph = build_graph()


def generate_question(topic: str, student_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the full pipeline for one topic and return the final GraphState.

    Convenience entrypoint for a Celery worker / management command. The caller
    MUST have Django configured (DJANGO_SETTINGS_MODULE=conf.settings) before
    calling, because the Publisher writes to the ORM.

    On success the result holds `question_id` (the persisted Question) and
    `was_duplicate`: False if this run wrote a new row, True if the same problem
    was already in the bank (dedup hit) — `question_id` then points at the
    existing row and nothing new was written. If the draft hit the fallback
    breakout instead, `critic_passed` is False and no Question was created —
    check that before treating the run as published.
    """
    initial: GraphState = {"topic": topic, "student_profile": student_profile or {}}
    return graph.invoke(initial)
