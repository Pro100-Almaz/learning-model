"""Shared fixtures for the UBT engine's tests.

None of these tests touch the database or the network. The engine is Django-free
everywhere except publisher.py, and this directory keeps it that way -- the DB
and API tests live under apps/.
"""

from __future__ import annotations

import pytest

from ubt_question_engine.loader import list_topics
from ubt_question_engine.testing import use_fake_translations, use_no_translations

# Enough seeds to exercise every topic's constraint solving and pool filtering
# without turning the suite into a two-minute wait. The exhaustive 40-seed sweep
# is a manual job, not a per-commit one.
SEEDS = (0, 1, 2)


@pytest.fixture(scope="session")
def topics() -> list[str]:
    return list_topics()


@pytest.fixture
def fake_i18n():
    with use_fake_translations() as store:
        yield store


@pytest.fixture
def empty_i18n():
    with use_no_translations():
        yield
