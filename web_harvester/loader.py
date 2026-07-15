"""Persistence for harvested professions.

Turns an extractor ``WebSearch`` result into a ``Profession`` row via an
idempotent upsert keyed on the composite ``(national_code, name)``. Re-running
the harvest updates the existing row instead of creating duplicates.

Trust is decided in ``trust.py``: a result whose sources are not trusted
(stamp returns tier ``None``) is deliberately NOT stored.
"""

from django.db import transaction
from django.utils import timezone

from web_harvester import trust
from web_harvester.models import Profession
from web_harvester.schemas import WebSearch


def save(name: str, national_code: str, result: WebSearch) -> Profession | None:
    """Idempotently upsert one harvested profession.

    Keyed on the composite (national_code, name). Returns the saved Profession,
    or None when no trusted source backed the data (untrusted data is not stored).
    """
    tier, confidence = trust.stamp(result.sources)
    if tier is None:
        # No trusted source backed this extraction — skip it rather than
        # store data we don't trust (and confidence would be NULL here).
        return None

    with transaction.atomic():
        obj, _created = Profession.objects.update_or_create(
            national_code=national_code,
            name=name,
            defaults={
                "ubt_score": result.ubt_score,
                "subjects": result.subjects,
                "universities": result.universities,
                "sources": result.sources,
                "source_tier": tier,
                "confidence": confidence,
                "fetched_at": timezone.now(),
            },
        )

    return obj
