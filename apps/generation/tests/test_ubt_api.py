"""The UBT endpoints under /api/v1/ubt/.

Synchronous by design: unlike the MAIQE job endpoints next door, generation here
is pure Python and localization is a dict lookup, so there is no job to poll and
nothing to stream.

The security-relevant assertion in this file is
test_published_questions_never_expose_the_answer: /preview/ deliberately returns
`is_correct` and `misconception`, and /questions/ must never.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.assessments.models import AnswerOption, Question
from ubt_question_engine import i18n
from ubt_question_engine.testing import use_fake_translations, use_no_translations

pytestmark = pytest.mark.django_db

TOPIC = "ubt_roots_expressions"
PREVIEW = "/api/v1/ubt/preview/"
PUBLISH = "/api/v1/ubt/questions/"
TOPICS = "/api/v1/ubt/topics/"
COVERAGE = "/api/v1/ubt/coverage/"


def _post(client, url, **payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


@pytest.fixture
def staff_client():
    user = get_user_model().objects.create(
        email="staff@example.com", is_staff=True, is_superuser=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def student_client():
    user = get_user_model().objects.create(email="student@example.com")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture(autouse=True)
def no_translations():
    """Default state: English only, which is what a fresh checkout can serve."""
    with use_no_translations():
        yield


# --- routing and permissions ------------------------------------------------


def test_routes_resolve():
    assert reverse("v1:ubt:ubt-topics") == TOPICS
    assert reverse("v1:ubt:ubt-preview") == PREVIEW
    assert reverse("v1:ubt:ubt-questions") == PUBLISH
    assert reverse("v1:ubt:ubt-coverage") == COVERAGE


def test_the_catalogue_needs_authentication(student_client):
    assert APIClient().get(TOPICS).status_code in (401, 403)
    assert student_client.get(TOPICS).status_code == 200


@pytest.mark.parametrize("url", [PREVIEW, PUBLISH])
def test_generating_is_staff_only(url, student_client):
    """Preview leaks the answer key; publish writes to the bank."""
    assert _post(student_client, url, topic=TOPIC).status_code == 403


def test_coverage_is_staff_only(student_client, staff_client):
    assert student_client.get(COVERAGE).status_code == 403
    assert staff_client.get(COVERAGE).status_code == 200


# --- the catalogue ----------------------------------------------------------


def test_the_catalogue_describes_every_topic(staff_client):
    body = staff_client.get(TOPICS).json()
    assert len(body) >= 58
    entry = next(t for t in body if t["topic"] == TOPIC)
    assert entry["display_name"]
    assert entry["tag_slug"]
    assert entry["supported_difficulties"]
    assert entry["default_difficulty"] in entry["supported_difficulties"]
    assert entry["modes"]


def test_coverage_reports_what_can_be_served(staff_client):
    """Counts are derived, not pinned.

    The surface is one string per distinct blueprint instruction, so every topic
    added or retired moves the total. Hard-coding it means an unrelated
    blueprint edit fails a test about the coverage ENDPOINT. What matters here
    is the relationship: English is complete by definition, an unbuilt language
    is at zero, and only complete languages are publishable.
    """
    total = len(i18n.translatable())
    body = staff_client.get(COVERAGE).json()
    assert body["translatable_strings"] == total
    assert body["coverage"]["en"] == [total, total]
    assert body["coverage"]["kk"] == [0, total]
    assert body["publishable_languages"] == ["en"]


# --- preview ----------------------------------------------------------------


def test_preview_writes_nothing(staff_client):
    response = _post(staff_client, PREVIEW, topic=TOPIC, count=3, seed=41)
    assert response.status_code == 200
    body = response.json()
    assert body["generated"] == 3
    assert body["failures"] == []
    assert Question.objects.count() == 0


def test_preview_shows_the_answer_key_because_it_is_a_review_tool(staff_client):
    body = _post(staff_client, PREVIEW, topic=TOPIC, count=1, seed=41).json()
    options = body["items"][0]["options"]
    assert len(options) == 5
    assert sum(o["is_correct"] for o in options) == 1
    assert any(o["misconception"] for o in options)
    assert body["items"][0]["answer_latex"]


def test_the_same_seed_returns_the_same_preview(staff_client):
    first = _post(staff_client, PREVIEW, topic=TOPIC, count=3, seed=777).json()
    second = _post(staff_client, PREVIEW, topic=TOPIC, count=3, seed=777).json()
    assert first["items"] == second["items"]


def test_an_omitted_seed_is_drawn_and_reported(staff_client):
    """So a reviewer who finds a bad item can hand back the seed."""
    body = _post(staff_client, PREVIEW, topic=TOPIC, count=1).json()
    assert isinstance(body["seed"], int)
    replay = _post(staff_client, PREVIEW, topic=TOPIC, count=1,
                   seed=body["seed"]).json()
    assert replay["items"] == body["items"]


# --- publishing -------------------------------------------------------------


def test_publishing_creates_rows(staff_client):
    body = _post(staff_client, PUBLISH, topic=TOPIC, count=2, seed=41).json()
    assert body["published"] == 2
    assert body["failed"] == 0
    assert body["languages"] == ["en"]
    assert Question.objects.count() == 2
    assert AnswerOption.objects.count() == 10


def test_published_questions_never_expose_the_answer(staff_client):
    """The public shape must not carry is_correct or misconception."""
    body = _post(staff_client, PUBLISH, topic=TOPIC, count=1, seed=41).json()
    serialized = json.dumps(body["questions"])
    assert "is_correct" not in serialized
    assert "misconception" not in serialized
    assert sorted(body["questions"][0]) == ["figure", "id", "image", "options", "text"]


def test_republishing_the_same_seed_adds_nothing(staff_client):
    _post(staff_client, PUBLISH, topic=TOPIC, count=2, seed=41)
    before = Question.objects.count()
    body = _post(staff_client, PUBLISH, topic=TOPIC, count=2, seed=41).json()
    assert body["duplicates"] == 2
    assert body["published"] == 0
    assert Question.objects.count() == before


def test_publishing_reports_the_language_free_hash(staff_client):
    body = _post(staff_client, PUBLISH, topic=TOPIC, count=1, seed=41).json()
    item = body["items"][0]
    question = Question.objects.get(pk=item["question_ids"]["en"])
    assert question.solution["content_hash"] == item["content_hash"]


def test_the_whole_collection_publishes_through_the_api(staff_client):
    body = _post(staff_client, PUBLISH, count=1, all_difficulties=True).json()
    assert body["failed"] == 0, body["failures"][:3]
    assert body["requested"] == body["published"]
    assert Question.objects.filter(tags__isnull=True).count() == 0


def test_three_languages_when_the_cache_is_complete(staff_client):
    with use_fake_translations():
        body = _post(staff_client, PUBLISH, topic=TOPIC, count=1, seed=41).json()
    assert sorted(body["languages"]) == ["en", "kk", "ru"]
    assert Question.objects.count() == 3
    assert len({q.solution["content_hash"] for q in Question.objects.all()}) == 1


# --- refusals ---------------------------------------------------------------


def test_an_unknown_topic_is_a_404_with_a_suggestion(staff_client):
    response = _post(staff_client, PREVIEW, topic="ubt_roots_expresions")
    assert response.status_code == 404
    assert "ubt_roots_expressions" in response.json()["detail"]


def test_an_unsupported_difficulty_is_refused(staff_client):
    """ubt_integral_areas supports difficulty 3 only.

    Asserted on `detail`, not `code`: apps.common.exceptions derives the code
    from the exception CLASS, so every ValidationError in the project reports
    "validation_error" regardless of what the payload carries. The message is
    the part that reaches a human.
    """
    response = _post(staff_client, PREVIEW, topic="ubt_integral_areas", difficulty=1)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "does not support difficulty 1" in detail
    assert "[3]" in detail


def test_the_batch_cap_is_enforced(staff_client):
    assert _post(staff_client, PREVIEW, topic=TOPIC, count=999).status_code == 400
    assert _post(staff_client, PREVIEW, topic=TOPIC, count=0).status_code == 400


def test_an_unknown_language_is_refused(staff_client):
    assert _post(staff_client, PREVIEW, topic=TOPIC, languages=["de"]).status_code == 400


def test_an_untranslated_language_fails_fast(staff_client):
    """Not 200 with N identical failures: that buries a config problem."""
    response = _post(staff_client, PREVIEW, topic=TOPIC, languages=["kk"])
    assert response.status_code == 400
    total = len(i18n.translatable())
    detail = response.json()["detail"]
    assert f"missing {total} of {total} translations" in detail
    assert "build_i18n" in detail
