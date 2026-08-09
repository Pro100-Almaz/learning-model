"""Field-aware source validation and confidence stamping."""

from collections.abc import Collection, Sequence
from typing import Literal, TypeAlias
from urllib.parse import urlsplit

from web_harvester.source_policy import (
    COMMON_PRIMARY_DOMAINS,
    FALLBACK_DOMAINS,
    FIELD_PRIMARY_DOMAINS,
    FieldType,
    SourceStrategy,
)

Confidence: TypeAlias = Literal["High", "Low"]
TrustStamp: TypeAlias = tuple[int, Confidence]

_STRATEGY_STAMPS: dict[SourceStrategy, TrustStamp] = {
    SourceStrategy.PRIMARY: (1, "High"),
    SourceStrategy.FALLBACK: (2, "Low"),
}


def domain_of(url: str) -> str:
    """Return a normalized hostname, or an empty string for malformed input."""
    candidate = url.strip()
    if not candidate:
        return ""

    value = (
        candidate
        if "://" in candidate or candidate.startswith("//")
        else f"//{candidate}"
    )

    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"", "http", "https"}:
            return ""
        domain = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""

    return domain.removeprefix("www.")


def allowed_domains(
    field_type: FieldType,
    strategy: SourceStrategy,
) -> frozenset[str]:
    """Return the domains permitted for one field and search strategy."""
    if strategy == SourceStrategy.PRIMARY:
        return COMMON_PRIMARY_DOMAINS | FIELD_PRIMARY_DOMAINS[field_type]
    if strategy == SourceStrategy.FALLBACK:
        return FALLBACK_DOMAINS
    raise ValueError(f"Unsupported source strategy: {strategy}")


def matches_domain(domain: str, allowed: Collection[str]) -> bool:
    """Match an exact allowed hostname or one of its real subdomains."""
    return any(
        domain == allowed_domain or domain.endswith(f".{allowed_domain}")
        for allowed_domain in allowed
    )


def is_allowed_source(
    url: str,
    field_type: FieldType,
    strategy: SourceStrategy,
) -> bool:
    """Return whether one URL belongs to the selected source group."""
    domain = domain_of(url)
    return bool(domain) and matches_domain(
        domain,
        allowed_domains(field_type, strategy),
    )


def strategy_of(url: str, field_type: FieldType) -> SourceStrategy | None:
    """Identify whether a URL is primary, fallback, or untrusted for a field."""
    for strategy in SourceStrategy:
        if is_allowed_source(url, field_type, strategy):
            return strategy
    return None


def filter_sources(
    urls: Sequence[str],
    field_type: FieldType,
    strategy: SourceStrategy,
) -> list[str]:
    """Keep allowed URLs in input order and discard duplicate occurrences."""
    return list(
        dict.fromkeys(url for url in urls if is_allowed_source(url, field_type, strategy))
    )


def validate_sources(
    sources: Sequence[str],
    field_type: FieldType,
    strategy: SourceStrategy,
) -> bool:
    """Require a non-empty source set entirely backed by one source group."""
    return bool(sources) and all(
        is_allowed_source(source, field_type, strategy) for source in sources
    )


def stamp(
    sources: Sequence[str],
    field_type: FieldType,
    strategy: SourceStrategy,
) -> TrustStamp | tuple[None, None]:
    """Return the strategy's tier/confidence only for a wholly valid source set."""
    if not validate_sources(sources, field_type, strategy):
        return None, None
    return _STRATEGY_STAMPS[strategy]
