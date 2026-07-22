import os
from tavily import TavilyClient
from langchain_openai import ChatOpenAI
from web_harvester.schemas import WebSearch
from web_harvester.prompts_for_agents import EXTRACTOR_SYSTEM_PROMPT, build_extractor_input

_client = TavilyClient(api_key = os.getenv("TAVILY_API_KEY"))

MAX_CHARS = 10000

#===================================================
#                 SEARCH FUNCTION
#===================================================

def truncate_content(text: str, max_chars: int = MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]

    last_space = truncated.rfind(" ")
    if last_space != -1:
        truncated = truncated[:last_space]

    return truncated + " ____ [Content Truncated]"

def search(name:str, national_code: str, max_results: int = 6) -> list[tuple[str, str]]:
    query = f"{name}({national_code}) ҰБТ проходной балл предметы университеты Казахстан"
    try:
        response = _client.search(
            query = query,
            max_results = max_results,
            include_raw_content = True
        )

        pages = []
        for r in response.get("results", []):
            url = r.get("url")
            content = r.get("raw_content") or r.get("content") or ""
            if url and content:
                truncated_content = truncate_content(content, max_chars = MAX_CHARS)
                pages.append((url, truncated_content))
        return pages

    except Exception as e:
        print(f"Tavily Search Failed for query: {query} ({e})")
        return []

#===================================================
#               EXTRACTION FUNCTION
#===================================================

_llm = ChatOpenAI(model = "gpt-4o-mini", temperature = 0, timeout = 60)
_extractor = _llm.with_structured_output(WebSearch, method = "function_calling")

def extract(name: str, national_code: str, pages: list[tuple[str, str]]) -> WebSearch | None:
    messages = [
        ("system", EXTRACTOR_SYSTEM_PROMPT),
        ("human", build_extractor_input(name, national_code, pages))
    ]
    if not pages:
        return None

    try:
        result = _extractor.invoke(messages)
        return result
    except Exception as e:
        print(f"ChatGPT search and extraction failed for {name}, {e}")
        return None