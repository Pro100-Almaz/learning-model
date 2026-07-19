"""Tests for the analytics services + endpoints.

Covers:
- compute_tag_stats: zero-state, mixed correctness, null-safety on total=0
- compute_recommendations: filtering at the 50% boundary, lesson grouping
- HTTP endpoints: happy paths under /api/v1/analytics/tags|recommendations
"""

from __future__ import annotations

from datetime import timedelta

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import StudentProfile
from apps.analytics.services import (
    build_post_topic_results,
    build_student_report,
    classify_topics,
    compute_recommendations,
    compute_tag_stats,
)
from apps.assessments.models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestAttempt,
    TestQuestion,
)
from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag
from apps.users.models import CustomUser as User


def _make_module(order: int = 0) -> Module:
    subject, _ = Subject.objects.get_or_create(
        slug="profile_math", defaults={"name": "Профильная математика"}
    )
    grade, _ = ClassGrade.objects.get_or_create(grade=11, subject=subject)
    return Module.objects.create(
        title=f"Module {order}",
        slug=f"module-{order}",
        order=order,
        class_grade=grade,
    )


def _make_lesson(
    module: Module, *, title: str, order: int = 0, tag: Tag | None = None
) -> Lesson:
    return Lesson.objects.create(
        module=module,
        title=title,
        description="",
        video_url="https://example.com/v",
        video_provider="youtube",
        duration_sec=600,
        order=order,
        tag=tag,
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


def _make_test(
    questions: list[Question],
    *,
    lesson: Lesson | None = None,
    type: str = "micro",
) -> Test:
    t = Test.objects.create(type=type, title="T", lesson=lesson)
    for i, q in enumerate(questions):
        TestQuestion.objects.create(test=t, question=q, order=i)
    return t


def _make_attempt(
    *,
    user: User,
    test: Test,
    answers: list[tuple[Question, AnswerOption, bool]],
    completed: bool = True,
    score: float | None = None,
    finished_at=None,
) -> TestAttempt:
    attempt = TestAttempt.objects.create(
        student=user,
        test=test,
        is_completed=completed,
        score=score,
        finished_at=finished_at,
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


def _topic_result(tag_id: int, score: float) -> dict:
    """One ``build_post_topic_results`` entry, hand-built (no DB).

    classify_topics only reads ``post_score`` to route the entry and stores the
    whole dict, so the other fields just need to be present and plausible.
    """
    return {
        "tag": {"id": tag_id, "name": f"Topic {tag_id}", "slug": f"topic-{tag_id}"},
        "post_score": score,
        "correct": 0,
        "total": 0,
        "finished_at": None,
    }


class ClassifyTopicsTests(SimpleTestCase):
    """Protects the weak/improving/solid boundaries (config.WEAK_BELOW=50,
    SOLID_MIN=75) and the weakest-first ordering of the weak bucket.

    Pure function over a dict — no DB, hence SimpleTestCase.
    """

    def _post_results(self) -> dict:
        # Scores chosen to hit every branch AND both boundaries exactly.
        return {
            1: _topic_result(1, 30),  # weak
            2: _topic_result(2, 49),  # weak
            3: _topic_result(3, 50),  # improving (low boundary → rounds up)
            4: _topic_result(4, 60),  # improving
            5: _topic_result(5, 74),  # improving
            6: _topic_result(6, 75),  # solid (high boundary → rounds up)
            7: _topic_result(7, 90),  # solid
        }

    def test_bucket_counts(self):
        result = classify_topics(self._post_results())
        self.assertEqual(len(result["weak"]), 2)
        self.assertEqual(len(result["improving"]), 3)
        self.assertEqual(len(result["solid"]), 2)

    def test_boundaries_round_upward(self):
        result = classify_topics(self._post_results())
        weak = [e["post_score"] for e in result["weak"]]
        improving = [e["post_score"] for e in result["improving"]]
        solid = [e["post_score"] for e in result["solid"]]
        # Exactly 50 is improving, never weak.
        self.assertIn(50, improving)
        self.assertNotIn(50, weak)
        # Exactly 75 is solid, never improving.
        self.assertIn(75, solid)
        self.assertNotIn(75, improving)

    def test_weak_bucket_is_sorted_weakest_first(self):
        result = classify_topics(self._post_results())
        scores = [e["post_score"] for e in result["weak"]]
        self.assertEqual(scores, sorted(scores))

    def test_every_topic_lands_in_exactly_one_bucket(self):
        results = self._post_results()
        buckets = classify_topics(results)
        placed = len(buckets["weak"]) + len(buckets["improving"]) + len(buckets["solid"])
        # No topic dropped and none double-counted.
        self.assertEqual(placed, len(results))

    def test_empty_input_returns_three_empty_lists(self):
        self.assertEqual(
            classify_topics({}),
            {"weak": [], "improving": [], "solid": []},
        )


class BuildPostTopicResultsTests(APITestCase):
    """build_post_topic_results: maps the latest completed *micro* attempt per
    topic via test.lesson.tag, and ignores everything that isn't one.
    """

    def setUp(self):
        self.user = User.objects.create_user(email="learner@example.com", password="pw")
        self.module = _make_module(order=0)
        self.trig = Tag.objects.create(name="Trigonometry", slug="trig")

    def test_maps_micro_attempt_to_topic_with_score_and_counts(self):
        lesson = _make_lesson(self.module, title="L", order=0, tag=self.trig)
        q1, c1, _ = _make_question(lesson=lesson, tags=[self.trig], text="q1")
        q2, _, w2 = _make_question(lesson=lesson, tags=[self.trig], text="q2")
        test = _make_test([q1, q2], lesson=lesson)
        _make_attempt(
            user=self.user,
            test=test,
            answers=[(q1, c1, True), (q2, w2, False)],
            score=50.0,
            finished_at=timezone.now(),
        )

        result = build_post_topic_results(self.user)

        self.assertIn(self.trig.id, result)
        entry = result[self.trig.id]
        self.assertEqual(entry["post_score"], 50.0)
        self.assertEqual(entry["correct"], 1)
        self.assertEqual(entry["total"], 2)
        self.assertEqual(entry["tag"]["slug"], "trig")

    def test_keeps_only_the_latest_attempt_per_topic(self):
        lesson = _make_lesson(self.module, title="L", order=0, tag=self.trig)
        q, c, w = _make_question(lesson=lesson, tags=[self.trig], text="q")
        test = _make_test([q], lesson=lesson)
        now = timezone.now()
        # Earlier, low score.
        _make_attempt(
            user=self.user, test=test, answers=[(q, w, False)],
            score=30.0, finished_at=now - timedelta(days=1),
        )
        # Later, high score — this one must win.
        _make_attempt(
            user=self.user, test=test, answers=[(q, c, True)],
            score=70.0, finished_at=now,
        )

        result = build_post_topic_results(self.user)

        self.assertEqual(result[self.trig.id]["post_score"], 70.0)

    def test_empty_when_no_completed_micro_attempts(self):
        fresh = User.objects.create_user(email="fresh@example.com", password="pw")
        self.assertEqual(build_post_topic_results(fresh), {})

    def test_skips_attempt_whose_lesson_has_no_tag(self):
        lesson = _make_lesson(self.module, title="untagged", order=0, tag=None)
        q, c, _ = _make_question(lesson=lesson, tags=[self.trig], text="q")
        test = _make_test([q], lesson=lesson)
        _make_attempt(
            user=self.user, test=test, answers=[(q, c, True)],
            score=90.0, finished_at=timezone.now(),
        )
        # No topic can be attributed (lesson.tag is None), so nothing is returned.
        self.assertEqual(build_post_topic_results(self.user), {})

    def test_ignores_mock_and_incomplete_attempts(self):
        lesson = _make_lesson(self.module, title="L", order=0, tag=self.trig)
        q, c, w = _make_question(lesson=lesson, tags=[self.trig], text="q")
        # A completed MOCK (wrong type) must be ignored.
        mock = _make_test([q], lesson=lesson, type="mock")
        _make_attempt(
            user=self.user, test=mock, answers=[(q, c, True)],
            score=88.0, finished_at=timezone.now(),
        )
        # An INCOMPLETE micro attempt must be ignored.
        micro = _make_test([q], lesson=lesson, type="micro")
        _make_attempt(
            user=self.user, test=micro, answers=[(q, w, False)],
            completed=False, score=10.0, finished_at=timezone.now(),
        )
        self.assertEqual(build_post_topic_results(self.user), {})


class BuildStudentReportTests(APITestCase):
    """build_student_report composes buckets + recommendations, and degrades
    gracefully to empty math/universities when the student has no math mock
    (calculate_grant raises NoMockError).
    """

    def setUp(self):
        self.user = User.objects.create_user(email="learner@example.com", password="pw")
        self.module = _make_module(order=0)
        self.trig = Tag.objects.create(name="Trigonometry", slug="trig")
        # A completed micro exam with a low score → trig is a WEAK topic.
        self.lesson = _make_lesson(self.module, title="Trig basics", order=1, tag=self.trig)
        q, _, wrong = _make_question(lesson=self.lesson, tags=[self.trig], text="q")
        test = _make_test([q], lesson=self.lesson)
        _make_attempt(
            user=self.user,
            test=test,
            answers=[(q, wrong, False)],
            score=20.0,
            finished_at=timezone.now(),
        )

    def test_report_has_all_four_top_level_keys(self):
        report = build_student_report(self.user)
        self.assertEqual(
            set(report.keys()), {"buckets", "recommendations", "math", "universities"}
        )

    def test_weak_topic_flows_into_buckets_and_recommendations(self):
        report = build_student_report(self.user)
        weak_slugs = [e["tag"]["slug"] for e in report["buckets"]["weak"]]
        self.assertIn("trig", weak_slugs)
        # The weak topic gets a recommendation carrying its remedial lesson.
        rec_slugs = [r["tag"]["slug"] for r in report["recommendations"]]
        self.assertIn("trig", rec_slugs)
        trig_rec = next(r for r in report["recommendations"] if r["tag"]["slug"] == "trig")
        lesson_ids = [lsn["id"] for lsn in trig_rec["lessons"]]
        self.assertIn(self.lesson.id, lesson_ids)

    def test_degrades_gracefully_without_a_math_mock(self):
        # No mock attempt exists → calculate_grant raises NoMockError, caught.
        report = build_student_report(self.user)
        self.assertEqual(
            report["math"],
            {"current_math": None, "target_math": None, "gap": None},
        )
        self.assertEqual(
            report["universities"], {"qualifying": [], "near_miss": []}
        )

    def test_target_math_comes_from_profile_even_without_a_mock(self):
        StudentProfile.objects.create(user=self.user, target_math_score=80)
        report = build_student_report(self.user)
        # Target is read from the profile; current/gap stay None (no mock yet).
        self.assertEqual(report["math"]["target_math"], 80)
        self.assertIsNone(report["math"]["current_math"])
        self.assertIsNone(report["math"]["gap"])
