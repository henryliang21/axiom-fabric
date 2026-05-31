from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from axiom_fabric.config import get_settings
from axiom_fabric.db import reset_engine_for_tests
from axiom_fabric.migrate import upgrade_to_head
from axiom_fabric_dashboard.app import app


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    """Each test gets a fresh, migrated in-memory SQLite database (unseeded)."""
    monkeypatch.setenv("AF_DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    reset_engine_for_tests()
    upgrade_to_head()
    yield
    reset_engine_for_tests()
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
