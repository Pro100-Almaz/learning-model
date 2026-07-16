"""Celery task that runs the MAIQE graph and streams per-node progress.

Heavy imports (LangGraph, the LLM transport, the prompts module) live INSIDE
the task body so the Django web process never pays for them at import time.
The Celery worker container, on the other hand, evaluates them once per
process start and reuses the compiled graph across jobs.

For each question in the job:
  1. Initialize the LangGraph state with topic + student profile.
  2. Iterate ``graph.stream(initial, stream_mode="updates")`` — LangGraph
     yields one chunk per node execution (the Critic loop produces multiple
     storyteller+critic chunks).
  3. After each chunk: persist a ``GenerationStep`` row AND publish an event
     to the Redis pub/sub channel ``gen-job:{job_id}`` so the SSE endpoint
     can broadcast it live.
  4. The Publisher's chunk carries the final ``question_id`` + ``was_duplicate``;
     we tally created/skipped counts from there.

Failures inside a single question attempt are caught so one bad roll doesn't
sink the rest of the batch.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.utils import timezone

from apps.generation import services
from apps.generation.models import GenerationJob, GenerationStep

logger = logging.getLogger("apps.generation")


@shared_task(bind=True, name="generation.run_job")
def run_generation_job(self, job_id: int) -> dict[str, Any]:
    """Run one batch of MAIQE generations, emitting per-node steps live."""
    try:
        job = GenerationJob.objects.get(pk=job_id)
    except GenerationJob.DoesNotExist:
        logger.error("run_generation_job: job %s does not exist", job_id)
        return {"status": "missing"}

    if job.is_terminal:
        # Could be a cancelled job picked up by a stale worker.
        return {"status": job.status}

    job.status = GenerationJob.STATUS_RUNNING
    job.started_at = timezone.now()
    job.celery_task_id = (self.request.id or job.celery_task_id) or ""
    job.save(update_fields=["status", "started_at", "celery_task_id"])

    services.publish_event(
        job.pk,
        {
            "type": "job.started",
            "job_id": job.pk,
            "topic": job.topic,
            "count": job.count,
        },
    )

    fatal_error = ""
    try:
        # Lazy: this import pulls in LangGraph + LLM clients.
        from agents_and_engine.graph import build_graph

        graph = build_graph()

        for idx in range(job.count):
            # Re-read job each iteration so a `cancel` request takes effect.
            job.refresh_from_db()
            if job.status == GenerationJob.STATUS_CANCELLED:
                services.publish_event(
                    job.pk,
                    {"type": "job.cancelled", "job_id": job.pk},
                )
                break

            try:
                _run_one_question(job, idx, graph)
            except Exception as exc:  # one bad roll must not kill the batch
                logger.exception(
                    "run_generation_job: question %s of job %s failed", idx, job.pk
                )
                _record_error_step(job, idx, str(exc))
                job.failed_count += 1
                job.save(update_fields=["failed_count"])
    except Exception as exc:
        logger.exception("run_generation_job: fatal failure on job %s", job.pk)
        fatal_error = str(exc)

    job.refresh_from_db()
    if job.status != GenerationJob.STATUS_CANCELLED:
        services.update_job_terminal(job, error=fatal_error)
        services.publish_event(
            job.pk,
            {
                "type": "job.finished",
                "job_id": job.pk,
                "status": job.status,
                "created_count": job.created_count,
                "skipped_count": job.skipped_count,
                "failed_count": job.failed_count,
                "error": fatal_error or None,
            },
        )

    return {"status": job.status, "job_id": job.pk}


def _run_one_question(job: GenerationJob, idx: int, graph) -> None:
    """Stream one full Architect→Storyteller→Critic→Publisher run.

    Each LangGraph chunk is shape ``{node_name: state_updates}``. We persist a
    ``GenerationStep`` and publish an event for each one.
    """
    initial: dict[str, Any] = {
        "topic": job.topic,
        "language": job.language,
        "student_profile": {},
    }
    if job.target_score is not None:
        initial["student_profile"] = {"target_score": job.target_score}

    final_publisher_data: dict[str, Any] | None = None

    for chunk in graph.stream(initial, stream_mode="updates"):
        for node_name, updates in chunk.items():
            kind = services.kind_for_node(node_name)
            message = services.format_node_message(node_name, updates or {})
            data = services.serialize_node_data(node_name, updates or {})

            step = GenerationStep.objects.create(
                job=job,
                question_index=idx,
                kind=kind,
                status=GenerationStep.STATUS_SUCCEEDED,
                message=message,
                data=data,
                question_id=(
                    updates.get("question_id")
                    if isinstance(updates, dict)
                    else None
                ),
            )
            services.publish_event(
                job.pk,
                {
                    "type": "step",
                    "step_id": step.pk,
                    "job_id": job.pk,
                    "question_index": idx,
                    "kind": kind,
                    "status": step.status,
                    "message": message,
                    "data": data,
                    "question_id": step.question_id,
                    "at": step.created_at.isoformat(),
                },
            )

            if node_name == "publisher":
                final_publisher_data = data

    # Tally results from the publisher's payload. No publisher chunk means
    # the Critic gave up (fallback edge to END) — count as failed.
    if final_publisher_data is None:
        job.failed_count += 1
        job.save(update_fields=["failed_count"])
        return

    if final_publisher_data.get("was_duplicate"):
        job.skipped_count += 1
        job.save(update_fields=["skipped_count"])
    elif final_publisher_data.get("question_id"):
        job.created_count += 1
        job.save(update_fields=["created_count"])
    else:
        job.failed_count += 1
        job.save(update_fields=["failed_count"])


def _record_error_step(job: GenerationJob, idx: int, message: str) -> None:
    step = GenerationStep.objects.create(
        job=job,
        question_index=idx,
        kind=GenerationStep.KIND_ERROR,
        status=GenerationStep.STATUS_FAILED,
        message=f"Error: {message[:280]}",
        data={"error": message[:1000]},
    )
    services.publish_event(
        job.pk,
        {
            "type": "step",
            "step_id": step.pk,
            "job_id": job.pk,
            "question_index": idx,
            "kind": GenerationStep.KIND_ERROR,
            "status": GenerationStep.STATUS_FAILED,
            "message": step.message,
            "data": step.data,
            "question_id": None,
            "at": step.created_at.isoformat(),
        },
    )
