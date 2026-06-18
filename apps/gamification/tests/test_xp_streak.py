"""Tests for the gamification engine (T-203).

Covers:
  - XP amounts honor settings.XP_RULES
  - level transitions follow settings.LEVELS
  - streak increments once per day; gap resets to 1
  - active_today reflects today's activity
  - /gamification/me/ endpoint shape & happy path
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.gamification import services
from apps.gamification.models import Streak, StudentProgress, XPEvent


def _make_user(email="learner@example.com"):
    return get_user_model().objects.create_user(
        email=email,
        password="testpassword123",
        first_name="Learn",
        last_name="Er",
    )


class LevelTests(APITestCase):
    def test_level_for_zero_xp_is_novice(self):
        self.assertEqual(services.level_for_xp(0), ("novice", "Новичок"))

    def test_level_thresholds_match_settings(self):
        self.assertEqual(services.level_for_xp(999)[0], "novice")
        self.assertEqual(services.level_for_xp(1000)[0], "znatok")
        self.assertEqual(services.level_for_xp(4999)[0], "znatok")
        self.assertEqual(services.level_for_xp(5000)[0], "geniy")
        self.assertEqual(services.level_for_xp(1_000_000)[0], "geniy")

    def test_xp_to_next_level(self):
        user = _make_user()
        progress = services.get_or_create_progress(user)
        progress.total_xp = 0
        progress.save()
        self.assertEqual(services.compute_xp_to_next_level(progress), 1000)

        progress.total_xp = 1500
        progress.save()
        self.assertEqual(services.compute_xp_to_next_level(progress), 3500)

        progress.total_xp = 5000
        progress.save()
        self.assertEqual(services.compute_xp_to_next_level(progress), 0)


class AwardXPTests(APITestCase):
    def setUp(self):
        self.user = _make_user("xp@example.com")

    def test_award_xp_correct_answer_amount_matches_rules(self):
        awarded = services.award_xp(self.user, "correct_answer")
        self.assertEqual(awarded, settings.XP_RULES["correct_answer"])

        progress = StudentProgress.objects.get(student=self.user)
        self.assertEqual(progress.total_xp, settings.XP_RULES["correct_answer"])
        self.assertEqual(progress.level_code, "novice")

        self.assertEqual(
            XPEvent.objects.filter(student=self.user, reason="correct_answer").count(),
            1,
        )

    def test_award_xp_video_amount_matches_rules(self):
        awarded = services.award_xp(self.user, "video")
        self.assertEqual(awarded, settings.XP_RULES["video"])

        progress = StudentProgress.objects.get(student=self.user)
        self.assertEqual(progress.total_xp, settings.XP_RULES["video"])

    def test_award_xp_unknown_reason_no_op(self):
        awarded = services.award_xp(self.user, "totally-unknown")
        self.assertEqual(awarded, 0)
        self.assertFalse(XPEvent.objects.filter(student=self.user).exists())
        # Streak should not move either when no XP was awarded.
        self.assertFalse(Streak.objects.filter(student=self.user).exists())

    def test_award_xp_promotes_level(self):
        # 200 correct answers at 5xp = 1000xp -> znatok
        for _ in range(200):
            services.award_xp(self.user, "correct_answer")
        progress = StudentProgress.objects.get(student=self.user)
        self.assertEqual(progress.total_xp, 1000)
        self.assertEqual(progress.level_code, "znatok")

    def test_award_xp_also_updates_streak(self):
        services.award_xp(self.user, "correct_answer")
        streak = Streak.objects.get(student=self.user)
        self.assertEqual(streak.current_streak, 1)
        self.assertIsNotNone(streak.last_active_date)


class StreakTests(APITestCase):
    def setUp(self):
        self.user = _make_user("streak@example.com")

    def test_first_activity_sets_streak_to_one(self):
        streak = services.update_streak(self.user)
        self.assertEqual(streak.current_streak, 1)
        self.assertEqual(streak.longest_streak, 1)

    def test_same_day_does_not_increment(self):
        services.update_streak(self.user)
        services.update_streak(self.user)
        streak = Streak.objects.get(student=self.user)
        self.assertEqual(streak.current_streak, 1)

    def test_consecutive_day_increments(self):
        # Day 1
        services.update_streak(self.user)
        s = Streak.objects.get(student=self.user)
        # Rewind to yesterday to simulate consecutive-day activity.
        today = timezone.localdate()
        s.last_active_date = today - timedelta(days=1)
        s.current_streak = 1
        s.longest_streak = 1
        s.save()

        services.update_streak(self.user)
        s.refresh_from_db()
        self.assertEqual(s.current_streak, 2)
        self.assertEqual(s.longest_streak, 2)

    def test_gap_resets_streak(self):
        # Seed a 5-day streak that ended 3 days ago.
        today = timezone.localdate()
        Streak.objects.create(
            student=self.user,
            current_streak=5,
            longest_streak=5,
            last_active_date=today - timedelta(days=3),
        )
        services.update_streak(self.user)
        s = Streak.objects.get(student=self.user)
        self.assertEqual(s.current_streak, 1)
        # Longest preserved.
        self.assertEqual(s.longest_streak, 5)


class GamificationMeEndpointTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.url = reverse("v1:gamification:me")
        cls.user = _make_user("me@example.com")

    def test_requires_auth(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_default_shape_when_no_activity(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        self.assertEqual(
            set(data.keys()),
            {"total_xp", "level_code", "level_label", "xp_to_next_level", "streak"},
        )
        self.assertEqual(data["total_xp"], 0)
        self.assertEqual(data["level_code"], "novice")
        self.assertEqual(data["level_label"], "Новичок")
        self.assertEqual(data["xp_to_next_level"], 1000)

        self.assertEqual(set(data["streak"].keys()), {"current", "longest", "active_today"})
        self.assertEqual(data["streak"]["current"], 0)
        self.assertEqual(data["streak"]["longest"], 0)
        self.assertFalse(data["streak"]["active_today"])

    def test_reflects_awarded_xp_and_active_streak(self):
        services.award_xp(self.user, "correct_answer")
        services.award_xp(self.user, "video")

        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_total = settings.XP_RULES["correct_answer"] + settings.XP_RULES["video"]
        self.assertEqual(response.data["total_xp"], expected_total)
        self.assertTrue(response.data["streak"]["active_today"])
        self.assertEqual(response.data["streak"]["current"], 1)

    def test_active_today_false_when_last_active_was_yesterday(self):
        Streak.objects.create(
            student=self.user,
            current_streak=3,
            longest_streak=4,
            last_active_date=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["streak"]["active_today"])
        self.assertEqual(response.data["streak"]["current"], 3)
        self.assertEqual(response.data["streak"]["longest"], 4)
