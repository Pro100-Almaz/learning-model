"""Roadmap generation + status-update tests.

Covers:
- generate_roadmap_for_student: returns None without an attempt, orders items
  weakest-tag first, dedupes lessons across overlapping tags, archives the
  previous active roadmap, and falls back to a mock attempt when no
  diagnostic exists.
- mark_item_status_from_attempt: passing score → completed, failing score →
  in_progress (only from pending), no roadmap / no matching test → no-op.
- The assessments.finish_attempt hook auto-generates a roadmap for a
  diagnostic attempt.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.assessments.models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestAttempt,
    TestQuestion,
)
from apps.assessments.services import finish_attempt
from apps.content.models import Lesson, Module, Tag
from apps.roadmap.models import Roadmap, RoadmapItem
from apps.roadmap.services import (
    generate_roadmap_for_student,
    get_active_roadmap,
    mark_item_status_from_attempt,
)
from apps.users.models import CustomUser as User


def _make_question(*, lesson, tags, correct=True, text="Q"):
    q = Question.objects.create(
        text=text, explanation="why", difficulty=1, lesson=lesson
    )
    q.tags.set(tags)
    right = AnswerOption.objects.create(question=q, text="r", is_correct=True)
    wrong = AnswerOption.objects.create(question=q, text="w", is_correct=False)
    return q, right, wrong


def _make_diag_test(questions):
    t = Test.objects.create(type="diagnostic", title="Diag")
    for i, q in enumerate(questions):
        TestQuestion.objects.create(test=t, question=q, order=i)
    return t


def _make_micro_test(lesson, questions):
    t = Test.objects.create(type="micro", title=f"Micro {lesson.title}", lesson=lesson)
    for i, q in enumerate(questions):
        TestQuestion.objects.create(test=t, question=q, order=i)
    return t


def _make_attempt(*, user, test, answer_results, completed=True):
    """answer_results: list of (question, selected_option, is_correct)."""
    attempt = TestAttempt.objects.create(
        student=user, test=test, is_completed=completed
    )
    if completed:
        attempt.finished_at = timezone.now()
        attempt.score = (
            sum(1 for _, _, c in answer_results if c) / len(answer_results) * 100
            if answer_results
            else 0
        )
        attempt.save(update_fields=["finished_at", "score"])
    for q, opt, correct in answer_results:
        AttemptAnswer.objects.create(
            attempt=attempt, question=q, selected_option=opt, is_correct=correct
        )
    return attempt


class GenerationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="s@e.com", password="pw")
        self.module = Module.objects.create(
            title="Алгебра", slug="algebra", order=0, subject="profile_math"
        )
        self.t_log = Tag.objects.create(name="Логарифмы", slug="logarithms")
        self.t_trig = Tag.objects.create(name="Тригонометрия", slug="trig")
        self.t_frac = Tag.objects.create(name="Дроби", slug="fractions")
        self.l_log = Lesson.objects.create(
            module=self.module, title="Logs", description="",
            video_url="https://e.com/v", video_provider="youtube",
            duration_sec=300, order=0,
        )
        self.l_trig = Lesson.objects.create(
            module=self.module, title="Trig", description="",
            video_url="https://e.com/v", video_provider="youtube",
            duration_sec=300, order=1,
        )
        self.l_frac = Lesson.objects.create(
            module=self.module, title="Frac", description="",
            video_url="https://e.com/v", video_provider="youtube",
            duration_sec=300, order=2,
        )
        # 1 question per tag, each linked to its lesson.
        self.q_log, self.opt_log_r, self.opt_log_w = _make_question(
            lesson=self.l_log, tags=[self.t_log], text="LogQ"
        )
        self.q_trig, self.opt_trig_r, self.opt_trig_w = _make_question(
            lesson=self.l_trig, tags=[self.t_trig], text="TrigQ"
        )
        self.q_frac, self.opt_frac_r, self.opt_frac_w = _make_question(
            lesson=self.l_frac, tags=[self.t_frac], text="FracQ"
        )
        # Micro-tests linked to each lesson.
        self.mt_log = _make_micro_test(self.l_log, [self.q_log])
        self.mt_trig = _make_micro_test(self.l_trig, [self.q_trig])
        # No micro for l_frac — should still appear in roadmap with micro_test=None.
        self.diag = _make_diag_test([self.q_log, self.q_trig, self.q_frac])

    def test_returns_none_when_no_attempt_exists(self):
        roadmap = generate_roadmap_for_student(self.user)
        self.assertIsNone(roadmap)
        self.assertFalse(Roadmap.objects.exists())

    def test_orders_items_weakest_tag_first(self):
        # Trig wrong, Log correct, Frac correct → trig (0%) weakest.
        attempt = _make_attempt(
            user=self.user, test=self.diag,
            answer_results=[
                (self.q_log, self.opt_log_r, True),
                (self.q_trig, self.opt_trig_w, False),
                (self.q_frac, self.opt_frac_r, True),
            ],
        )
        roadmap = generate_roadmap_for_student(self.user, source_attempt=attempt)
        self.assertIsNotNone(roadmap)
        self.assertTrue(roadmap.is_active)
        items = list(roadmap.items.order_by("order"))
        # Weakest tag's lesson must come first.
        self.assertEqual(items[0].lesson_id, self.l_trig.pk)
        self.assertEqual(items[0].weak_tag_id, self.t_trig.pk)
        self.assertIn("Тригонометрия", items[0].rationale)
        # All three lessons covered, no duplicates.
        lesson_ids = [it.lesson_id for it in items]
        self.assertEqual(sorted(lesson_ids), sorted([self.l_log.pk, self.l_trig.pk, self.l_frac.pk]))
        self.assertEqual(len(set(lesson_ids)), 3)

    def test_links_micro_test_when_available(self):
        attempt = _make_attempt(
            user=self.user, test=self.diag,
            answer_results=[
                (self.q_log, self.opt_log_w, False),
                (self.q_trig, self.opt_trig_r, True),
                (self.q_frac, self.opt_frac_r, True),
            ],
        )
        roadmap = generate_roadmap_for_student(self.user, source_attempt=attempt)
        by_lesson = {it.lesson_id: it for it in roadmap.items.all()}
        self.assertEqual(by_lesson[self.l_log.pk].micro_test_id, self.mt_log.pk)
        self.assertEqual(by_lesson[self.l_trig.pk].micro_test_id, self.mt_trig.pk)
        self.assertIsNone(by_lesson[self.l_frac.pk].micro_test_id)

    def test_dedupes_lessons_across_overlapping_tags(self):
        # Tag a lesson with TWO tags so both weakness paths could hit it.
        self.q_log.tags.add(self.t_trig)
        attempt = _make_attempt(
            user=self.user, test=self.diag,
            answer_results=[
                (self.q_log, self.opt_log_w, False),
                (self.q_trig, self.opt_trig_w, False),
                (self.q_frac, self.opt_frac_r, True),
            ],
        )
        roadmap = generate_roadmap_for_student(self.user, source_attempt=attempt)
        lesson_ids = [it.lesson_id for it in roadmap.items.all()]
        # l_log should appear exactly once even though it matches both weak tags.
        self.assertEqual(lesson_ids.count(self.l_log.pk), 1)

    def test_archives_previous_active_roadmap(self):
        attempt = _make_attempt(
            user=self.user, test=self.diag,
            answer_results=[
                (self.q_log, self.opt_log_r, True),
                (self.q_trig, self.opt_trig_w, False),
                (self.q_frac, self.opt_frac_r, True),
            ],
        )
        first = generate_roadmap_for_student(self.user, source_attempt=attempt)
        second = generate_roadmap_for_student(self.user, source_attempt=attempt)
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        # get_active_roadmap returns the new one.
        active = get_active_roadmap(self.user)
        self.assertEqual(active.pk, second.pk)

    def test_falls_back_to_mock_when_no_diagnostic(self):
        mock_test = Test.objects.create(type="mock", title="Mock", time_limit_sec=600)
        TestQuestion.objects.create(test=mock_test, question=self.q_log, order=0)
        attempt = _make_attempt(
            user=self.user, test=mock_test,
            answer_results=[(self.q_log, self.opt_log_w, False)],
        )
        roadmap = generate_roadmap_for_student(self.user)
        self.assertIsNotNone(roadmap)
        self.assertEqual(roadmap.source, "mock_recompute")
        self.assertEqual(roadmap.source_attempt_id, attempt.pk)


class StatusUpdateTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="s@e.com", password="pw")
        self.module = Module.objects.create(
            title="Алгебра", slug="algebra", order=0, subject="profile_math"
        )
        self.tag = Tag.objects.create(name="Логарифмы", slug="logarithms")
        self.lesson = Lesson.objects.create(
            module=self.module, title="Logs", description="",
            video_url="https://e.com/v", video_provider="youtube",
            duration_sec=300, order=0,
        )
        self.q, self.opt_r, self.opt_w = _make_question(
            lesson=self.lesson, tags=[self.tag], text="LogQ"
        )
        self.mt = _make_micro_test(self.lesson, [self.q])
        self.diag = _make_diag_test([self.q])
        # Seed an active roadmap via a finished diagnostic.
        self.diag_attempt = _make_attempt(
            user=self.user, test=self.diag,
            answer_results=[(self.q, self.opt_w, False)],
        )
        self.roadmap = generate_roadmap_for_student(
            self.user, source_attempt=self.diag_attempt
        )
        self.item = self.roadmap.items.get(lesson=self.lesson)
        self.assertEqual(self.item.status, "pending")
        self.assertEqual(self.item.micro_test_id, self.mt.pk)

    def test_passing_score_marks_item_completed(self):
        attempt = _make_attempt(
            user=self.user, test=self.mt,
            answer_results=[(self.q, self.opt_r, True)],  # 100%
        )
        result = mark_item_status_from_attempt(attempt)
        self.assertIsNotNone(result)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "completed")
        self.assertIsNotNone(self.item.completed_at)

    def test_failing_score_marks_item_in_progress(self):
        attempt = _make_attempt(
            user=self.user, test=self.mt,
            answer_results=[(self.q, self.opt_w, False)],  # 0%
        )
        mark_item_status_from_attempt(attempt)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "in_progress")
        self.assertIsNone(self.item.completed_at)

    def test_no_roadmap_is_safe_noop(self):
        other = User.objects.create_user(email="other@e.com", password="pw")
        attempt = _make_attempt(
            user=other, test=self.mt,
            answer_results=[(self.q, self.opt_r, True)],
        )
        # Should be None and NOT touch this user's item.
        result = mark_item_status_from_attempt(attempt)
        self.assertIsNone(result)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "pending")

    def test_finish_attempt_hook_auto_generates_roadmap_for_diagnostic(self):
        # Wipe any prior roadmaps; start a brand-new unfinished diagnostic.
        Roadmap.objects.filter(student=self.user).delete()
        new_attempt = TestAttempt.objects.create(
            student=self.user, test=self.diag, is_completed=False
        )
        AttemptAnswer.objects.create(
            attempt=new_attempt, question=self.q,
            selected_option=self.opt_w, is_correct=False,
        )
        # Calling finish_attempt should trigger the hook → new active roadmap.
        finish_attempt(new_attempt)
        active = get_active_roadmap(self.user)
        self.assertIsNotNone(active)
        self.assertEqual(active.source, "diagnostic")
        self.assertEqual(active.source_attempt_id, new_attempt.pk)


class EndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="s@e.com", password="pw")
        self.module = Module.objects.create(
            title="Алгебра", slug="algebra", order=0, subject="profile_math"
        )
        self.tag = Tag.objects.create(name="Логарифмы", slug="logarithms")
        self.lesson = Lesson.objects.create(
            module=self.module, title="Logs", description="",
            video_url="https://e.com/v", video_provider="youtube",
            duration_sec=300, order=0,
        )
        self.q, self.opt_r, self.opt_w = _make_question(
            lesson=self.lesson, tags=[self.tag]
        )
        self.diag = _make_diag_test([self.q])
        self.client.force_authenticate(user=self.user)

    def test_diagnostic_info_when_no_attempt(self):
        resp = self.client.get("/api/v1/roadmap/diagnostic/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["test_id"], self.diag.pk)
        self.assertFalse(resp.data["taken"])
        self.assertFalse(resp.data["completed"])
        self.assertIsNone(resp.data["attempt_id"])

    def test_diagnostic_info_after_finished_attempt(self):
        attempt = _make_attempt(
            user=self.user, test=self.diag,
            answer_results=[(self.q, self.opt_r, True)],
        )
        resp = self.client.get("/api/v1/roadmap/diagnostic/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["taken"])
        self.assertTrue(resp.data["completed"])
        self.assertEqual(resp.data["attempt_id"], attempt.pk)
        self.assertEqual(resp.data["score"], 100.0)

    def test_roadmap_get_returns_409_when_no_attempt(self):
        resp = self.client.get("/api/v1/roadmap/")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data["code"], "no_diagnostic_attempt")

    def test_roadmap_get_lazy_generates_after_diagnostic(self):
        _make_attempt(
            user=self.user, test=self.diag,
            answer_results=[(self.q, self.opt_w, False)],
        )
        resp = self.client.get("/api/v1/roadmap/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["source"], "diagnostic")
        self.assertGreaterEqual(resp.data["stats"]["total"], 1)
        self.assertEqual(resp.data["stats"]["pending"], resp.data["stats"]["total"])

    def test_roadmap_requires_authentication(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/roadmap/")
        self.assertEqual(resp.status_code, 401)

    def test_regenerate_creates_new_active_roadmap(self):
        _make_attempt(
            user=self.user, test=self.diag,
            answer_results=[(self.q, self.opt_w, False)],
        )
        first = self.client.get("/api/v1/roadmap/").data
        regen = self.client.post("/api/v1/roadmap/regenerate/").data
        self.assertNotEqual(first["id"], regen["id"])
        # Only one active roadmap remains.
        self.assertEqual(
            Roadmap.objects.filter(student=self.user, is_active=True).count(), 1
        )
