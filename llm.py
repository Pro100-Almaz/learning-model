"""
LLM transport for MAIQE agents — the "voice" side, opposite math_engine's "math".

Thin wrapper over the providers. Nodes call `chat_openai(...)` / (later)
`chat_anthropic(...)` and get back plain text; they never deal with client
construction or message formatting. Per arch.md's heterogeneous routing:
Storyteller + Critic -> OpenAI/DeepSeek, Tutor -> Anthropic.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_openai import ChatOpenAI

from config import Config


def _build_client(model: str, temperature: float) -> ChatOpenAI:
    """Construct a ChatOpenAI client, honoring each model's temperature rules.

    `OPENAI_BASE_URL` (env) lets the same call hit DeepSeek instead of OpenAI,
    exactly as arch.md's cost-routing describes — no code change needed.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": Config.OPENAI_API_KEY,
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
    """Like `chat_openai`, but force the reply to match `schema`.

    `schema` is a TypedDict / Pydantic model / JSON schema. The model is bound
    to it at the API layer (structured outputs / tool-calling), so the result is
    a validated object — no fragile prose-scraping, and the model retries itself
    on a schema mismatch. Used by the Critic so a formatting quirk can no longer
    masquerade as a quality failure.
    """
    llm = _build_client(model, temperature)
    structured = llm.with_structured_output(schema)
    return structured.invoke([("system", system), ("human", user)])
