"""Persistence boundary for completed profession harvests."""

from django.db import transaction
from django.utils import timezone

from web_harvester import trust
from web_harvester.models import Profession
from web_harvester.orchestration import HarvestOutcome, has_useful_facts


def save(
    name: str,
    national_code: str,
    outcome: HarvestOutcome,
) -> Profession | None:
    """Idempotently save one successful, useful, and trusted harvest outcome."""
    if not outcome.succeeded or outcome.field_type is None:
        return None

    attempt = outcome.successful_attempt
    if attempt is None or attempt.result is None:
        return None

    result = attempt.result
    if not has_useful_facts(result):
        return None

    tier, confidence = trust.stamp(
        result.sources,
        outcome.field_type,
        attempt.strategy,
    )
    if tier is None or confidence is None:
        return None

    with transaction.atomic():
        profession, _created = Profession.objects.update_or_create(
            national_code=national_code,
            name=name,
            defaults={
                "field_type": outcome.field_type.value,
                "ubt_score": result.ubt_score,
                "subjects": result.subjects,
                "universities": result.universities,
                "sources": result.sources,
                "source_strategy": attempt.strategy.value,
                "source_tier": tier,
                "confidence": confidence,
                "fetched_at": timezone.now(),
            },
        )

    return profession
