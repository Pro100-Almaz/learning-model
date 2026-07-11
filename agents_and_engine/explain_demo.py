"""Show the Tutor's EXPLANATION mode on real skipped questions (calls Claude live).

Run it from the project root with no setup:

    .venv\\Scripts\\python.exe -m agents_and_engine.explain_demo

It builds a few skipped-question scenarios (the same data the Architect persists
on a Question) and runs the real explanation path: prompt assembly ->
chat_anthropic (max_tokens=600) -> worked explanation that reveals the answer.
Needs ANTHROPIC_API_KEY in your .env. Sibling of tutor_demo.py, which shows the
diagnosis (wrong-answer) mode.
"""
import os
import sys

# Settings need these two; values are throwaway (no DB is touched — we never
# save or query, just build unsaved model instances).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "conf.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "demo-only")
os.environ.setdefault("DATABASE_URL", "sqlite:///explain_demo_unused.sqlite3")
sys.stdout.reconfigure(encoding="utf-8")  # so Kazakh prints on any console

import django

django.setup()

from apps.assessments.models import Question  # noqa: E402
from apps.assessments.services import _build_explanation_prompt  # noqa: E402
from config import TUTOR_MODEL  # noqa: E402
from .llm import chat_anthropic  # noqa: E402
from .prompts import TUTOR_EXPLANATION_SYSTEM  # noqa: E402

EXAMPLES = [
    {
        "label": "Quadratic — student skipped it entirely",
        "question": Question(
            text="$x^2 - 7x + 12 = 0$ теңдеуінің түбірлерін тап.",
            solution={
                "steps": [
                    {"label": "Equation", "detail": "x^2 - 7x + 12 = 0"},
                    {"label": "Vieta: sum", "detail": "x1 + x2 = -b/a = 7"},
                    {"label": "Vieta: product", "detail": "x1 * x2 = c/a = 12"},
                    {"label": "Roots", "detail": "x1 = 3, x2 = 4"},
                ],
                "answer_key": [3, 4],
            },
        ),
    },
    {
        "label": "Progression — skipped, has a full worked solution",
        "question": Question(
            text="Арифметикалық прогрессияда $a_1 = 10$, $d = 1$. $n = 7$ үшін $a_n$ мен $S_n$ тап.",
            solution={
                "steps": [
                    {"label": "Given", "detail": "a1 = 10, d = 1, n = 7"},
                    {"label": "nth term", "detail": "a_n = a1 + (n-1)d = 16"},
                    {"label": "Partial sum", "detail": "S_n = n(2a1+(n-1)d)/2 = 91"},
                ],
                "answer_key": {"a_n": 16, "S_n": 91},
            },
        ),
    },
    {
        "label": "No worked solution on file — model must solve from scratch",
        "question": Question(
            text="$x^2 - 5x + 6 = 0$ теңдеуінің түбірлерін тап.",
            solution={},
        ),
    },
]


def main() -> None:
    for ex in EXAMPLES:
        print("=" * 72)
        print(ex["label"])
        print("-" * 72)
        print("Problem:", ex["question"].text)
        prompt = _build_explanation_prompt(ex["question"])
        try:
            note = chat_anthropic(
                TUTOR_EXPLANATION_SYSTEM, prompt, model=TUTOR_MODEL, max_tokens=600
            ).strip()
            print("\nTutor's explanation (live from Claude):\n")
            print(note)
        except Exception as exc:
            print(f"\n[API CALL FAILED] {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()
