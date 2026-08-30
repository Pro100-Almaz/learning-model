"""
Look at generated questions with human eyes.

    python -m ubt_question_engine.preview ubt_roots_expressions --answers
    python -m ubt_question_engine.preview --all-difficulties --count 2 > review.txt

Every automated check so far proves *shape*: five options, exactly one correct,
nothing undefined, the same seed reproducing the same item. Not one of them can
say whether the mathematics is right, whether the instruction matches what the
formula actually asks, or whether a distractor encodes a mistake a real student
would make. Only a person reading the output can say that, and this is the tool
that puts the output in front of them.

The seed is printed on every question on purpose. A reviewer who finds a broken
item hands back `--seed 41`, and it reproduces exactly.
"""

from __future__ import annotations

import argparse
import json
import random
import sys

from .generator import generate_question
from .i18n import MissingTranslation
from .loader import list_topics, topic_meta
from .localize import ENGINE_LANGUAGES, localize
from .params import GenerationError
from .present import render_question
from .state import EngineState

SEPARATOR = "=" * 78


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ubt_question_engine.preview",
        description="Render generated UBT questions for human review.",
    )
    parser.add_argument(
        "topic",
        nargs="?",
        help="blueprint stem, e.g. ubt_roots_expressions. Omit for every topic.",
    )
    parser.add_argument(
        "--difficulty", type=int, choices=(1, 2, 3),
        help="default: the topic's own default_difficulty",
    )
    parser.add_argument(
        "--all-difficulties", action="store_true",
        help="one sample per supported difficulty instead of just the default",
    )
    parser.add_argument("--count", type=int, default=1, help="questions per topic")
    parser.add_argument(
        "--seed", type=int,
        help="base seed; omitted means a fresh one, which is always printed",
    )
    parser.add_argument(
        "--language", choices=ENGINE_LANGUAGES, default="en",
        help="default: en, the language blueprints are authored in and the only "
             "one that needs no translations",
    )
    parser.add_argument(
        "--answers", action="store_true",
        help="mark the correct option and show each distractor's misconception id",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="dump raw EngineState instead of prose, for piping",
    )
    parser.add_argument("--list", action="store_true", help="list topics and exit")
    return parser


def _difficulties(topic: str, args: argparse.Namespace) -> list[int]:
    meta = topic_meta(topic)
    if args.all_difficulties:
        return meta["supported_difficulties"]
    if args.difficulty is not None:
        return [args.difficulty]
    return [meta["default_difficulty"]]


def _print_question(state: EngineState, show_answers: bool) -> None:
    print(SEPARATOR)
    print(
        f"{state['topic']} / {state['mode']} / d{state['difficulty']} "
        f"/ seed {state['seed']} / {state.get('language', 'en')}"
    )
    print(f"{state['display_name']}   [tag: {state['tag_slug']}]")
    print("-" * 78)
    print(render_question(state, show_answers=show_answers))
    if not show_answers:
        print()
        print(f"answer: {state['answer_latex']}")
    print()


def main(argv: list[str] | None = None) -> int:
    # In main, never at import: a module that mutates global stdout the moment it
    # is imported is a module no test can safely import. Windows consoles default
    # to cp1251 here and the tag names are Kazakh Cyrillic, so without this the
    # run dies with UnicodeEncodeError partway through topic three.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)

    if args.list:
        for name in list_topics():
            meta = topic_meta(name)
            print(f"{name:52} d{meta['supported_difficulties']} {meta['display_name']}")
        return 0

    topics = [args.topic] if args.topic else list_topics()
    base_seed = args.seed if args.seed is not None else random.randrange(2**31)

    built = 0
    failures: list[str] = []

    for topic in topics:
        try:
            wanted = _difficulties(topic, args)
        except Exception as error:  # unknown topic, malformed blueprint
            failures.append(f"{topic}: {type(error).__name__}: {error}")
            continue

        for difficulty in wanted:
            for index in range(args.count):
                # Offset per question so one --seed gives a varied but fully
                # reproducible sheet rather than the same item repeated.
                seed = base_seed + index * 1_000 + difficulty
                try:
                    state = generate_question(topic, difficulty=difficulty, seed=seed)
                    if args.language != "en":
                        # A partially built cache is the normal state while
                        # build_i18n is still running, so an untranslated mode is
                        # a reportable gap here rather than a crash.
                        state = localize(state, args.language)
                except (GenerationError, MissingTranslation) as error:
                    # Keep going. A survey of 58 topics that reports three
                    # failures is worth far more than one that dies on topic 12.
                    failures.append(f"{topic} d{difficulty}: {str(error).splitlines()[0]}")
                    continue

                built += 1
                if args.json:
                    print(json.dumps(state, ensure_ascii=False, default=str))
                else:
                    _print_question(state, show_answers=args.answers)

    print(SEPARATOR)
    print(f"{built} question(s) from {len(topics)} topic(s); {len(failures)} failure(s)")
    for failure in failures:
        print(f"  FAIL {failure}")

    # Non-zero on any failure, so this can sit in CI unchanged.
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
