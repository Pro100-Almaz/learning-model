"""Persisted state for the MAIQE question-generation pipeline.

A ``GenerationJob`` is one batch request (topic + count). Each job streams
the MAIQE graph and writes a ``GenerationStep`` for every node execution
(Architect, Storyteller, Critic — possibly multiple rounds, Publisher).

Steps survive after the job ends so the admin / SSE replay can reconstruct
the timeline of how a question was built. The compact ``data`` JSON keeps
node payloads small enough to ship over Server-Sent Events without
ballooning the wire.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class GenerationJob(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_PARTIAL, "Partial (some questions failed)"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]
    TERMINAL_STATUSES = (
        STATUS_SUCCEEDED,
        STATUS_PARTIAL,
        STATUS_FAILED,
        STATUS_CANCELLED,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_jobs",
    )
    topic = models.CharField(max_length=80)
    count = models.PositiveIntegerField()
    target_score = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    celery_task_id = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def __str__(self) -> str:
        return f"Job<{self.pk}> {self.topic} x{self.count} [{self.status}]"


class GenerationStep(models.Model):
    KIND_ARCHITECT = "architect"
    KIND_STORYTELLER = "storyteller"
    KIND_CRITIC = "critic"
    KIND_PUBLISHER = "publisher"
    KIND_ERROR = "error"

    KIND_CHOICES = [
        (KIND_ARCHITECT, "Architect"),
        (KIND_STORYTELLER, "Storyteller"),
        (KIND_CRITIC, "Critic"),
        (KIND_PUBLISHER, "Publisher"),
        (KIND_ERROR, "Error"),
    ]

    STATUS_STARTED = "started"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_STARTED, "Started"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    ]

    job = models.ForeignKey(
        GenerationJob,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    question_index = models.PositiveIntegerField()
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_SUCCEEDED,
    )
    message = models.CharField(max_length=300, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    question = models.ForeignKey(
        "assessments.Question",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generation_steps",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["job", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Step<{self.pk}> job={self.job_id} #{self.question_index} {self.kind}"
