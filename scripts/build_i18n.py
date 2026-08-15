"""
Fill the UBT engine's translation cache. Offline, occasional, never on a request.

    python -m scripts.build_i18n --check                 # no API calls, CI gate
    python -m scripts.build_i18n --language kk --dry-run # show the work, call nothing
    python -m scripts.build_i18n --language kk           # translate for real
    python -m scripts.build_i18n --all

198 strings x 3 languages, minus English which is the source, is 396 model calls
in total, ever. After that, serving a question in any language is a dict lookup
and this script never needs to run again unless a blueprint's wording changes.

Design rules, all of them about not trusting the model:

  * Symbols are checked in Python BEFORE the Critic is called. Deterministic,
    free, exact -- and a model asked to grade its own output is none of those.
  * The Critic replies through a schema, so a formatting quirk cannot masquerade
    as a quality failure.
  * Retries are capped. An unbounded loop against a paid API is a bug that bills.
  * The store is saved after EVERY entry. 396 sequential calls is long enough
    that the process will be interrupted, and losing 300 translations to a rate
    limit on the 301st is not acceptable.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, TypedDict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from config import CRITIC_MODEL, STORYTELLER_MODEL  # noqa: E402
from ubt_question_engine import i18n, prompts, symbols  # noqa: E402
from ubt_question_engine.llm import chat_openai, chat_openai_structured  # noqa: E402

# Three drafts, then give up and record the attempt unreviewed. A string that
# cannot be translated in three tries has something wrong with it that another
# ten calls will not discover.
MAX_DRAFTS = 3


class CriticVerdict(TypedDict):
    """Schema the Critic is bound to at the API layer."""

    passed: bool
    issues: list[str]


def _draft_request(source: str, kind: str, context: str) -> str:
    """The user turn for a first draft.

    KIND is stated explicitly because the Localizer's rules differ between an
    instruction and an answer label, and TOPIC is given so a bare word like
    "Simplify" is translated with the right mathematical sense.
    """
    return (
        f"KIND: {kind}\n"
        f"TOPIC: {context}\n"
        f"SOURCE: {source}"
    )


def _redraft_request(source: str, kind: str, context: str, issues: list[str]) -> str:
    numbered = "\n".join(f"{n}. {issue}" for n, issue in enumerate(issues, 1))
    return (
        f"{_draft_request(source, kind, context)}\n\n"
        f"Your previous attempt was rejected for these reasons:\n{numbered}\n"
        "Produce a corrected translation. Output only the string."
    )


def translate_one(
    source: str,
    kind: str,
    context: str,
    language: str,
    *,
    draft_model: str,
    critic_model: str,
) -> tuple[str | None, list[str]]:
    """Localizer -> symbol check -> Critic, up to MAX_DRAFTS times.

    Returns (text, issues). `text` is None when every draft was rejected; the
    issues explain why, and the caller decides whether to record the last
    attempt or skip it.
    """
    localizer = prompts.localizer_system(language)
    critic = prompts.critic_system(language)
    is_label = kind == i18n.KIND_ANSWER_LABEL

    request = _draft_request(source, kind, context)
    issues: list[str] = []
    draft = ""

    for _ in range(MAX_DRAFTS):
        draft = chat_openai(localizer, request, model=draft_model, temperature=0.2).strip()

        # Cheap deterministic gate first. No point spending a Critic call on a
        # draft that has already dropped a formula.
        broken = symbols.check(source, draft, label=is_label)
        if broken:
            issues = [symbols.describe(broken)]
            request = _redraft_request(source, kind, context, issues)
            continue

        verdict: Any = chat_openai_structured(
            critic,
            f"KIND: {kind}\nSOURCE: {source}\nTRANSLATION: {draft}",
            model=critic_model,
            schema=CriticVerdict,
        )
        if verdict.get("passed"):
            return draft, []

        issues = list(verdict.get("issues") or ["rejected without a stated reason"])
        request = _redraft_request(source, kind, context, issues)

    return None, issues


def build_language(
    language: str,
    *,
    force: bool,
    dry_run: bool,
    limit: int | None,
    draft_model: str,
    critic_model: str,
) -> tuple[int, int]:
    """Translate everything missing for one language. Returns (done, failed)."""
    if language == i18n.SOURCE_LANGUAGE:
        print(f"[{language}] source language, nothing to translate")
        return 0, 0

    todo = i18n.translatable() if force else i18n.missing(language)
    if limit:
        todo = dict(list(todo.items())[:limit])

    print(f"[{language}] {len(todo)} string(s) to translate")
    if dry_run:
        for source, meta in list(todo.items())[:15]:
            print(f"   would translate [{meta['kind']}] {source!r}")
        if len(todo) > 15:
            print(f"   ... and {len(todo) - 15} more")
        return 0, 0

    store = i18n.load(refresh=True)
    done = failed = 0

    for index, (source, meta) in enumerate(todo.items(), 1):
        text, issues = translate_one(
            source,
            meta["kind"],
            meta["context"],
            language,
            draft_model=draft_model,
            critic_model=critic_model,
        )
        if text is None:
            failed += 1
            print(f"  [{index}/{len(todo)}] FAILED {source!r}")
            for issue in issues:
                print(f"      - {issue}")
            continue

        i18n.put(store, source, language, text, kind=meta["kind"], reviewed=False)
        # Saved every time, not at the end: this loop will be interrupted.
        i18n.save(store)
        done += 1
        print(f"  [{index}/{len(todo)}] {source!r} -> {text!r}")

    return done, failed


def report_coverage() -> int:
    """Print coverage. Non-zero exit when a language cannot be fully served."""
    incomplete = 0
    for language, (done, total) in i18n.coverage().items():
        flag = "" if done == total else "   <-- INCOMPLETE"
        if done != total:
            incomplete += 1
        print(f"  {language}: {done}/{total}{flag}")
    return 1 if incomplete else 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        prog="python -m scripts.build_i18n",
        description="Build the UBT engine's translation cache.",
    )
    parser.add_argument("--language", action="append", choices=("kk", "ru", "en"))
    parser.add_argument("--all", action="store_true", help="every supported language")
    parser.add_argument("--force", action="store_true", help="retranslate existing entries")
    parser.add_argument("--dry-run", action="store_true", help="list the work, call nothing")
    parser.add_argument("--limit", type=int, help="translate at most N strings (a smoke test)")
    parser.add_argument("--check", action="store_true", help="report coverage and exit")
    parser.add_argument("--draft-model", default=STORYTELLER_MODEL)
    parser.add_argument("--critic-model", default=CRITIC_MODEL)
    args = parser.parse_args(argv)

    if args.check:
        print("coverage:")
        return report_coverage()

    languages = ("kk", "ru") if args.all else tuple(args.language or ())
    if not languages:
        parser.error("give --language, --all, or --check")

    total_done = total_failed = 0
    for language in languages:
        done, failed = build_language(
            language,
            force=args.force,
            dry_run=args.dry_run,
            limit=args.limit,
            draft_model=args.draft_model,
            critic_model=args.critic_model,
        )
        total_done += done
        total_failed += failed

    print(f"\ntranslated {total_done}, failed {total_failed}")
    print("coverage:")
    report_coverage()
    print(
        "\nEvery entry is stored with reviewed=false. A native speaker should read "
        f"{i18n.STORE_PATH} and flip the flag; nothing in the engine enforces it yet."
    )
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
