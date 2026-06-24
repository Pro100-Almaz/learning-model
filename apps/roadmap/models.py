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
