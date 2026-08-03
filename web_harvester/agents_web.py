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
from web_harvester.schemas import FieldClassification, WebSearch
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
    name: str,
    national_code: str,
    field_type: FieldType,
    strategy: SourceStrategy,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> list[tuple[str, str]]:
    """Search only the domains allowed for one field and source strategy."""
    if max_results < 1:
        raise ValueError("max_results must be at least 1")

    domains = sorted(trust.allowed_domains(field_type, strategy))
    field_label = field_type.value.replace("_", " ")
    query = (
        f'"{name}" "{national_code}" {field_label} '
        "ҰБТ ЕНТ проходной балл профильные предметы "
        "университеты Казахстан"
    )

    try:
        response = _client.search(
            query=query,
            max_results=max_results,
            include_domains=domains,
            include_raw_content="text",
        )
    except Exception as error:
        logger.warning(
            "Tavily search failed for %s (%s/%s): %s",
            name,
            field_type.value,
            strategy.value,
            type(error).__name__,
        )
        return []

    pages: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for result in response.get("results", []):
        url = result.get("url")
        content = result.get("raw_content") or result.get("content") or ""
        if not isinstance(url, str) or not isinstance(content, str):
            continue
        if not url or not content or url in seen_urls:
            continue
        if not trust.is_allowed_source(url, field_type, strategy):
            continue

        pages.append((url, truncate_content(content)))
        seen_urls.add(url)

    return pages


_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, timeout=60)
_classifier = _llm.with_structured_output(
    FieldClassification,
    method="function_calling",
)
_extractor = _llm.with_structured_output(WebSearch, method="function_calling")


def classify(name: str, national_code: str) -> FieldType | None:
    """Classify a profession into one supported field."""
    messages = [
        ("system", CLASSIFIER_SYSTEM_PROMPT),
        ("human", build_classifier_input(name, national_code)),
    ]

    try:
        result = _classifier.invoke(messages)
        return result.field_type
    except Exception as error:
        logger.warning(
            "Profession field classification failed for %s: %s",
            name,
            type(error).__name__,
        )
        return None


def extract(
    name: str,
    national_code: str,
    pages: list[tuple[str, str]],
) -> WebSearch | None:
    """Extract grounded profession facts from already validated pages."""
    if not pages:
        return None

    messages = [
        ("system", EXTRACTOR_SYSTEM_PROMPT),
        ("human", build_extractor_input(name, national_code, pages)),
    ]

    try:
        return _extractor.invoke(messages)
    except Exception as error:
        logger.warning(
            "ChatGPT extraction failed for %s: %s",
            name,
            type(error).__name__,
        )
        return None
