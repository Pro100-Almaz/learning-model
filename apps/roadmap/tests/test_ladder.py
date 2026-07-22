"""Chapter-ladder tests (07_Chapter_Ladder_Spec.md §Tests).

Covers the ladder state machine (step up/down, early-stop, asymmetric confirm),
verdict mapping, skip-on-prior + staleness, bank degradation, the mastery-update
side effect, ladder-attempt storage (source="ladder", null test, no global
roadmap), the soft-fail plan, the analytics distribution, and the endpoints.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.analytics.services import compute_ladder_verdict_distribution
from apps.assessments.models import AnswerOption, Question, TestAttempt
from apps.content.models import ClassGrade, Lesson, Module, Subject, Tag
from apps.roadmap import ladder
from apps.roadmap.models import ChapterLadderSession, Roadmap, StudentTopicMastery
from apps.users.models import CustomUser as User

_counter = [0]


def _uniq(prefix: str) -> str:
    _counter[0] += 1
    return f"{prefix}-{_counter[0]}"


def _make_question(tag: Tag, difficulty: int) -> Question:
    q = Question.objects.create(text=_uniq("q"), explanation="", difficulty=difficulty)
    q.tags.add(tag)
    AnswerOption.objects.create(question=q, text="correct", is_correct=True)
    for _ in range(3):
        AnswerOption.objects.create(question=q, text=_uniq("wrong"), is_correct=False)
    return q


def _add_topic(module: Module, *, rungs=(1, 2, 3), n_per=3, lessons=2) -> Tag:
    tag = Tag.objects.create(name=_uniq("tag"), slug=_uniq("tag"))
    for i in range(lessons):
        Lesson.objects.create(module=module, tag=tag, title=_uniq("L"), video_url="http://x", order=i)
    for d in rungs:
        for _ in range(n_per):
            _make_question(tag, d)
    return tag


def _make_module(**topic_kwargs) -> tuple[Module, Tag]:
    subject, _ = Subject.objects.get_or_create(
        slug="profile_math", defaults={"name": "Профильная математика"}
    )
    grade, _ = ClassGrade.objects.get_or_create(grade=11, subject=subject)
    module = Module.objects.create(
        title=_uniq("mod"), slug=_uniq("mod"), class_grade=grade
    )
    tag = _add_topic(module, **topic_kwargs)
    return module, tag


def _drive(student, module, outcomes):
    """Run the ladder to completion; return (session, served_difficulties)."""
    session = ladder.start_ladder(student, module)
    served = []
    i = 0
    while True:
        question = ladder.next_question(session)
        if question is None:
            break
        served.append(question.difficulty)
        option = question.options.filter(is_correct=(outcomes[i] == 1)).first()
        i += 1
        ladder.record_answer(session, question.id, option.id)
    return session, served


def _drive_moves(student, module, moves):
    """Like ``_drive`` but each move is ``1`` (correct), ``0`` (wrong), or the
    string ``"idk"`` ("I don't know"). Stops when the ladder resolves or ``moves``
    runs out; returns ``(session, served_difficulties)``."""
    session = ladder.start_ladder(student, module)
    served = []
    for move in moves:
        question = ladder.next_question(session)
        if question is None:
            break
        served.append(question.difficulty)
        if move == "idk":
            ladder.record_answer(session, question.id)
        else:
            option = question.options.filter(is_correct=(move == 1)).first()
            ladder.record_answer(session, question.id, option.id)
    return session, served


def _verdict(session, tag) -> str:
    return session.state["per_topic"][str(tag.id)]["verdict"]


def _topic_state(session, tag) -> dict:
    return session.state["per_topic"][str(tag.id)]


class LadderStateMachineTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email=_uniq("u") + "@x.io", password="x")

    @override_settings(LADDER_CONFIRM=False)
    def test_steps_up_on_correct_early_stop_two_questions(self):
        module, tag = _make_module()
        session, served = _drive(self.user, module, [1, 1])  # medium✓ → hard✓
        self.assertEqual(served, [2, 3])  # stepped up; resolved in 2
        self.assertEqual(_verdict(session, tag), "mastered")

    @override_settings(LADDER_CONFIRM=False)
    def test_steps_down_on_wrong(self):
        module, tag = _make_module()
        session, served = _drive(self.user, module, [0, 0])  # medium✗ → easy✗
        self.assertEqual(served, [2, 1])  # stepped down
        self.assertEqual(_verdict(session, tag), "gap")

    def test_mastered_when_hard_cleared(self):
        module, tag = _make_module()
        session, _ = _drive(self.user, module, [1, 1, 1])  # + confirm hard✓
        self.assertEqual(_verdict(session, tag), "mastered")

    def test_solid_when_medium_cleared_hard_missed(self):
        module, tag = _make_module()
        session, served = _drive(self.user, module, [1, 0])  # medium✓, hard✗ (deciding wrong)
        self.assertEqual(served, [2, 3])  # no confirm on a deciding wrong
        self.assertEqual(_verdict(session, tag), "solid")

    def test_gap_when_easy_missed(self):
        module, tag = _make_module()
        session, _ = _drive(self.user, module, [0, 0])
        self.assertEqual(_verdict(session, tag), "gap")

    def test_gap_when_only_easy_cleared(self):
        module, tag = _make_module()
        session, served = _drive(self.user, module, [0, 1])  # medium✗, easy✓ → gap, no confirm
        self.assertEqual(served, [2, 1])
        self.assertEqual(_verdict(session, tag), "gap")

    def test_asymmetric_confirm_deciding_correct_asks_second(self):
        module, tag = _make_module()
        session, served = _drive(self.user, module, [1, 1, 1])
        self.assertEqual(served, [2, 3, 3])  # deciding correct → a second hard question
        self.assertEqual(_verdict(session, tag), "mastered")

    def test_asymmetric_confirm_deciding_wrong_no_second(self):
        module, tag = _make_module()
        session, served = _drive(self.user, module, [1, 0])
        self.assertEqual(len(served), 2)  # deciding wrong → straight to verdict
        self.assertEqual(_verdict(session, tag), "solid")

    def test_confirm_no_second_question_accepts_single_and_logs(self):
        # Only one hard question exists → confirm cannot fire.
        module, tag = _make_module(rungs=(1, 2, 3), n_per=1)
        session, served = _drive(self.user, module, [1, 1])
        self.assertEqual(served, [2, 3])
        self.assertEqual(_verdict(session, tag), "mastered")
        self.assertTrue(_topic_state(session, tag)["degraded"])

    def test_confirm_downgrade_on_failed_second(self):
        module, tag = _make_module()
        session, _ = _drive(self.user, module, [1, 1, 0])  # confirm hard✗ → downgrade
        self.assertEqual(_verdict(session, tag), "solid")

    def test_missing_hard_caps_at_solid(self):
        module, tag = _make_module(rungs=(1, 2))
        session, _ = _drive(self.user, module, [1, 1])  # medium✓, confirm medium✓
        self.assertEqual(_verdict(session, tag), "solid")

    def test_single_rung_gate(self):
        module, tag = _make_module(rungs=(2,))
        session, served = _drive(self.user, module, [1])
        self.assertEqual(served, [2])
        self.assertEqual(_verdict(session, tag), "solid")
        self.assertTrue(_topic_state(session, tag)["degraded"])

        module2, tag2 = _make_module(rungs=(2,))
        session2, _ = _drive(self.user, module2, [0])
        self.assertEqual(_verdict(session2, tag2), "gap")

    def test_no_questions_topic_defaults_gap(self):
        module, tag = _make_module(rungs=())  # no questions at all
        session = ladder.start_ladder(self.user, module)
        self.assertTrue(session.is_complete)
        self.assertEqual(_verdict(session, tag), "gap")
        self.assertTrue(_topic_state(session, tag)["degraded"])

    def test_next_question_none_and_complete_when_resolved(self):
        module, tag = _make_module()
        session, _ = _drive(self.user, module, [1, 0])
        self.assertTrue(session.is_complete)
        self.assertIsNone(ladder.next_question(session))


class LadderDontKnowTests(APITestCase):
    """"I don't know" abstention — steps down like a wrong answer (the verdict
    falls out of the same rung machine) but leaves the student's theta untouched."""

    def setUp(self):
        self.user = User.objects.create_user(email=_uniq("u") + "@x.io", password="x")

    @override_settings(LADDER_CONFIRM=False)
    def test_idk_at_hard_accepts_solid(self):
        module, tag = _make_module()
        # medium✓ steps up to hard; "I don't know" there → accept the cleared level.
        session, served = _drive_moves(self.user, module, [1, "idk"])
        self.assertEqual(served, [2, 3])
        self.assertEqual(_verdict(session, tag), "solid")

    def test_idk_at_medium_serves_easy_next(self):
        module, tag = _make_module()
        # "I don't know" at the medium start rung steps down and poses easy.
        session, served = _drive_moves(self.user, module, ["idk", 1])
        self.assertEqual(served, [2, 1])

    def test_idk_at_easy_is_a_gap(self):
        module, tag = _make_module()
        # medium abstain → easy; abstain again at the bottom rung → gap.
        session, served = _drive_moves(self.user, module, ["idk", "idk"])
        self.assertEqual(served, [2, 1])
        self.assertEqual(_verdict(session, tag), "gap")

    @override_settings(LADDER_CONFIRM=False)
    def test_idk_does_not_move_theta(self):
        module, tag = _make_module()
        # medium✓ writes one mastery update; the hard "I don't know" writes none.
        _drive_moves(self.user, module, [1, "idk"])
        row = StudentTopicMastery.objects.get(student=self.user, tag=tag)
        self.assertEqual(row.n_observations, 1)

    def test_idk_records_an_answer_with_no_option(self):
        module, tag = _make_module()
        session, _ = _drive_moves(self.user, module, ["idk", "idk"])
        ans = session.attempt.answers.order_by("id").first()
        self.assertIsNone(ans.selected_option)
        self.assertFalse(ans.is_correct)


class LadderSkipOnPriorTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email=_uniq("u") + "@x.io", password="x")

    def _prior(self, tag, *, theta, n, days_ago):
        StudentTopicMastery.objects.create(
            student=self.user,
            tag=tag,
            theta=theta,
            n_observations=n,
            last_seen_at=timezone.now() - timedelta(days=days_ago),
        )

    def test_fresh_confident_prior_skips_topic(self):
        module, tag = _make_module()
        self._prior(tag, theta=1.5, n=6, days_ago=1)
        session, served = _drive(self.user, module, [])
        self.assertEqual(served, [])  # never asked
        self.assertEqual(_verdict(session, tag), "mastered")

    def test_stale_prior_asks_single_hard_probe(self):
        module, tag = _make_module()
        self._prior(tag, theta=1.5, n=6, days_ago=90)
        session, served = _drive(self.user, module, [1])  # hard probe✓
        self.assertEqual(served, [3])
        self.assertEqual(_verdict(session, tag), "mastered")

    def test_stale_prior_fail_falls_into_full_ladder(self):
        module, tag = _make_module()
        self._prior(tag, theta=1.5, n=6, days_ago=90)
        # hard probe✗ → full ladder from medium: medium✓, hard✓, confirm hard✓
        session, served = _drive(self.user, module, [0, 1, 1, 1])
        self.assertEqual(served, [3, 2, 3, 3])
        self.assertEqual(_verdict(session, tag), "mastered")

    def test_low_prior_is_not_skipped(self):
        module, tag = _make_module()
        self._prior(tag, theta=0.2, n=6, days_ago=1)  # below mastered bar
        session, served = _drive(self.user, module, [1, 1, 1])
        self.assertTrue(len(served) >= 2)  # full ladder ran


class LadderStorageAndMasteryTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email=_uniq("u") + "@x.io", password="x")

    def test_attempt_is_ladder_source_with_null_test_and_no_roadmap(self):
        module, tag = _make_module()
        session, _ = _drive(self.user, module, [1, 1, 1])
        attempt = session.attempt
        self.assertEqual(attempt.source, "ladder")
        self.assertIsNone(attempt.test_id)
        self.assertTrue(attempt.is_completed)
        # The global roadmap hook must no-op for ladder attempts.
        self.assertEqual(Roadmap.objects.filter(student=self.user).count(), 0)

    def test_each_answer_writes_a_mastery_update(self):
        module, tag = _make_module()
        _drive(self.user, module, [1, 1, 1])  # 3 answers
        row = StudentTopicMastery.objects.get(student=self.user, tag=tag)
        self.assertEqual(row.n_observations, 3)
        self.assertGreater(row.theta, 0.0)  # three correct → ability rose
        self.assertIsNotNone(row.last_seen_at)

    def test_answers_recorded_as_attempt_answers(self):
        module, tag = _make_module()
        session, _ = _drive(self.user, module, [1, 0])
        self.assertEqual(session.attempt.answers.count(), 2)


class LadderPlanTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email=_uniq("u") + "@x.io", password="x")

    def test_soft_fail_gap_gets_lessons_solid_sibling_does_not(self):
        subject, _ = Subject.objects.get_or_create(
            slug="profile_math", defaults={"name": "Профильная математика"}
        )
        grade, _ = ClassGrade.objects.get_or_create(grade=11, subject=subject)
        module = Module.objects.create(
            title=_uniq("mod"), slug=_uniq("mod"), class_grade=grade
        )
        gap_tag = _add_topic(module)     # first topic
        solid_tag = _add_topic(module)   # second topic
        # Topic 1 → gap (medium✗, easy✗); topic 2 → solid (medium✓, hard✗).
        session, _ = _drive(self.user, module, [0, 0, 1, 0])
        self.assertEqual(_verdict(session, gap_tag), "gap")
        self.assertEqual(_verdict(session, solid_tag), "solid")

        plan = ladder.chapter_plan(session)
        by_tag = {t["tag_id"]: t for t in plan["topics"]}
        self.assertTrue(by_tag[gap_tag.id]["lessons"])          # gap → its own lessons
        self.assertEqual(by_tag[solid_tag.id]["lessons"], [])   # solid sibling → none
        self.assertEqual(by_tag[gap_tag.id]["hard_question_ids"], [])

    def test_mastered_plan_offers_hard_questions(self):
        module, tag = _make_module()
        session, _ = _drive(self.user, module, [1, 1, 1])
        plan = ladder.chapter_plan(session)
        entry = plan["topics"][0]
        self.assertEqual(entry["verdict"], "mastered")
        self.assertTrue(entry["hard_question_ids"])
        self.assertEqual(entry["lessons"], [])


class LadderAnalyticsTests(APITestCase):
    def test_verdict_distribution_counts_completed_sessions(self):
        module, tag = _make_module()
        for outcomes in ([1, 1, 1], [1, 0], [0, 0]):  # mastered, solid, gap
            u = User.objects.create_user(email=_uniq("u") + "@x.io", password="x")
            _drive(u, module, outcomes)

        dist = compute_ladder_verdict_distribution(module=module)
        self.assertEqual(len(dist), 1)
        topic = dist[0]["topics"][0]
        self.assertEqual(topic["counts"], {"gap": 1, "solid": 1, "mastered": 1})
        self.assertEqual(topic["total"], 3)


class LadderEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email=_uniq("u") + "@x.io", password="x")
        self.client.force_authenticate(user=self.user)

    def _start_url(self, module):
        return f"/api/v1/roadmap/chapter/{module.id}/ladder/start/"

    NEXT_URL = "/api/v1/roadmap/chapter/ladder/next/"

    def test_start_then_answer_to_completion(self):
        module, tag = _make_module()
        resp = self.client.post(self._start_url(module))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["is_complete"])
        self.assertIsNotNone(data["question"])
        # No correctness leaked in the option payload.
        self.assertNotIn("is_correct", data["question"]["options"][0])

        session_id = data["session_id"]
        # Answer until the plan comes back.
        guard = 0
        while not data["is_complete"] and guard < 10:
            guard += 1
            question = data["question"]
            q = Question.objects.get(pk=question["id"])
            option = q.options.filter(is_correct=True).first()  # always answer correctly
            resp = self.client.post(
                self.NEXT_URL,
                {"session_id": session_id, "question_id": q.id, "option_id": option.id},
                format="json",
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()

        self.assertTrue(data["is_complete"])
        self.assertIsNone(data["question"])
        self.assertIsNotNone(data["plan"])
        self.assertEqual(data["plan"]["module_id"], module.id)

    @override_settings(CHAPTER_LADDER_ENABLED=False)
    def test_start_disabled_returns_409(self):
        module, tag = _make_module()
        resp = self.client.post(self._start_url(module))
        self.assertEqual(resp.status_code, 409)

    def test_next_rejects_wrong_rung_question(self):
        module, tag = _make_module()
        resp = self.client.post(self._start_url(module)).json()
        # An easy (d1) question is not the expected medium start rung.
        easy_q = Question.objects.filter(tags=tag, difficulty=1).first()
        option = easy_q.options.filter(is_correct=True).first()
        resp2 = self.client.post(
            self.NEXT_URL,
            {"session_id": resp["session_id"], "question_id": easy_q.id, "option_id": option.id},
            format="json",
        )
        self.assertEqual(resp2.status_code, 400)
