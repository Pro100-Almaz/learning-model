"""Deterministic, fact-specific query planning for admission harvesting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SearchFact(StrEnum):
    PROGRAM_IDENTITY = "program_identity"
    LEGAL_MINIMUM = "legal_minimum"
    UNIVERSITY_MINIMUM = "university_minimum"
    HISTORICAL_GRANT_CUTOFF = "historical_grant_cutoff"
    PROFILE_SUBJECTS = "profile_subjects"
    PROGRAM_OFFERINGS = "program_offerings"


@dataclass(frozen=True, slots=True)
class SearchTarget:
    profession_name: str
    program_group_code: str
    program_group_name: str
    year: int
    legacy_codes: tuple[str, ...] = ()
    alternative_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "profession_name",
            "program_group_code",
            "program_group_name",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
        if not 2000 <= self.year <= 2100:
            raise ValueError("year must be between 2000 and 2100")


@dataclass(frozen=True, slots=True)
class PlannedQuery:
    fact: SearchFact
    anchor: str
    text: str


@dataclass(frozen=True, slots=True)
class SearchPage:
    fact: SearchFact
    query: str
    url: str
    content: str


_FACT_TERMS: dict[SearchFact, str] = {
    SearchFact.PROGRAM_IDENTITY: (
        "образовательная программа группа образовательных программ "
        "білім беру бағдарламаларының тобы Казахстан"
    ),
    SearchFact.LEGAL_MINIMUM: (
        "ЕНТ ҰБТ минимальный пороговый шекті балл типовые правила приема"
    ),
    SearchFact.UNIVERSITY_MINIMUM: (
        "ЕНТ ҰБТ пороговый балл прием университет шекті балл қабылдау"
    ),
    SearchFact.HISTORICAL_GRANT_CUTOFF: (
        "образовательный грант проходной балл конкурс грант өту балы"
    ),
    SearchFact.PROFILE_SUBJECTS: (
        "ЕНТ ҰБТ профильные предметы комбинация бейіндік пәндер комбинациясы"
    ),
    SearchFact.PROGRAM_OFFERINGS: (
        "университет образовательная программа реестр білім беру бағдарламасы"
    ),
}


def _unique_nonblank(values: tuple[str, ...]) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            unique.append(cleaned)
            seen.add(key)
    return tuple(unique)


def _anchors(target: SearchTarget, fact: SearchFact) -> tuple[str, ...]:
    if fact == SearchFact.HISTORICAL_GRANT_CUTOFF:
        values = (
            target.program_group_code,
            *target.legacy_codes,
            target.program_group_name,
        )
        return _unique_nonblank(values)[:4]

    if fact in {SearchFact.LEGAL_MINIMUM, SearchFact.UNIVERSITY_MINIMUM}:
        return _unique_nonblank((target.program_group_code, target.program_group_name))[
            :2
        ]

    values = (
        target.program_group_code,
        target.program_group_name,
        target.profession_name,
        *target.alternative_names,
    )
    return _unique_nonblank(values)[:4]


def build_queries(
    target: SearchTarget,
    facts: tuple[SearchFact, ...] | None = None,
) -> tuple[PlannedQuery, ...]:
    """Build bounded queries with one exact identity anchor per query."""
    selected_facts = facts or tuple(SearchFact)
    queries: list[PlannedQuery] = []
    seen: set[tuple[SearchFact, str]] = set()

    for fact in selected_facts:
        for anchor in _anchors(target, fact):
            text = f'"{anchor}" {target.year} {_FACT_TERMS[fact]}'
            key = (fact, text.casefold())
            if key in seen:
                continue
            queries.append(PlannedQuery(fact=fact, anchor=anchor, text=text))
            seen.add(key)

    return tuple(queries)
