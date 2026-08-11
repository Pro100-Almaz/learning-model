"""
LLM part of the UBT_question_engine.
"""

from __future__ import annotations

import os
from typing import Any
from langchain_openai import ChatOpenAI
from config import Config

def _build_client(model: str, temperature: float) -> ChatOpenAI:
    kwargs: dict[str, Any] = {
        "api_key": Config.OPENAI_API_KEY,
        "model": model,
        "base_url": os.getenv("OPENAI_BASE_URL") or None,
    }
    # Reasoning models (o-series, GPT-5 family) only accept the default
    # temperature of 1 and 400 on any other value, so omit it for them.
    if not model.startswith(("o1", "o3", "o4", "gpt-5")):
        kwargs["temperature"] = temperature

    return ChatOpenAI(**kwargs)

def chat_openai(
    system: str,
    user: str,
    *,
    model: str,
    temperature: float = 0.7,
) -> str:
    """Send one system+user turn to an OpenAI-compatible model, return its text."""
    llm = _build_client(model, temperature)
    response = llm.invoke([("system", system), ("human", user)])
    return response.content

def chat_openai_structured(
    system: str,
    user: str,
    *,
    model: str,
    schema: Any,
    temperature: float = 0.0,
) -> dict[str, Any]:
    llm = _build_client(model, temperature)
    structured = llm.with_structured_output(schema)
    return structured.invoke([("system", system), ("human", user)])

def chat_anthropic(
    system: str,
    user: str,
    *,
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 300,
) -> str:
    """Send one system+user turn to an Anthropic (Claude) model, return its text."""
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(
        model=model,
        api_key=Config.ANTHRO_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    response = llm.invoke([("system", system), ("human", user)])

    # Anthropic replies can arrive as a list of content blocks; flatten to text.
    content = response.content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return content