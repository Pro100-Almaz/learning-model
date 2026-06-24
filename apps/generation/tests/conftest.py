"""Stub the heavy LangGraph stack so generation tests stay hermetic.

The Celery task lazy-imports ``graph.build_graph`` (which in turn imports
``langgraph``, ``langchain_openai``, ``langchain_anthropic``). Those wheels
are several hundred MB and aren't required to exercise our orchestrator —
every test patches ``graph.build_graph`` with a scripted ``.stream()`` shim.

When the real modules are installed (worker image), this stub is a no-op:
``sys.modules`` already has them. When they're missing (CI / a lean test
image), we register MagicMock placeholders before any test imports run so
``patch("graph.build_graph", ...)`` resolves cleanly.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


def _ensure_stub(module_name: str) -> None:
    if module_name in sys.modules:
        return
    try:
        __import__(module_name)
    except Exception:
        sys.modules[module_name] = MagicMock(name=module_name)


for _name in ("langgraph", "langgraph.graph", "graph"):
    _ensure_stub(_name)
