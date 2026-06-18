"""Tests for the analytics services + endpoints.

Covers:
- compute_tag_stats: zero-state, mixed correctness, null-safety on total=0
- compute_recommendations: filtering at the 50% boundary, lesson grouping
- HTTP endpoints: happy paths under /api/v1/analytics/tags|recommendations
"""

from __future__ import annotations

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.analytics.services import compute_recommendations, compute_tag_stats
from apps.assessments.models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestAttempt,
    TestQuestion,
)
from apps.content.models import Lesson, Module, Tag
from apps.users.models import CustomUser as User


def _make_module(order: int = 0) -> Module:
    return Module.objects.create(
        title=f"Module {order}",
        slug=f"module-{order}",
        order=order,
        subject="profile_math",
    )


def _make_lesson(module: Module, *, title: str, order: int = 0) -> Lesson:
    return Lesson.objects.create(
        module=module,
        title=title,
        description="",
        video_url="https://example.com/v",
        video_provider="youtube",
        duration_sec=600,
        order=order,
    )


def _make_question(
    *,
    lesson: Lesson | None,
    tags: list[Tag],
    text: str = "Q?",
) -> tuple[Question, AnswerOption, AnswerOption]:
    question = Question.objects.create(
        text=text,
        explanation="because",
        difficulty=1,
        lesson=lesson,
    )
    question.tags.set(tags)
    correct = AnswerOption.objects.create(
        question=question, text="right", is_correct=True
    )
    wrong = AnswerOption.objects.create(
        question=question, text="nope", is_correct=False
    )
    return question, correct, wrong


def _make_test(questions: list[Question]) -> Test:
    t = Test.objects.create(type="micro", title="T")
    for i, q in enumerate(questions):
        TestQuestion.objects.create(test=t, question=q, order=i)
    return t


def _make_attempt(
    *,
    user: User,
    test: Test,
    answers: list[tuple[Question, AnswerOption, bool]],
    completed: bool = True,
) -> TestAttempt:
    attempt = TestAttempt.objects.create(
        student=user, test=test, is_completed=completed
    )
    for question, selected, correct in answers:
        AttemptAnswer.objects.create(
            attempt=attempt,
            question=question,
            selected_option=selected,
            is_correct=correct,
        )
    return attempt


class TagStatsServiceTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="learner@example.com", password="pw"
        )
        self.module = _make_module(order=0)
        self.algebra = Tag.objects.create(name="Algebra", slug="algebra")
        self.trig = Tag.objects.create(name="Trigonometry", slug="trig")
        self.geometry = Tag.objects.create(name="Geometry", slug="geometry")

    def test_returns_zero_state_for_user_with_no_attempts(self):
        stats = compute_tag_stats(self.user)
        self.assertEqual(len(stats), 3)
        # ordered by Tag.name
        names = [s["tag"]["name"] for s in stats]
        self.assertEqual(names, ["Algebra", "Geometry", "Trigonometry"])
        for s in stats:
            self.assertEqual(s["correct"], 0)
            self.assertEqual(s["total"], 0)
            # Null-safety: percent stays at 0.0 when total == 0.
            self.assertEqual(s["percent"], 0.0)

    def test_counts_only_completed_attempts_for_the_user(self):
        lesson = _make_lesson(self.module, title="L1", order=0)
        q_alg, alg_correct, alg_wrong = _make_question(
            lesson=lesson, tags=[self.algebra], text="A"
        )
        q_trig, trig_correct, trig_wrong = _make_question(
            lesson=lesson, tags=[self.trig], text="T"
        )
        test = _make_test([q_alg, q_trig])

        # This user: 1 algebra correct, 1 trig wrong (completed)
        _make_attempt(
            user=self.user,
            test=test,
            answers=[
                (q_alg, alg_correct, True),
                (q_trig, trig_wrong, False),
            ],
            completed=True,
        )

        # Same user has an INCOMPLETE attempt — must be ignored.
        _make_attempt(
            user=self.user,
            test=test,
            answers=[(q_alg, alg_wrong, False)],
            completed=False,
        )

        # Another user — must be ignored.
        other = User.objects.create_user(email="other@example.com", password="pw")
        _make_attempt(
            user=other,
            test=test,
            answers=[(q_alg, alg_wrong, False)],
            completed=True,
        )

        stats = {s["tag"]["slug"]: s for s in compute_tag_stats(self.user)}

        self.assertEqual(stats["algebra"]["correct"], 1)
        self.assertEqual(stats["algebra"]["total"], 1)
        self.assertEqual(stats["algebra"]["percent"], 100.0)

        self.assertEqual(stats["trig"]["correct"], 0)
        self.assertEqual(stats["trig"]["total"], 1)
        self.assertEqual(stats["trig"]["percent"], 0.0)

        # Geometry was never answered.
        self.assertEqual(stats["geometry"]["correct"], 0)
        self.assertEqual(stats["geometry"]["total"], 0)
        self.assertEqual(stats["geometry"]["percent"], 0.0)


class RecommendationsServiceTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="learner@example.com", password="pw"
        )
        self.module = _make_module(order=0)
        self.weak_tag = Tag.objects.create(name="Logarithms", slug="logarithms")
        self.strong_tag = Tag.objects.create(name="Arithmetic", slug="arithmetic")

        self.lesson_a = _make_lesson(self.module, title="Logs intro", order=1)
        self.lesson_b = _make_lesson(self.module, title="Logs deep", order=2)
        self.lesson_c = _make_lesson(self.module, title="Arithmetic", order=3)

        # 1 weak-tag question on each weak lesson
        self.q_weak_a, _, w_wrong_a = _make_question(
            lesson=self.lesson_a, tags=[self.weak_tag], text="logA"
        )
        self.q_weak_b, _, _ = _make_question(
            lesson=self.lesson_b, tags=[self.weak_tag], text="logB"
        )
        self.q_strong, s_correct, _ = _make_question(
            lesson=self.lesson_c, tags=[self.strong_tag], text="arith"
        )

        test = _make_test([self.q_weak_a, self.q_weak_b, self.q_strong])
        _make_attempt(
            user=self.user,
            test=test,
            answers=[
                # Weak tag: 0/2 = 0%
                (self.q_weak_a, w_wrong_a, False),
                (
                    self.q_weak_b,
                    AnswerOption.objects.filter(
                        question=self.q_weak_b, is_correct=False
                    ).first(),
                    False,
                ),
                # Strong tag: 1/1 = 100%
                (self.q_strong, s_correct, True),
            ],
            completed=True,
        )

    def test_weak_tag_yields_ordered_deduplicated_lessons(self):
        recs = compute_recommendations(self.user)
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["tag"]["slug"], "logarithms")
        self.assertEqual(rec["percent"], 0.0)

        lesson_ids = [l["id"] for l in rec["lessons"]]
        # Ordered by Lesson.order, no duplicates, strong-tag lesson excluded.
        self.assertEqual(lesson_ids, [self.lesson_a.id, self.lesson_b.id])
        self.assertNotIn(self.lesson_c.id, lesson_ids)

    def test_strong_tag_excluded_at_or_above_50_percent(self):
        # The setUp scenario already produces 100% for strong_tag — confirm
        # it isn't returned. Then create a borderline tag at exactly 50%
        # and ensure it is also excluded.
        recs = compute_recommendations(self.user)
        slugs = [r["tag"]["slug"] for r in recs]
        self.assertNotIn("arithmetic", slugs)

        boundary_tag = Tag.objects.create(name="Boundary", slug="boundary")
        lesson = _make_lesson(self.module, title="Boundary", order=10)
        q1, c1, w1 = _make_question(lesson=lesson, tags=[boundary_tag], text="b1")
        q2, c2, w2 = _make_question(lesson=lesson, tags=[boundary_tag], text="b2")
        test = _make_test([q1, q2])
        _make_attempt(
            user=self.user,
            test=test,
            answers=[
                (q1, c1, True),
                (q2, w2, False),
            ],
            completed=True,
        )

        recs = compute_recommendations(self.user)
        slugs = [r["tag"]["slug"] for r in recs]
        # 50% is not strictly less than 50% → not recommended.
        self.assertNotIn("boundary", slugs)

    def test_empty_recommendations_when_no_attempts(self):
        fresh = User.objects.create_user(email="fresh@example.com", password="pw")
        self.assertEqual(compute_recommendations(fresh), [])


class AnalyticsEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="api@example.com", password="pw"
        )
        self.client.force_authenticate(user=self.user)

        self.module = _make_module(order=0)
        self.tag = Tag.objects.create(name="Algebra", slug="algebra")
        self.lesson = _make_lesson(self.module, title="Algebra basics", order=1)
        self.question, correct, wrong = _make_question(
            lesson=self.lesson, tags=[self.tag], text="A"
        )
        test = _make_test([self.question])
        _make_attempt(
            user=self.user,
            test=test,
            answers=[(self.question, wrong, False)],
            completed=True,
        )

    def test_tags_endpoint_returns_contract_shape(self):
        url = reverse("v1:analytics:tags")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertGreaterEqual(len(response.data), 1)
        item = response.data[0]
        self.assertEqual(set(item.keys()), {"tag", "correct", "total", "percent"})
        self.assertEqual(set(item["tag"].keys()), {"id", "name", "slug"})

    def test_tags_endpoint_requires_auth(self):
        self.client.force_authenticate(user=None)
        url = reverse("v1:analytics:tags")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_recommendations_endpoint_returns_weak_tag(self):
        url = reverse("v1:analytics:recommendations")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        rec = response.data[0]
        self.assertEqual(set(rec.keys()), {"tag", "percent", "lessons"})
        self.assertEqual(rec["tag"]["slug"], "algebra")
        self.assertEqual(rec["percent"], 0.0)
        lesson_ids = [l["id"] for l in rec["lessons"]]
        self.assertIn(self.lesson.id, lesson_ids)

    def test_recommendations_endpoint_is_empty_when_no_weak_tags(self):
        fresh = User.objects.create_user(email="strong@example.com", password="pw")
        self.client.force_authenticate(user=fresh)
        url = reverse("v1:analytics:recommendations")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_recommendations_endpoint_requires_auth(self):
        self.client.force_authenticate(user=None)
        url = reverse("v1:analytics:recommendations")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
