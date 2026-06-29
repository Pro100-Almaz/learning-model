"""Publisher dedup: a batch must never insert the same problem twice.

Two questions are "the same problem" when they share a topic and the same rolled
numbers — even if the Storyteller wrapped them in different narratives. These
tests drive publisher_node directly with hand-built states so they don't need
the LLM nodes.
"""

import pytest

from apps.assessments.models import AnswerOption, Question
from maiqe.math.engine import compute_content_hash
from maiqe.nodes import publisher_node

pytestmark = pytest.mark.django_db


def _state(content_hash, *, draft="Решите уравнение $x^2 = 4$."):
    """A minimal, valid publishable state with a fixed dedup hash."""
    return {
        "topic": "quadratic_equations",
        "draft_text": draft,
        "difficulty": 1,
        "tag_slug": "quad",
        "tag_name": "Quadratics",
        "answer_key": [-2, 2],
        "content_hash": content_hash,
        "answer_options": [
            {"text": "-2, 2", "is_correct": True, "misconception": ""},
            {"text": "-2, 3", "is_correct": False, "misconception": "sign"},
            {"text": "1, 4", "is_correct": False, "misconception": "factor"},
            {"text": "0, 2", "is_correct": False, "misconception": ""},
        ],
        "solution": {"answer_key": [-2, 2]},
    }


def test_same_problem_published_twice_creates_no_duplicate():
    h = "a" * 64
    first = publisher_node(_state(h))
    # Same math, deliberately different story — must still be deduped.
    second = publisher_node(_state(h, draft="Совсем другая история, та же математика."))

    assert first["was_duplicate"] is False
    assert second["was_duplicate"] is True
    assert second["question_id"] == first["question_id"]  # reused the existing row
    assert Question.objects.filter(content_hash=h).count() == 1
    assert Question.objects.count() == 1
    # The re-draft did not duplicate the first run's options.
    assert AnswerOption.objects.filter(question_id=first["question_id"]).count() == 4


def test_different_problems_both_persist():
    a = publisher_node(_state("a" * 64))
    b = publisher_node(_state("b" * 64))

    assert a["was_duplicate"] is False
    assert b["was_duplicate"] is False
    assert a["question_id"] != b["question_id"]
    assert Question.objects.count() == 2


def test_compute_content_hash_identity():
    # Same topic + same numbers (any key order) -> same hash.
    h1 = compute_content_hash("quad", {"a": 1, "b": 2, "c": 3})
    h2 = compute_content_hash("quad", {"c": 3, "b": 2, "a": 1})
    # A different roll, or a different topic, -> a different hash.
    h3 = compute_content_hash("quad", {"a": 1, "b": 2, "c": 9})
    h4 = compute_content_hash("progression", {"a": 1, "b": 2, "c": 3})

    assert h1 == h2
    assert h1 != h3
    assert h1 != h4
    assert len(h1) == 64
