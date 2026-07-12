from __future__ import annotations

import os
from datetime import datetime, timezone
from io import BytesIO
import httpx
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from openai import OpenAI
import config
from .allow_list import allowed_hosts, is_allowed
from .prompts_for_agents import (
    EXTRACTOR_SYSTEM,
    HARVESTER_SYSTEM,
    build_extractor_prompt,
    build_harvester_prompt,
)
from .schemas import HarvestedSource, HarvestResult, SpecialtyDocument

_PREVIEW_CHARS = 2000

def _extract_text(response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    is_pdf = "application/pdf" in content_type or response.url.path.lower().endswith(".pdf")
    if is_pdf:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(response.content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return response.text

def _search(query: str) -> list[dict[str, str]]:
    response = httpx.post(
        "https://api.tavily.com/search",
        json = {"api_key": config.Config.SEARCH_API_KEY, "query": query, "max_results": 8},
        timeout = 20.0
    )
    response.raise_for_status()
    return [
        {"url": r["url"], "snippet": r.get("content", "")}
        for r in response.json().get("results", [])
    ]

def _guard_sources(sources: list[HarvestedSource]) -> list[HarvestedSource]:
    guarded: list[HarvestedSource] = []
    for source in sources:
        if not is_allowed(source.url):
            continue
        reachable = source.reachable and bool(source.raw_text.strip())
        guarded.append(source.model_copy(update = {"reachable": reachable}))
    return guarded

def run_harvester(specialty_code: str) -> HarvestResult:
    collected = []
    allowed = allowed_hosts()
    @tool
    def fetch_url(url: str) -> str:
        if not is_allowed(url):
            return f"REFUSED: {url} is not in the allowed list of URLs"
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            response = httpx.get(url, timeout = 20.0, follow_redirects = True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            collected.append(
                HarvestedSource(
                    url = url,
                    raw_text = "",
                    fetched_at = fetched_at,
                    reachable = False
                )
            )
            return f"ERROR FETCHING {url}: {exc}"
        text = _extract_text(response)
        collected.append(
            HarvestedSource(
                url = url,
                raw_text = text,
                fetched_at = fetched_at,
                reachable = True
            )
        )
        return text[:_PREVIEW_CHARS]

    @tool
    def web_search(query: str) -> list[dict[str, str]]:
        return _search(query)

    model = ChatOpenAI(
        model = config.HARVESTER_MODEL,
        api_key = config.Config.OPENAI_API_KEY,
        base_url = os.getenv("OPENAI_BASE_URL") or None,
    )

    agent = create_react_agent(
        model, tools = [web_search, fetch_url], prompt = HARVESTER_SYSTEM
    )

    agent.invoke(
        {"messages": [("human", build_harvester_prompt(specialty_code, allowed))]},
        config = {"recursion_limit": 50},
    )

    return HarvestResult(
        specialty_code = specialty_code,
        sources = _guard_sources(collected)
    )

_EXTRACT_TOOL_NAME = "emit_specialty_document"

def run_extractor(result: HarvestResult) -> SpecialtyDocument:
    client = OpenAI(
        api_key = config.Config.OPENAI_API_KEY,
        base_url = os.getenv("OPENAI_BASE_URL") or None,
    )
    tool_schema = {
        "type": "function",
        "function": {
            "name": _EXTRACT_TOOL_NAME,
            "description": "Return the structured specialty document.",
            "parameters": SpecialtyDocument.model_json_schema(),
        },
    }
    response = client.chat.completions.create(
        model = config.EXTRACTOR_MODEL,
        messages = [
            {"role": "system", "content": EXTRACTOR_SYSTEM},
            {"role": "user", "content": build_extractor_prompt(
                result.specialty_code, result.sources
            )},
        ],
        tools = [tool_schema],
        tool_choice = {
            "type": "function",
            "function": {"name": _EXTRACT_TOOL_NAME},
        },
    )
    call = response.choices[0].message.tool_calls[0]
    return SpecialtyDocument.model_validate_json(call.function.arguments)