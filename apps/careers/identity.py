"""Deterministic identity normalization and canonical resolvers."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.careers.models import EducationalProgramGroup, Profession


class AmbiguousIdentityError(LookupError):
    """Raised when one identifier points at more than one canonical identity."""


def normalize_identifier(value: str) -> str:
    """Normalize Unicode, whitespace, and case without changing punctuation."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.split()).casefold()


def resolve_program_group(
    value: str,
    *,
    scheme: str | None = None,
) -> EducationalProgramGroup | None:
    """Resolve a current code or verified alias to one canonical program group."""
    from apps.careers.models import (
        EducationalProgramGroup,
        ProgramGroupAlias,
        ProgramIdentifierScheme,
    )

    normalized = normalize_identifier(value)
    if not normalized:
        return None

    group_ids: set[int] = set()
    if scheme in {None, ProgramIdentifierScheme.CURRENT_GROUP_CODE}:
        group_ids.update(
            EducationalProgramGroup.objects.filter(code__iexact=normalized)
            .values_list("id", flat=True)
            .distinct()
        )

    if scheme != ProgramIdentifierScheme.CURRENT_GROUP_CODE:
        aliases = ProgramGroupAlias.objects.filter(normalized_value=normalized)
        if scheme is not None:
            aliases = aliases.filter(scheme=scheme)
        group_ids.update(aliases.values_list("program_group_id", flat=True).distinct())

    if not group_ids:
        return None
    if len(group_ids) > 1:
        raise AmbiguousIdentityError(
            f"Program identifier {value!r} resolves to multiple groups."
        )
    return EducationalProgramGroup.objects.get(pk=group_ids.pop())


def resolve_profession(
    value: str,
    *,
    scheme: str | None = None,
) -> Profession | None:
    """Resolve a stable slug or verified identifier to one profession."""
    from apps.careers.models import Profession, ProfessionIdentifier

    normalized = normalize_identifier(value)
    if not normalized:
        return None

    profession_ids: set[int] = set()
    if scheme is None:
        profession_ids.update(
            Profession.objects.filter(slug__iexact=normalized).values_list(
                "id", flat=True
            )
        )

    identifiers = ProfessionIdentifier.objects.filter(normalized_value=normalized)
    if scheme is not None:
        identifiers = identifiers.filter(scheme=scheme)
    profession_ids.update(identifiers.values_list("profession_id", flat=True).distinct())

    if not profession_ids:
        return None
    if len(profession_ids) > 1:
        raise AmbiguousIdentityError(
            f"Profession identifier {value!r} resolves to multiple professions."
        )
    return Profession.objects.get(pk=profession_ids.pop())
