import logging
import os

from langchain_openai import ChatOpenAI
from tavily import TavilyClient

from web_harvester import trust
from web_harvester.prompts_for_agents import (
    CLASSIFIER_SYSTEM_PROMPT,
    EXTRACTOR_SYSTEM_PROMPT,
    build_classifier_input,
    build_extractor_input,
)
from web_harvester.schemas import AdmissionExtraction, FieldClassification
from web_harvester.search_planning import SearchPage, SearchTarget, build_queries
from web_harvester.source_policy import FieldType, SourceStrategy

logger = logging.getLogger(__name__)

_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

MAX_CHARS = 10000
DEFAULT_MAX_RESULTS = 6


def truncate_content(text: str, max_chars: int = MAX_CHARS) -> str:
    """Limit page content without cutting the final word when possible."""
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space != -1:
        truncated = truncated[:last_space]

    return truncated + " ____ [Content Truncated]"


def search(
    target: SearchTarget,
    field_type: FieldType,
    strategy: SourceStrategy,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> list[SearchPage]:
    """Execute bounded fact-specific queries against allowed domains."""
    if max_results < 1:
        raise ValueError("max_results must be at least 1")

    domains = sorted(trust.allowed_domains(field_type, strategy))
    pages: list[SearchPage] = []
    seen_pages: set[tuple[str, str]] = set()
    for planned_query in build_queries(target):
        try:
            response = _client.search(
                query=planned_query.text,
                max_results=max_results,
                include_domains=domains,
                include_raw_content="text",
            )
        except Exception as error:
            logger.warning(
                "Tavily query failed for %s (%s/%s/%s): %s",
                target.program_group_code,
                field_type.value,
                strategy.value,
                planned_query.fact.value,
                type(error).__name__,
            )
            continue

        for result in response.get("results", []):
            url = result.get("url")
            content = result.get("raw_content") or result.get("content") or ""
            if not isinstance(url, str) or not isinstance(content, str):
                continue
            page_key = (planned_query.fact.value, url)
            if not url or not content or page_key in seen_pages:
                continue
            if not trust.is_allowed_source(url, field_type, strategy):
                continue

            pages.append(
                SearchPage(
                    fact=planned_query.fact,
                    query=planned_query.text,
                    url=url,
                    content=truncate_content(content),
                )
            )
            seen_pages.add(page_key)

    return pages


_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, timeout=60)
_classifier = _llm.with_structured_output(
    FieldClassification,
    method="function_calling",
)
_extractor = _llm.with_structured_output(AdmissionExtraction, method="function_calling")


def classify(target: SearchTarget) -> FieldType | None:
    """Classify a profession into one supported field."""
    messages = [
        ("system", CLASSIFIER_SYSTEM_PROMPT),
        ("human", build_classifier_input(target)),
    ]

    try:
        result = _classifier.invoke(messages)
        return result.field_type
    except Exception as error:
        logger.warning(
            "Profession field classification failed for %s: %s",
            target.profession_name,
            type(error).__name__,
        )
        return None


def extract(
    target: SearchTarget,
    pages: list[SearchPage],
) -> AdmissionExtraction | None:
    """Extract grounded profession facts from already validated pages."""
    if not pages:
        return None

    messages = [
        ("system", EXTRACTOR_SYSTEM_PROMPT),
        ("human", build_extractor_input(target, pages)),
    ]

    try:
        return _extractor.invoke(messages)
    except Exception as error:
        logger.warning(
            "ChatGPT extraction failed for %s: %s",
            target.profession_name,
            type(error).__name__,
        )
        return None
