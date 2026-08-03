"""Outcome types and usability rules for profession harvesting."""

from dataclasses import dataclass
from enum import StrEnum

from web_harvester import agents_web, trust
from web_harvester.schemas import WebSearch
from web_harvester.source_policy import FieldType, SourceStrategy


class AttemptStatus(StrEnum):
    SUCCESS = "success"
    NO_PAGES = "no_pages"
    EXTRACTION_FAILED = "extraction_failed"
    NO_USEFUL_FACTS = "no_useful_facts"
    INVALID_SOURCES = "invalid_sources"


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    strategy: SourceStrategy
    status: AttemptStatus
    result: WebSearch | None

    @property
    def succeeded(self) -> bool:
        return self.status == AttemptStatus.SUCCESS and self.result is not None


@dataclass(frozen=True, slots=True)
class HarvestOutcome:
    field_type: FieldType | None
    attempts: tuple[AttemptOutcome, ...]

    @property
    def succeeded(self) -> bool:
        return self.successful_attempt is not None

    @property
    def successful_attempt(self) -> AttemptOutcome | None:
        for attempt in self.attempts:
            if attempt.succeeded:
                return attempt
        return None

    @property
    def result(self) -> WebSearch | None:
        success_attempt = self.successful_attempt
        if success_attempt is not None:
            return success_attempt.result
        return None

    @property
    def strategy(self) -> SourceStrategy | None:
        success_attempt = self.successful_attempt
        if success_attempt is not None:
            return success_attempt.strategy
        return None


def has_useful_facts(result: WebSearch) -> bool:
    return bool(result.ubt_score is not None or result.subjects or result.universities)


def _run_attempt(
    name: str,
    national_code: str,
    field_type: FieldType,
    strategy: SourceStrategy,
    max_results: int,
) -> AttemptOutcome:
    pages = agents_web.search(
        name=name,
        national_code=national_code,
        field_type=field_type,
        strategy=strategy,
        max_results=max_results,
    )

    if not pages:
        return AttemptOutcome(
            strategy=strategy,
            status=AttemptStatus.NO_PAGES,
            result=None,
        )

    extracted_result = agents_web.extract(
        name=name,
        national_code=national_code,
        pages=pages,
    )

    if extracted_result is None:
        return AttemptOutcome(
            strategy=strategy,
            status=AttemptStatus.EXTRACTION_FAILED,
            result=None,
        )

    if not has_useful_facts(extracted_result):
        return AttemptOutcome(
            strategy=strategy,
            status=AttemptStatus.NO_USEFUL_FACTS,
            result=None,
        )

    if not trust.validate_sources(
        extracted_result.sources,
        field_type,
        strategy,
    ):
        return AttemptOutcome(
            strategy=strategy,
            status=AttemptStatus.INVALID_SOURCES,
            result=None,
        )

    fetched_urls = {url for url, _content in pages}
    if not set(extracted_result.sources).issubset(fetched_urls):
        return AttemptOutcome(
            strategy=strategy,
            status=AttemptStatus.INVALID_SOURCES,
            result=None,
        )

    return AttemptOutcome(
        strategy=strategy,
        status=AttemptStatus.SUCCESS,
        result=extracted_result,
    )


def harvest(
    name: str,
    national_code: str,
    primary_max_results: int = agents_web.DEFAULT_MAX_RESULTS,
    fallback_max_results: int = agents_web.DEFAULT_MAX_RESULTS,
) -> HarvestOutcome:
    """Classify a profession and run primary, then fallback, harvesting."""
    field_type = agents_web.classify(name, national_code)
    if field_type is None:
        return HarvestOutcome(field_type=None, attempts=())

    primary_attempt = _run_attempt(
        name=name,
        national_code=national_code,
        field_type=field_type,
        strategy=SourceStrategy.PRIMARY,
        max_results=primary_max_results,
    )
    if primary_attempt.succeeded:
        return HarvestOutcome(
            field_type=field_type,
            attempts=(primary_attempt,),
        )

    fallback_attempt = _run_attempt(
        name=name,
        national_code=national_code,
        field_type=field_type,
        strategy=SourceStrategy.FALLBACK,
        max_results=fallback_max_results,
    )
    return HarvestOutcome(
        field_type=field_type,
        attempts=(primary_attempt, fallback_attempt),
    )
