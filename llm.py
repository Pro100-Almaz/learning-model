"""
LLM transport for MAIQE agents — the "voice" side, opposite math_engine's "math".

Thin wrapper over the providers. Nodes call `chat_openai(...)` / (later)
`chat_anthropic(...)` and get back plain text; they never deal with client
construction or message formatting. Per arch.md's heterogeneous routing:
Storyteller + Critic -> OpenAI/DeepSeek, Tutor -> Anthropic.
"""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from config import Config


def chat_openai(
    system: str,
    user: str,
    *,
    model: str,
    temperature: float = 0.7,
) -> str:
    """Send one system+user turn to an OpenAI-compatible model, return its text.

    `OPENAI_BASE_URL` (env) lets the same call hit DeepSeek instead of OpenAI,
    exactly as arch.md's cost-routing describes — no code change needed.
    """
    llm = ChatOpenAI(
        model=model,
        api_key=Config.OPENAI_API_KEY,
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        temperature=temperature,
    )
    response = llm.invoke([("system", system), ("human", user)])
    return response.content
