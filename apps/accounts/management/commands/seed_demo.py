"""Create a reproducible demo student account for the walkthrough.

Usage::

    python manage.py seed_demo

Idempotent: every run leaves the database in the same shape — the demo
user, profile, expected scores, and one completed mock attempt with a
deliberate weakness in Тригонометрия (~30% correct vs ~70% on other
tags). Re-runs wipe the demo user's previous attempts and rebuild them.

Prereq: ``python manage.py seed`` must have been run first so the mock
Test (pk=1) and its questions exist.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import ExpectedScore, StudentProfile
from apps.assessments.models import (
    AnswerOption,
    AttemptAnswer,
    Question,
    Test,
    TestAttempt,
)
from apps.careers.models import Specialty, University
from apps.content.models import Subject


DEMO_EMAIL = "demo@ent.kz"
DEMO_PASSWORD = "demo-pass-1234"  # noqa: S105 — demo fixture, not a secret

TARGET_UNIVERSITY_CODE = "ENU"          # ЕНУ им. Гумилёва
TARGET_SPECIALTY_CODE = "6B061"         # Software Engineering
TARGET_SCORE = 115

EXPECTED_SCORES: tuple[tuple[str, int], ...] = (
    ("history-of-kazakhstan", 15),
    ("reading-literacy", 15),
    ("math-literacy", 12),
)

# The mock test seeded by the content fixture.
MOCK_TEST_PK = 1
# The diagnostic test seeded by the content fixture (drives the roadmap).
DIAGNOSTIC_TEST_PK = 2

# Tag slug that we deliberately make weak (~30% correct).
WEAK_TAG_SLUG = "trigonometry"

# Target correctness ratios.
WEAK_CORRECT_RATIO = 1 / 3   # one of every three trig questions right
STRONG_CORRECT_RATIO = 0.7   # ~70% on everything else


class Command(BaseCommand):
    help = "Create or refresh the demo student account (T-403)."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        User = get_user_model()

        # --- demo user ---------------------------------------------------
        user, created = User.objects.get_or_create(
            email=DEMO_EMAIL,
            defaults={"is_active": True},
        )
        # Always reset the password so the demo password is known.
        user.set_password(DEMO_PASSWORD)
        # Some custom user models keep ``first_name`` etc.; set safely.
        if hasattr(user, "first_name") and not user.first_name:
            user.first_name = "Demo"
        if hasattr(user, "last_name") and not user.last_name:
            user.last_name = "Student"
        user.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Reused'} demo user {user.email}"
            )
        )

        # --- targets -----------------------------------------------------
        try:
            university = University.objects.get(code=TARGET_UNIVERSITY_CODE)
        except University.DoesNotExist as exc:
            raise CommandError(
                f"University {TARGET_UNIVERSITY_CODE!r} missing — run `seed` first."
            ) from exc
        try:
            specialty = Specialty.objects.get(
                university=university,
                code=TARGET_SPECIALTY_CODE,
            )
        except Specialty.DoesNotExist as exc:
            raise CommandError(
                f"Specialty {TARGET_SPECIALTY_CODE!r} missing — run `seed` first."
            ) from exc

        # --- profile -----------------------------------------------------
        profile, _ = StudentProfile.objects.get_or_create(user=user)
        profile.target_university = university
        profile.target_specialty = specialty
        profile.target_score = TARGET_SCORE
        profile.onboarding_completed = True
        profile.save()
        self.stdout.write(self.style.SUCCESS("Profile set with target ЕНУ / SE."))

        # --- expected scores (upsert) -----------------------------------
        for subject_slug, score in EXPECTED_SCORES:
            try:
                subject = Subject.objects.get(slug=subject_slug)
            except Subject.DoesNotExist as exc:
                raise CommandError(
                    f"Subject {subject_slug!r} missing — run `migrate`/`seed` first."
                ) from exc
            ExpectedScore.objects.update_or_create(
                profile=profile,
                subject=subject,
                defaults={"score": score},
            )
        self.stdout.write(
            self.style.SUCCESS(f"Upserted {len(EXPECTED_SCORES)} expected scores.")
        )

        # --- mock attempt ------------------------------------------------
        try:
            mock_test = Test.objects.get(pk=MOCK_TEST_PK)
        except Test.DoesNotExist as exc:
            raise CommandError(
                "Mock test (pk=1) missing — run `seed` first."
            ) from exc

        # Wipe any existing attempts by this user on this test so we
        # start from a clean state. ``AttemptAnswer`` cascades.
        TestAttempt.objects.filter(student=user, test=mock_test).delete()

        attempt = TestAttempt.objects.create(
            student=user,
            test=mock_test,
            is_completed=True,
            finished_at=timezone.now(),
        )

        questions = list(
            mock_test.questions.all().prefetch_related("tags", "options")
        )
        correct_count = 0
        for idx, question in enumerate(questions):
            is_weak = question.tags.filter(slug=WEAK_TAG_SLUG).exists()
            # Use index modulo to deterministically produce ratios.
            if is_weak:
                # 1-of-3 correct, deterministic by position.
                should_be_correct = (idx % 3 == 0)
            else:
                # ~70%: 7 correct out of every 10 by position.
                should_be_correct = ((idx * 7) % 10) < 7

            correct_option = question.options.filter(is_correct=True).first()
            incorrect_option = question.options.filter(is_correct=False).first()
            if correct_option is None:
                # Skip malformed questions instead of crashing the demo.
                continue

            selected = correct_option if should_be_correct else (
                incorrect_option or correct_option
            )
            actually_correct = selected.is_correct
            if actually_correct:
                correct_count += 1

            AttemptAnswer.objects.create(
                attempt=attempt,
                question=question,
                selected_option=selected,
                is_correct=actually_correct,
            )

        total = len(questions)
        attempt.score = (correct_count / total * 100.0) if total else 0.0
        attempt.save(update_fields=["score"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Mock attempt: {correct_count}/{total} correct "
                f"(score={attempt.score:.1f})."
            )
        )

        # --- diagnostic attempt + roadmap -------------------------------
        diag_test = Test.objects.filter(pk=DIAGNOSTIC_TEST_PK).first()
        if diag_test is None:
            self.stdout.write(
                self.style.WARNING(
                    "Diagnostic test (pk=2) missing — skipping roadmap seed. "
                    "Run `seed` after pulling the latest fixtures."
                )
            )
        else:
            TestAttempt.objects.filter(student=user, test=diag_test).delete()
            diag_attempt = TestAttempt.objects.create(
                student=user,
                test=diag_test,
                is_completed=True,
                finished_at=timezone.now(),
            )
            diag_questions = list(
                diag_test.questions.all().prefetch_related("tags", "options")
            )
            diag_correct = 0
            for idx, question in enumerate(diag_questions):
                is_weak = question.tags.filter(slug=WEAK_TAG_SLUG).exists()
                should_be_correct = (
                    (idx % 3 == 0) if is_weak else (((idx * 7) % 10) < 7)
                )
                correct_option = question.options.filter(is_correct=True).first()
                incorrect_option = question.options.filter(is_correct=False).first()
                if correct_option is None:
                    continue
                selected = correct_option if should_be_correct else (
                    incorrect_option or correct_option
                )
                if selected.is_correct:
                    diag_correct += 1
                AttemptAnswer.objects.create(
                    attempt=diag_attempt,
                    question=question,
                    selected_option=selected,
                    is_correct=selected.is_correct,
                )
            diag_total = len(diag_questions)
            diag_attempt.score = (
                (diag_correct / diag_total * 100.0) if diag_total else 0.0
            )
            diag_attempt.save(update_fields=["score"])

            # Generate the roadmap from this diagnostic attempt.
            from apps.roadmap.services import generate_roadmap_for_student

            roadmap = generate_roadmap_for_student(
                user, source_attempt=diag_attempt, source="diagnostic"
            )
            item_count = roadmap.items.count() if roadmap else 0
            self.stdout.write(
                self.style.SUCCESS(
                    f"Diagnostic: {diag_correct}/{diag_total} "
                    f"(score={diag_attempt.score:.1f}) → roadmap "
                    f"with {item_count} items."
                )
            )

        self.stdout.write(self.style.SUCCESS("Demo account ready."))
        self.stdout.write(f"  email:    {DEMO_EMAIL}")
        self.stdout.write(f"  password: {DEMO_PASSWORD}")
