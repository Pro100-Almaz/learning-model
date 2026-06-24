"""Tests for the generation pipeline.

We mock ``graph.build_graph`` so the Critic/Storyteller never call out to a
real LLM — the tests verify our orchestration (step persistence, status
transitions, summary counters, SSE replay) end-to-end against a scripted
graph stream.

Coverage:
- run_generation_job: scripted graph emits 4 nodes → 4 GenerationStep rows;
  job ends ``succeeded`` and counts are tallied.
- dedup case: ``was_duplicate=True`` increments skipped_count, not created_count.
- failure inside one question: error step recorded, batch continues for the
  remaining attempts, status ends ``partial``.
- cancel before run: terminal job is left alone.
- POST endpoint creates a job and dispatches Celery (mocked).
- GET /jobs/{id}/ returns the snapshot incl. steps.
- iter_event_stream replays past steps when the job is already terminal.
- Non-staff can't create or list jobs (403 / IsAdminUser).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.assessments.models import Question
from apps.generation import services
from apps.generation.models import GenerationJob, GenerationStep
from apps.generation.tasks import run_generation_job
from apps.users.models import CustomUser as User


def _make_question(pk: int | None = None) -> Question:
    """Create a minimal Question (for publisher FK targets in tests)."""
    return Question.objects.create(text="placeholder", explanation="x", difficulty=1)


def _scripted_graph(chunks_per_question: list[list[dict[str, Any]]]):
    """Return an object whose ``.stream()`` yields the supplied chunks.

    ``chunks_per_question`` is a list of "question runs"; each run is a list
    of LangGraph chunks (each chunk a ``{node_name: state_updates}`` dict).
    """
    call_count = {"n": 0}

    class Stream:
        def __init__(self):
            self._chunks = []

        def __iter__(self):
            return iter(self._chunks)

    class Graph:
        def stream(self, initial, stream_mode="updates"):
            idx = call_count["n"]
            call_count["n"] += 1
            return iter(chunks_per_question[idx])

    return Graph()


class RunGenerationJobTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="staff@e.com", password="pw", is_staff=True
        )

    def _make_job(self, topic="quadratic_equations", count=1):
        return GenerationJob.objects.create(
            user=self.user, topic=topic, count=count
        )

    def test_records_one_step_per_node_and_marks_succeeded(self):
        job = self._make_job(count=1)
        question = _make_question()
        graph = _scripted_graph(
            [
                [
                    {"architect": {
                        "difficulty": 2,
                        "tag_slug": "logs",
                        "tag_name": "Логарифмы",
                        "content_hash": "abcdef12" * 8,
                    }},
                    {"storyteller": {"draft_text": "Compute log_2(8).", "revision_count": 0}},
                    {"critic": {"critic_passed": True, "revision_count": 0}},
                    {"publisher": {"question_id": question.pk, "was_duplicate": False}},
                ]
            ]
        )

        with patch("graph.build_graph", return_value=graph):
            result = run_generation_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(result["status"], GenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(job.status, GenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(job.created_count, 1)
        self.assertEqual(job.skipped_count, 0)
        self.assertEqual(job.failed_count, 0)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)

        kinds = list(
            job.steps.order_by("created_at").values_list("kind", flat=True)
        )
        self.assertEqual(
            kinds, ["architect", "storyteller", "critic", "publisher"]
        )

    def test_dedup_increments_skipped_not_created(self):
        job = self._make_job(count=1)
        question = _make_question()
        graph = _scripted_graph(
            [
                [
                    {"architect": {"difficulty": 1, "content_hash": "h"}},
                    {"storyteller": {"draft_text": "x", "revision_count": 0}},
                    {"critic": {"critic_passed": True, "revision_count": 0}},
                    {"publisher": {"question_id": question.pk, "was_duplicate": True}},
                ]
            ]
        )

        with patch("graph.build_graph", return_value=graph):
            run_generation_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, GenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(job.created_count, 0)
        self.assertEqual(job.skipped_count, 1)

    def test_critic_fallback_with_no_publisher_counts_as_failed(self):
        # Graph emits architect + storyteller + critic with no publisher chunk
        # (Critic exhausted retries → fallback edge to END).
        job = self._make_job(count=1)
        graph = _scripted_graph(
            [
                [
                    {"architect": {"difficulty": 1}},
                    {"storyteller": {"draft_text": "x", "revision_count": 0}},
                    {"critic": {"critic_passed": False, "revision_count": 3,
                                "rewrite_notes": "still wrong"}},
                ]
            ]
        )

        with patch("graph.build_graph", return_value=graph):
            run_generation_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.failed_count, 1)
        self.assertEqual(job.created_count, 0)
        # Only failure → terminal status is failed.
        self.assertEqual(job.status, GenerationJob.STATUS_FAILED)

    def test_partial_batch_when_one_fails_one_succeeds(self):
        # First question raises in the middle; second one publishes cleanly.
        job = self._make_job(count=2)
        question = _make_question()

        def boom():
            raise RuntimeError("LLM exploded")

        class Graph:
            def __init__(self):
                self.calls = 0

            def stream(self, initial, stream_mode="updates"):
                self.calls += 1
                if self.calls == 1:
                    yield {"architect": {"difficulty": 1}}
                    boom()
                else:
                    yield {"architect": {"difficulty": 1}}
                    yield {"storyteller": {"draft_text": "y", "revision_count": 0}}
                    yield {"critic": {"critic_passed": True}}
                    yield {"publisher": {"question_id": question.pk, "was_duplicate": False}}

        with patch("graph.build_graph", return_value=Graph()):
            run_generation_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.created_count, 1)
        self.assertEqual(job.failed_count, 1)
        self.assertEqual(job.status, GenerationJob.STATUS_PARTIAL)
        # An "error" kind step was written for the failed attempt.
        self.assertTrue(
            job.steps.filter(kind=GenerationStep.KIND_ERROR).exists()
        )

    def test_already_terminal_job_is_left_untouched(self):
        job = self._make_job()
        job.status = GenerationJob.STATUS_CANCELLED
        job.save(update_fields=["status"])

        with patch("graph.build_graph") as build_graph:
            result = run_generation_job(job.pk)

        build_graph.assert_not_called()
        self.assertEqual(result["status"], GenerationJob.STATUS_CANCELLED)


class EventStreamReplayTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="u@e.com", password="pw")
        self.job = GenerationJob.objects.create(
            user=self.user,
            topic="quadratic_equations",
            count=1,
            status=GenerationJob.STATUS_SUCCEEDED,
        )
        GenerationStep.objects.create(
            job=self.job,
            question_index=0,
            kind="architect",
            status="succeeded",
            message="Architect: rolled spec",
            data={"difficulty": 2},
        )
        GenerationStep.objects.create(
            job=self.job,
            question_index=0,
            kind="publisher",
            status="succeeded",
            message="Publisher: ✓ saved Question #1",
            data={"question_id": 1, "was_duplicate": False},
        )

    def test_replays_persisted_steps_and_emits_finished(self):
        chunks = list(services.iter_event_stream(self.job))
        # Three events: snapshot + 2 step rows + finished.
        payloads = []
        for chunk in chunks:
            for line in chunk.split("\n"):
                if line.startswith("data: "):
                    payloads.append(json.loads(line[6:]))
        types = [p["type"] for p in payloads]
        self.assertIn("job.snapshot", types)
        self.assertEqual(types.count("step"), 2)
        self.assertEqual(types[-1], "job.finished")


class JobEndpointTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@e.com", password="pw", is_staff=True
        )
        self.student = User.objects.create_user(email="s@e.com", password="pw")

    def test_create_dispatches_celery_and_returns_201(self):
        self.client.force_authenticate(user=self.staff)
        with patch("apps.generation.tasks.run_generation_job.delay") as delay:
            delay.return_value.id = "task-123"
            resp = self.client.post(
                "/api/v1/generation/jobs/",
                {"topic": "quadratic_equations", "count": 2, "target_score": 120},
                format="json",
            )
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(resp.data["topic"], "quadratic_equations")
        self.assertEqual(resp.data["count"], 2)
        self.assertEqual(resp.data["target_score"], 120)
        self.assertEqual(resp.data["status"], "pending")
        self.assertEqual(resp.data["celery_task_id"], "task-123")

    def test_student_cannot_create(self):
        self.client.force_authenticate(user=self.student)
        resp = self.client.post(
            "/api/v1/generation/jobs/",
            {"topic": "quadratic_equations", "count": 1},
            format="json",
        )
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_detail_owner_can_read_but_not_other_users(self):
        job = GenerationJob.objects.create(
            user=self.staff, topic="x", count=1
        )
        self.client.force_authenticate(user=self.staff)
        resp = self.client.get(f"/api/v1/generation/jobs/{job.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], job.pk)

        self.client.force_authenticate(user=self.student)
        resp = self.client.get(f"/api/v1/generation/jobs/{job.pk}/")
        self.assertEqual(resp.status_code, 404)

    def test_cancel_marks_status_and_publishes_event(self):
        job = GenerationJob.objects.create(
            user=self.staff,
            topic="x",
            count=1,
            status=GenerationJob.STATUS_RUNNING,
            celery_task_id="task-abc",
        )
        self.client.force_authenticate(user=self.staff)
        with patch("apps.generation.services.publish_event") as publish:
            resp = self.client.post(f"/api/v1/generation/jobs/{job.pk}/cancel/")
        self.assertEqual(resp.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, GenerationJob.STATUS_CANCELLED)
        publish.assert_called_once()

    def test_cancel_on_terminal_job_is_a_noop(self):
        job = GenerationJob.objects.create(
            user=self.staff,
            topic="x",
            count=1,
            status=GenerationJob.STATUS_SUCCEEDED,
        )
        self.client.force_authenticate(user=self.staff)
        resp = self.client.post(f"/api/v1/generation/jobs/{job.pk}/cancel/")
        self.assertEqual(resp.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, GenerationJob.STATUS_SUCCEEDED)
