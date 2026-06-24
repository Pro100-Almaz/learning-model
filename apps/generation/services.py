"""Business logic for the generation pipeline.

Three responsibilities:

1. ``dispatch_job`` — create a ``GenerationJob`` and hand it to Celery so the
   Architect/Storyteller/Critic/Publisher chain runs in the worker process.
2. ``publish_event`` / ``iter_event_stream`` — Redis pub/sub plumbing for the
   Server-Sent Events endpoint. Events are JSON dicts shipped over a channel
   named ``gen-job:<job_id>``.
3. ``format_node_message`` / ``serialize_node_data`` — turn the LangGraph
   per-node state update into compact, human-readable rows for both
   ``GenerationStep`` storage and the SSE wire payload.

The MAIQE graph + LLM imports are lazy in the Celery task, NOT here, so
``services`` stays import-safe inside the Django web process (admin pages,
list endpoints, etc.).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterator, Optional

from django.utils import timezone
from django_redis import get_redis_connection

from .models import GenerationJob, GenerationStep

logger = logging.getLogger("apps.generation")


CHANNEL_TEMPLATE = "gen-job:{job_id}"

# How often the SSE generator sends a no-op comment to keep the connection
# alive through reverse proxies (nginx defaults to 60s read timeout).
SSE_HEARTBEAT_SECONDS = 15
# Block-read timeout on the pub/sub get_message call. Tunes how snappy the
# heartbeat is vs. how often we wake up to do nothing.
SSE_POLL_SECONDS = 1.0
# Hard cap on stream duration so a forgotten client doesn't keep a connection
# open forever. 10 minutes is well past any realistic generation batch.
SSE_MAX_DURATION_SECONDS = 600


def dispatch_job(
    *,
    user,
    topic: str,
    count: int,
    target_score: Optional[int] = None,
) -> GenerationJob:
    """Create the job row, then hand it off to Celery.

    Returns the persisted ``GenerationJob`` with ``status='pending'`` and
    ``celery_task_id`` populated (so the caller can show the task id /
    later cancel it).
    """
    job = GenerationJob.objects.create(
        user=user if (user and user.is_authenticated) else None,
        topic=topic,
        count=count,
        target_score=target_score,
    )

    # Lazy import: ``apps.generation.tasks`` pulls in the LangGraph stack,
    # which is heavy. Web requests that just LIST jobs shouldn't pay for it.
    from . import tasks

    async_result = tasks.run_generation_job.delay(job.pk)
    job.celery_task_id = async_result.id or ""
    job.save(update_fields=["celery_task_id"])
    return job


# ---------------------------------------------------------------------------
# Pub/sub event plumbing
# ---------------------------------------------------------------------------


def publish_event(job_id: int, event: dict[str, Any]) -> None:
    """Push one event to the per-job Redis pub/sub channel.

    Best-effort: a Redis blip should not break a generation in progress, so
    we swallow connection errors after logging them.
    """
    try:
        conn = get_redis_connection("default")
        conn.publish(CHANNEL_TEMPLATE.format(job_id=job_id), json.dumps(event))
    except Exception:  # pragma: no cover - defensive
        logger.exception("publish_event: failed to publish on gen-job:%s", job_id)


def iter_event_stream(job: GenerationJob) -> Iterator[str]:
    """SSE generator: replay everything we already wrote, then go live.

    Yields strings already formatted for an SSE response body (``data: ...\\n\\n``).
    Stops when:
      * the job reaches a terminal status (the worker also publishes
        ``{"type": "job.finished", ...}`` to wake the listener), or
      * ``SSE_MAX_DURATION_SECONDS`` elapses (safety cap).
    """
    yield _sse(_job_snapshot_event(job))

    # 1) Replay every step already persisted at the moment the client connected.
    for step in job.steps.order_by("created_at", "id"):
        yield _sse(_step_event(step))

    # If the job is already done, we're done.
    job.refresh_from_db()
    if job.is_terminal:
        yield _sse(_job_finished_event(job))
        return

    # 2) Subscribe to the live channel.
    try:
        conn = get_redis_connection("default")
    except Exception:  # pragma: no cover - defensive
        logger.exception("iter_event_stream: redis connect failed")
        return
    pubsub = conn.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(CHANNEL_TEMPLATE.format(job_id=job.pk))

    started_at = time.monotonic()
    last_heartbeat = started_at

    try:
        while True:
            now = time.monotonic()
            if now - started_at > SSE_MAX_DURATION_SECONDS:
                # Bail out so the worker can't pile up dead connections.
                break

            msg = pubsub.get_message(timeout=SSE_POLL_SECONDS)
            if msg is None:
                if now - last_heartbeat > SSE_HEARTBEAT_SECONDS:
                    yield ": keepalive\n\n"
                    last_heartbeat = now
                continue

            payload = msg.get("data")
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="replace")
            if not isinstance(payload, str):
                continue
            yield f"data: {payload}\n\n"

            # Stop streaming once the job has fully concluded.
            try:
                event = json.loads(payload)
            except Exception:  # pragma: no cover - defensive
                continue
            if event.get("type") in ("job.finished", "job.failed", "job.cancelled"):
                break
    finally:
        try:
            pubsub.close()
        except Exception:  # pragma: no cover - defensive
            pass


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _job_snapshot_event(job: GenerationJob) -> dict[str, Any]:
    return {
        "type": "job.snapshot",
        "job_id": job.pk,
        "topic": job.topic,
        "count": job.count,
        "status": job.status,
        "created_count": job.created_count,
        "skipped_count": job.skipped_count,
        "failed_count": job.failed_count,
    }


def _job_finished_event(job: GenerationJob) -> dict[str, Any]:
    return {
        "type": "job.finished",
        "job_id": job.pk,
        "status": job.status,
        "created_count": job.created_count,
        "skipped_count": job.skipped_count,
        "failed_count": job.failed_count,
        "error": job.error or None,
    }


def _step_event(step: GenerationStep) -> dict[str, Any]:
    return {
        "type": "step",
        "step_id": step.pk,
        "question_index": step.question_index,
        "kind": step.kind,
        "status": step.status,
        "message": step.message,
        "data": step.data or {},
        "question_id": step.question_id,
        "at": step.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Node payload helpers — called from the Celery task to format the SSE row
# ---------------------------------------------------------------------------


# We never ship more than this many chars of a draft / spec / verdict over
# the wire — investors don't need the raw LLM token soup.
_MAX_DATA_STR = 600


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_DATA_STR:
        return value[: _MAX_DATA_STR - 1] + "…"
    return value


def format_node_message(node_name: str, updates: dict[str, Any]) -> str:
    """Short human-readable line shown in the SSE / admin step list."""
    if node_name == "architect":
        diff = updates.get("difficulty")
        ch = updates.get("content_hash")
        tag = updates.get("tag_name") or updates.get("tag_slug")
        ch_short = f"{ch[:8]}…" if isinstance(ch, str) and len(ch) >= 8 else ch or ""
        bits = []
        if diff is not None:
            bits.append(f"difficulty={diff}")
        if tag:
            bits.append(f"tag={tag}")
        if ch_short:
            bits.append(f"hash={ch_short}")
        return "Architect: rolled spec" + (f" ({', '.join(bits)})" if bits else "")

    if node_name == "storyteller":
        draft = updates.get("draft_text") or ""
        rev = updates.get("revision_count", 0)
        suffix = f" (rewrite #{rev})" if rev else ""
        return f"Storyteller: drafted{suffix} — {len(draft)} chars"

    if node_name == "critic":
        passed = updates.get("critic_passed")
        rev = updates.get("revision_count", 0)
        if passed:
            return "Critic: ✓ approved"
        notes = updates.get("rewrite_notes") or "needs rewrite"
        return f"Critic: ✗ rewrite (round {rev}) — {_truncate(notes)}"

    if node_name == "publisher":
        qid = updates.get("question_id")
        dup = updates.get("was_duplicate")
        if dup:
            return f"Publisher: dedup hit (existing Q#{qid})"
        return f"Publisher: ✓ saved Question #{qid}"

    return f"{node_name}: update"


def serialize_node_data(node_name: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Compact node payload for storage + SSE wire — only the fields the UI uses."""
    if node_name == "architect":
        return {
            "difficulty": updates.get("difficulty"),
            "tag_slug": updates.get("tag_slug"),
            "tag_name": updates.get("tag_name"),
            "content_hash": updates.get("content_hash"),
            "answer_key_preview": _truncate(str(updates.get("answer_key", ""))),
            "constraints_preview": _truncate(updates.get("constraints_payload", "") or ""),
        }
    if node_name == "storyteller":
        return {
            "draft_text": _truncate(updates.get("draft_text", "") or ""),
            "revision_count": updates.get("revision_count", 0),
        }
    if node_name == "critic":
        return {
            "critic_passed": bool(updates.get("critic_passed")),
            "revision_count": updates.get("revision_count", 0),
            "rewrite_notes": _truncate(updates.get("rewrite_notes", "") or ""),
        }
    if node_name == "publisher":
        return {
            "question_id": updates.get("question_id"),
            "was_duplicate": bool(updates.get("was_duplicate")),
            "lesson_id": updates.get("lesson_id"),
            "test_id": updates.get("test_id"),
        }
    return {}


def kind_for_node(node_name: str) -> str:
    """Map LangGraph node name → ``GenerationStep.kind`` value."""
    if node_name in (
        GenerationStep.KIND_ARCHITECT,
        GenerationStep.KIND_STORYTELLER,
        GenerationStep.KIND_CRITIC,
        GenerationStep.KIND_PUBLISHER,
    ):
        return node_name
    return GenerationStep.KIND_ERROR


def update_job_terminal(job: GenerationJob, error: str = "") -> None:
    """Apply the right terminal status given the per-question counts.

    Called by the Celery task after the inner loop finishes. The status is:
    - ``failed`` if EVERY attempt failed,
    - ``partial`` if some succeeded and some failed,
    - ``succeeded`` if all attempts produced or deduped a question.
    """
    total_attempts = job.created_count + job.skipped_count + job.failed_count
    if error:
        job.status = GenerationJob.STATUS_FAILED
        job.error = error[:5000]
    elif total_attempts == 0 or job.created_count + job.skipped_count == 0:
        job.status = GenerationJob.STATUS_FAILED
    elif job.failed_count and (job.created_count or job.skipped_count):
        job.status = GenerationJob.STATUS_PARTIAL
    else:
        job.status = GenerationJob.STATUS_SUCCEEDED
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "error",
            "finished_at",
            "created_count",
            "skipped_count",
            "failed_count",
        ]
    )
