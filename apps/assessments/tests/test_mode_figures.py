"""Diagrams attached to a blueprint MODE rather than to a question.

Three properties matter here, and each has a way of failing quietly:

    * one figure row serves all three language versions of an item, which is
      what keeps "one mathematics, three rows" true once pictures exist;
    * serializing a whole test costs one figure query, not one per question;
    * markup that could execute never reaches the database, because the
      frontend inlines it.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.db import IntegrityError

from apps.assessments import figures
from apps.assessments.models import ModeFigure, Question
from apps.assessments.serializers import QuestionPublicSerializer
from ubt_question_engine.localize import build_all_languages
from ubt_question_engine.publisher import publish, publish_item
from ubt_question_engine.testing import use_fake_translations, use_no_translations

pytestmark = pytest.mark.django_db

TOPIC = "ubt_triangle_properties"
MODE = "triangle_angle_sum"
SVG = ('<svg viewBox="0 0 100 60" xmlns="http://www.w3.org/2000/svg">'
       '<polygon points="5,55 95,55 40,5" fill="none" stroke="currentColor"/>'
       '<text x="2" y="59">A</text></svg>')


@pytest.fixture(autouse=True)
def clean_cache():
    """The figure table is cached process-wide; tests must not inherit it."""
    cache.delete(figures.CACHE_KEY)
    yield
    cache.delete(figures.CACHE_KEY)


@pytest.fixture
def figure():
    return ModeFigure.objects.create(
        topic=TOPIC, mode=MODE, svg=SVG,
        alt={"en": "A triangle with two angles marked",
             "kk": "Екі бұрышы белгіленген үшбұрыш"},
    )


def _publish_one(topic=TOPIC, seed=100, languages=("en",)):
    with use_no_translations():
        result = publish_item(topic, difficulty=1, seed=seed, languages=languages)
    return Question.objects.get(pk=result["en"]["question_id"])


# --- the key is (topic, mode) -----------------------------------------------


def test_a_figure_is_found_by_the_mode_recorded_in_the_solution(figure):
    question = _publish_one()
    assert question.solution["mode"] == MODE, "fixture seed no longer rolls this mode"

    payload = QuestionPublicSerializer(question).data
    assert payload["figure"]["svg"] == SVG
    assert payload["figure"]["alt"] == "A triangle with two angles marked"


def test_questions_of_another_mode_get_nothing(figure):
    other = _publish_one(topic="ubt_roots_expressions")
    assert QuestionPublicSerializer(other).data["figure"] is None


def test_a_hand_authored_question_can_never_match(figure):
    """No topic/mode in `solution`, so the lookup short-circuits."""
    question = Question.objects.create(text="2+2?", explanation="", solution={})
    assert QuestionPublicSerializer(question).data["figure"] is None
    assert figures.figure_for(None) is None
    assert figures.figure_for("not a dict") is None


def test_one_row_serves_every_language_of_an_item(figure):
    """The invariant a per-item figure would have put at risk."""
    with use_fake_translations():
        publish(build_all_languages(TOPIC, difficulty=1, seed=100))

    rows = list(Question.objects.filter(solution__mode=MODE))
    assert len(rows) == 3
    svgs = {QuestionPublicSerializer(q).data["figure"]["svg"] for q in rows}
    assert len(svgs) == 1, "the three languages must share one diagram"
    assert ModeFigure.objects.count() == 1


def test_alt_text_follows_the_row_language(figure):
    with use_fake_translations():
        publish(build_all_languages(TOPIC, difficulty=1, seed=100))

    by_language = {q.language: QuestionPublicSerializer(q).data["figure"]["alt"]
                   for q in Question.objects.filter(solution__mode=MODE)}
    assert by_language["kk"] == "Екі бұрышы белгіленген үшбұрыш"
    assert by_language["en"] == "A triangle with two angles marked"
    # No Russian alt was written; falling back to Kazakh would be worse than none.
    assert by_language["ru"] is None


def test_one_mode_can_hold_only_one_figure(figure):
    with pytest.raises(IntegrityError):
        ModeFigure.objects.create(topic=TOPIC, mode=MODE, svg=SVG)


# --- cost -------------------------------------------------------------------


def test_serializing_a_whole_test_costs_one_figure_query(figure, django_assert_num_queries):
    questions = [_publish_one(seed=seed) for seed in range(100, 106)]
    cache.delete(figures.CACHE_KEY)

    # One query for the figure table, plus one per question for its options.
    with django_assert_num_queries(1 + len(questions)):
        QuestionPublicSerializer(questions, many=True).data


def test_writing_a_figure_drops_the_cache(figure):
    question = _publish_one()
    assert QuestionPublicSerializer(question).data["figure"]["svg"] == SVG

    figure.svg = SVG.replace("A</text>", "B</text>")
    figure.save()
    assert "B</text>" in QuestionPublicSerializer(question).data["figure"]["svg"]

    figure.delete()
    assert QuestionPublicSerializer(question).data["figure"] is None


# --- refusals ---------------------------------------------------------------


@pytest.mark.parametrize("markup", [
    '<svg><script>alert(1)</script></svg>',
    '<svg onload="alert(1)"><circle r="1"/></svg>',
    '<svg><circle r="1" onclick="steal()"/></svg>',
    '<svg><a href="javascript:alert(1)"><text>x</text></a></svg>',
    '<svg><foreignObject><b>hi</b></foreignObject></svg>',
    '<svg><image href="https://evil.example/pixel.png"/></svg>',
    '<svg><iframe src="/admin"></iframe></svg>',
])
def test_executable_or_remote_markup_is_refused(markup):
    with pytest.raises(Exception):
        ModeFigure.objects.create(topic=TOPIC, mode=MODE, svg=markup)
    assert ModeFigure.objects.count() == 0


@pytest.mark.parametrize("markup", ["", "   ", "<div>not a figure</div>"])
def test_markup_that_is_not_an_svg_is_refused(markup):
    with pytest.raises(Exception):
        ModeFigure.objects.create(topic=TOPIC, mode=MODE, svg=markup)


def test_validation_runs_on_plain_save_not_only_in_the_admin():
    """A management command or a shell import must not be able to bypass it."""
    row = ModeFigure(topic=TOPIC, mode=MODE, svg=SVG)
    row.save()
    row.svg = '<svg onload="alert(1)"/>'
    with pytest.raises(Exception):
        row.save()
    row.refresh_from_db()
    assert "onload" not in row.svg
