"""
Quick preview of the text the system generates — no database needed.

Runs Architect -> Storyteller -> Critic (the same reflection loop as graph.py,
minus the Publisher) and prints each draft plus the Critic's verdict.

Usage:
    python preview.py                          # one of each blueprint topic
    python preview.py quadratic_equations      # just this topic
    python preview.py calculus_integrals 130   # topic + ENT target score
"""
from __future__ import annotations

import sys

import maiqe.config as config
from maiqe.nodes import architect_node, critic_node, critic_router, storyteller_node
from maiqe.state import GraphState

ALL_TOPICS = ["quadratic_equations", "arithmetic_progression", "calculus_integrals"]


def preview(topic: str, target_score: int | None = None) -> None:
    profile = {"target_score": target_score} if target_score is not None else {}
    state: GraphState = {"topic": topic, "student_profile": profile}

    state.update(architect_node(state))
    print(f"\n{'=' * 70}")
    print(f"{topic}  (difficulty {state['difficulty']}, answer_key={state['answer_key']})")
    print("=" * 70)
    print("options:")
    for opt in state["answer_options"]:
        mark = "*" if opt["is_correct"] else " "
        tag = f"  <- {opt['misconception']}" if opt["misconception"] else ""
        print(f"  [{mark}] {opt['text']}{tag}")

    while True:
        state.update(storyteller_node(state))
        state.update(critic_node(state))
        route = critic_router(state)

        verdict = "PASS" if state["critic_passed"] else "FAIL"
        print(f"\n[draft #{state['revision_count']} — critic: {verdict}]")
        print(state["draft_text"])
        if not state["critic_passed"]:
            print(f"\n  rewrite notes: {state.get('rewrite_notes', '')}")

        if route == "publisher":
            break
        if route == "fallback":
            print(f"\n(gave up after {state['revision_count']} tries — would use a cached question)")
            break


def main() -> None:
    args = sys.argv[1:]
    if args:
        topic = args[0]
        target = int(args[1]) if len(args) > 1 else None
        preview(topic, target)
    else:
        for topic in ALL_TOPICS:
            preview(topic)


if __name__ == "__main__":
    main()

'''
type: $env:PYTHONIOENCODING="utf-8"; python preview.py
'''