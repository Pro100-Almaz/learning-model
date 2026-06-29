"""Show the Tutor working on real wrong answers (calls Claude live).

Run it from the project root with no setup:

    .venv\\Scripts\\python.exe -m agents_and_engine.tutor_demo

It builds a couple of wrong-answer scenarios (the same data the Architect
persists on a Question) and runs the real Tutor path: prompt assembly ->
chat_anthropic -> margin note. Needs ANTHROPIC_API_KEY in your .env.
"""
import os
import sys

# Settings need these two; values are throwaway (no DB is touched — we never
# save or query, just build unsaved model instances).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "conf.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "demo-only")
os.environ.setdefault("DATABASE_URL", "sqlite:///tutor_demo_unused.sqlite3")
sys.stdout.reconfigure(encoding="utf-8")  # so Kazakh prints on any console

import django

django.setup()

from apps.assessments.models import AnswerOption, Question  # noqa: E402
from apps.assessments.services import _build_tutor_prompt  # noqa: E402
from config import TUTOR_MODEL  # noqa: E402
from .llm import chat_anthropic  # noqa: E402
from .prompts import TUTOR_SYSTEM  # noqa: E402

EXAMPLES = [
    {
        "label": "Quadratic — student flipped the signs of the roots",
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
                "misconceptions": {
                    "sign_flip_both": (
                        "Reported both roots with the opposite sign — dropped the "
                        "minus sign when applying Vieta's sum x1 + x2 = -b/a."
                    ),
                },
            },
        ),
        "wrong": AnswerOption(text="-3, -4", is_correct=False, misconception="sign_flip_both"),
    },
    {
        "label": "Progression — student forgot to divide the sum by 2",
        "question": Question(
            text="Арифметикалық прогрессияда $a_1 = 10$, $d = 1$. $n = 7$ үшін $a_n$ мен $S_n$ тап.",
            solution={
                "steps": [
                    {"label": "Given", "detail": "a1 = 10, d = 1, n = 7"},
                    {"label": "nth term", "detail": "a_n = a1 + (n-1)d = 16"},
                    {"label": "Partial sum", "detail": "S_n = n(2a1+(n-1)d)/2 = 91"},
                ],
                "answer_key": {"a_n": 16, "S_n": 91},
                "misconceptions": {
                    "sum_forgot_div2": (
                        "Forgot to divide by 2 in the sum formula "
                        "S_n = n*(2*a1 + (n-1)*d)/2."
                    ),
                },
            },
        ),
        "wrong": AnswerOption(
            text="a_n = 16, S_n = 182", is_correct=False, misconception="sum_forgot_div2"
        ),
    },
]


def main() -> None:
    for ex in EXAMPLES:
        print("=" * 72)
        print(ex["label"])
        print("-" * 72)
        print("Problem:        ", ex["question"].text)
        print("Student's answer:", ex["wrong"].text)
        prompt = _build_tutor_prompt(ex["question"], ex["wrong"])
        try:
            note = chat_anthropic(TUTOR_SYSTEM, prompt, model=TUTOR_MODEL).strip()
            print("\nTutor's margin note (live from Claude):\n")
            print("  " + note)
        except Exception as exc:
            print(f"\n[API CALL FAILED] {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()
