from __future__ import annotations

import pytest

from axiom_fabric.config import get_settings
from axiom_fabric.db import reset_engine_for_tests
from axiom_fabric.migrate import upgrade_to_head


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    """Give every test a fresh in-memory SQLite database with migrations applied."""
    monkeypatch.setenv("AF_DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    reset_engine_for_tests()
    upgrade_to_head()
    yield
    reset_engine_for_tests()
    get_settings.cache_clear()
