"""Tests for Agent 2 (Extractor): raw source text -> one SpecialtyDocument.

No network. The OpenAI client is replaced with a fake whose forced tool-call
returns a canned JSON payload; these tests verify the request wiring
(forced tool_choice, generated schema, model) and the prompt assembly /
Pydantic parsing — not the model's judgement.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from web_harvester import agents_web
from web_harvester.schemas import HarvestedSource, HarvestResult, SpecialtyDocument


def _fake_document_args() -> str:
    """A tool-call `arguments` string shaped like a valid SpecialtyDocument."""
    return json.dumps(
        {
            "specialty_code": "6B01401",
            "field": {"value": "Педагогика (Педагогика)", "as_of": 2026, "carried_forward": False},
            "subject_combination": {"value": "Математика + Физика", "as_of": 2026, "carried_forward": False},
            "threshold": {"value": 70.0, "as_of": 2026, "carried_forward": False},
            "universities": [
                {
                    "name": {"value": "ЕҰУ (ЕНУ)", "as_of": 2026, "carried_forward": False},
                    "passing_score": {"value": 95.0, "as_of": 2026, "carried_forward": False},
                    "tuition": {"value": None, "as_of": None, "carried_forward": False},
                }
            ],
            "professions": [
                {"value": "Мұғалім (Учитель)", "as_of": 2026, "carried_forward": False}
            ],
            "source_year_confidence": "high",
        }
    )


class _FakeCompletions:
    def __init__(self, recorder: dict, args: str):
        self._recorder = recorder
        self._args = args

    def create(self, **kwargs):
        self._recorder.update(kwargs)
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="emit_specialty_document", arguments=self._args)
        )
        message = SimpleNamespace(tool_calls=[tool_call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def spy_openai(monkeypatch):
    """Patch web_harvester.agents_web.OpenAI; record the create() kwargs."""
    recorder: dict = {}
    args = _fake_document_args()

    def fake_openai(*_a, **_k):
        client = SimpleNamespace()
        client.chat = SimpleNamespace(completions=_FakeCompletions(recorder, args))
        return client

    monkeypatch.setattr(agents_web, "OpenAI", fake_openai)
    return recorder


@pytest.fixture
def result() -> HarvestResult:
    return HarvestResult(
        specialty_code="6B01401",
        sources=[
            HarvestedSource(
                url="https://testcenter.kz/spec",
                raw_text="Мамандық: Педагогика. Табалдырық балл: 70. 2026 жыл.",
                fetched_at="2026-07-11T00:00:00+00:00",
                reachable=True,
            ),
            HarvestedSource(
                url="https://enu.kz/dead",
                raw_text="",
                fetched_at="2026-07-11T00:00:00+00:00",
                reachable=False,
            ),
        ],
    )


def test_returns_validated_specialty_document(spy_openai, result):
    doc = agents_web.run_extractor(result)

    assert isinstance(doc, SpecialtyDocument)
    assert doc.specialty_code == "6B01401"
    assert doc.threshold.value == 70.0
    assert doc.threshold.carried_forward is False
    assert doc.universities[0].tuition.value is None
    assert doc.source_year_confidence == "high"


def test_forces_the_tool_call_with_the_generated_schema(spy_openai, result):
    agents_web.run_extractor(result)

    assert spy_openai["model"] == "gpt-5-mini"
    # Tool choice is forced to our single emitter function.
    assert spy_openai["tool_choice"] == {
        "type": "function",
        "function": {"name": "emit_specialty_document"},
    }
    # The schema fed to the tool is generated from the model, not hand-typed.
    (tool,) = spy_openai["tools"]
    assert tool["function"]["name"] == "emit_specialty_document"
    assert tool["function"]["parameters"] == SpecialtyDocument.model_json_schema()
    # No temperature is sent (gpt-5/o-series reject non-default).
    assert "temperature" not in spy_openai


def test_prompt_includes_only_reachable_sources(spy_openai, result):
    agents_web.run_extractor(result)

    user_msg = next(m for m in spy_openai["messages"] if m["role"] == "user")["content"]
    assert "6B01401" in user_msg
    assert "https://testcenter.kz/spec" in user_msg  # reachable source text is fed
    assert "https://enu.kz/dead" not in user_msg      # unreachable source is dropped
