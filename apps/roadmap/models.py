"""Roadmap models.

A ``Roadmap`` is a persisted, ordered study plan for one student. It's
generated from a diagnostic ``TestAttempt`` (or a mock fallback) by
:mod:`apps.roadmap.services`. Each ``RoadmapItem`` points at a Lesson and
its optional follow-up micro-test, with a tracked status so the UI can
show progress.

Only ONE roadmap per student is ``is_active=True`` at a time;
regeneration deactivates the prior one rather than mutating items.
"""

from __future__ import annotations

import math

from django.conf import settings
from django.db import models


class Roadmap(models.Model):
    SOURCE_CHOICES = [
        ("diagnostic", "Diagnostic"),
        ("mock_recompute", "Mock recompute"),
        ("manual", "Manual"),
    ]

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="roadmaps",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default="diagnostic",
    )
    source_attempt = models.ForeignKey(
        "assessments.TestAttempt",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_roadmaps",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["student", "is_active"]),
        ]

    def __str__(self) -> str:
        flag = "active" if self.is_active else "archived"
        return f"Roadmap<{self.pk}> student={self.student_id} [{flag}]"


class RoadmapItem(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("in_progress", "In progress"),
        ("completed", "Completed"),
    ]

    roadmap = models.ForeignKey(
        Roadmap,
        on_delete=models.CASCADE,
        related_name="items",
    )
    order = models.PositiveIntegerField()
    lesson = models.ForeignKey(
        "content.Lesson",
        on_delete=models.CASCADE,
        related_name="roadmap_items",
    )
    micro_test = models.ForeignKey(
        "assessments.Test",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="roadmap_items",
    )
    weak_tag = models.ForeignKey(
        "content.Tag",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="roadmap_items",
    )
    rationale = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order"]
        unique_together = ("roadmap", "order")
        indexes = [
            models.Index(fields=["roadmap", "status"]),
        ]

    def __str__(self) -> str:
        return f"Item<{self.pk}> rm={self.roadmap_id} #{self.order} lesson={self.lesson_id}"


class StudentTopicMastery(models.Model):
    """Persistent per-topic student model — one latent ability per (student, tag).

    The scoped slice of ``06_Roadmap_Engine_Spec.md`` Phase 2 that the Chapter
    Ladder (``07_Chapter_Ladder_Spec.md``) depends on. ``theta`` is the student's
    ability on this topic on the logit scale, refined by a difficulty-weighted
    Elo update on every observed answer (see :mod:`apps.roadmap.mastery`); the
    ladder is the first writer of these rows for most students. ``n_observations``
    is the confidence behind ``theta`` (it damps the learning rate over time).
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="topic_mastery",
    )
    tag = models.ForeignKey(
        "content.Tag",
        on_delete=models.CASCADE,
        related_name="student_mastery",
    )
    theta = models.FloatField(default=0.0)
    n_observations = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "tag")
        indexes = [models.Index(fields=["student", "tag"])]

    @property
    def p_mastery(self) -> float:
        """Ability as a 0..1 probability, for the UI and the planner thresholds."""
        return 1 / (1 + math.exp(-self.theta))

    @property
    def progress(self) -> int:
        return 100 if self.theta >= 1 else 0

    def __str__(self) -> str:
        return (
            f"Mastery<{self.pk}> student={self.student_id} tag={self.tag_id} "
            f"θ={self.theta:.2f} n={self.n_observations}"
        )


class ChapterLadderSession(models.Model):
    """One in-progress chapter placement for a (student, module).

    Server-driven so the next rung is chosen on the server and the client can't
    see the ladder logic. ``state`` holds the per-topic ladder machine; the
    answers themselves are ordinary ``assessments.AttemptAnswer`` rows hanging off
    ``attempt`` (a ``source="ladder"`` ``TestAttempt`` with no Test), so they stay
    first-class for analytics and "questions answered" counts.

    ``state`` shape::

        {
          "topics": [tag_id, ...],            # curriculum order
          "per_topic": {
            "<tag_id>": {
              "phase": "probe|confirm|gate|stale_probe|done",
              "rung": int | None,             # difficulty to ask next
              "avail": [int, ...],            # rungs with questions for this tag
              "asked": [question_id, ...],
              "cleared": [difficulty, ...],   # rungs answered correctly (probe)
              "stepped": bool,                # took the one post-start step yet
              "pending": "solid|mastered"|None,   # verdict awaiting confirm
              "prior_theta": float,           # for stale_probe skip verdict
              "degraded": bool,               # bank couldn't form a full ladder
              "verdict": "gap|solid|mastered"|None,
            }
          }
        }
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ladder_sessions",
    )
    module = models.ForeignKey(
        "content.Module",
        on_delete=models.CASCADE,
        related_name="ladder_sessions",
    )
    attempt = models.OneToOneField(
        "assessments.TestAttempt",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ladder_session",
    )
    state = models.JSONField(default=dict)
    is_complete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["student", "module"])]

    def __str__(self) -> str:
        flag = "complete" if self.is_complete else "in-progress"
        return f"Ladder<{self.pk}> student={self.student_id} module={self.module_id} [{flag}]"
