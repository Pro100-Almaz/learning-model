"""
LLM transport for the UBT question engine.

Thin wrapper over the providers. Callers pass a system and a user turn and get
back text or a validated object; they never construct a client.

Almost nothing in this engine uses it. Mathematics is Python's, and the words
are translated once offline and cached, so exactly two callers exist:
scripts/build_i18n.py (594 calls, ever) and the Tutor (live, per request).
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Callable, TypeVar

from langchain_openai import ChatOpenAI

from config import Config

T = TypeVar("T")

# A translation build makes hundreds of sequential calls, and a rate limit on
# call 400 must not cost the first 399. Five attempts over ~30s of backoff
# outlasts every transient failure worth waiting for.
MAX_TRANSPORT_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 2.0

# Substrings of errors that no amount of waiting will fix. Matching on the
# message is crude, but the alternative is importing provider-specific exception
# classes and coupling this module to whichever SDK version is installed.
_PERMANENT = (
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "permission",
    "model_not_found",
    "does not exist",
    "content_policy",
    "invalid_request_error",
)


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


def _with_retry(call: Callable[[], T], *, what: str) -> T:
    """Run `call`, retrying transport failures with exponential backoff.

    Jittered on purpose: a build script that retried on an exact schedule would
    march its retries straight back into the same rate-limit window.
    """
    last: Exception | None = None
    for attempt in range(MAX_TRANSPORT_ATTEMPTS):
        try:
            return call()
        except Exception as error:  # provider SDKs raise their own hierarchies
            message = str(error).lower()
            if any(marker in message for marker in _PERMANENT):
                raise
            last = error
            if attempt == MAX_TRANSPORT_ATTEMPTS - 1:
                break
            delay = BACKOFF_BASE_SECONDS * (2**attempt) * (0.5 + random.random())
            print(f"  [retry {attempt + 1}/{MAX_TRANSPORT_ATTEMPTS}] {what}: {error}")
            time.sleep(delay)

    raise RuntimeError(
        f"{what} failed after {MAX_TRANSPORT_ATTEMPTS} attempts: {last}"
    ) from last


def chat_openai(
    system: str,
    user: str,
    *,
    model: str,
    temperature: float = 0.7,
) -> str:
    """Send one system+user turn to an OpenAI-compatible model, return its text."""
    llm = _build_client(model, temperature)

    def call() -> str:
        return llm.invoke([("system", system), ("human", user)]).content

    return _with_retry(call, what=f"chat_openai({model})")


def chat_openai_structured(
    system: str,
    user: str,
    *,
    model: str,
    schema: Any,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Like chat_openai, but force the reply to match `schema`.

    Used by the Critic, so that a formatting quirk can never masquerade as a
    quality failure: the model is bound to the schema at the API layer and
    retries itself on a mismatch.
    """
    llm = _build_client(model, temperature)
    structured = llm.with_structured_output(schema)

    def call() -> dict[str, Any]:
        return structured.invoke([("system", system), ("human", user)])

    return _with_retry(call, what=f"chat_openai_structured({model})")


def chat_anthropic(
    system: str,
    user: str,
    *,
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 300,
) -> str:
    """Send one system+user turn to an Anthropic (Claude) model, return its text.

    The Tutor's transport. Imported lazily so the offline build path stays
    importable without the Anthropic client installed.
    """
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(
        model=model,
        api_key=Config.ANTHRO_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    def call() -> str:
        content = llm.invoke([("system", system), ("human", user)]).content
        # Anthropic replies can arrive as a list of content blocks; flatten.
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return content

    return _with_retry(call, what=f"chat_anthropic({model})")
