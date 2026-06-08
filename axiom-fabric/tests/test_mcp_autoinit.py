"""Lazy store auto-initialization in the MCP server.

The autouse `fresh_db` fixture (conftest) pre-migrates an in-memory DB. These
tests deliberately point at a *fresh, un-migrated* file DB to exercise the
lazy-init path that runs on the first tool call.
"""

from __future__ import annotations

import asyncio

import pytest

from axiom_fabric.config import get_settings
from axiom_fabric.db import reset_engine_for_tests
from axiom_fabric.mcp import build_server
from axiom_fabric.migrate import is_initialized


def _run(coro):
    return asyncio.run(coro)


def call(server, tool_name, /, **arguments):
    _content, structured = _run(server.call_tool(tool_name, arguments))
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


@pytest.fixture
def empty_file_db(tmp_path, monkeypatch):
    """Point the store at a brand-new SQLite file that has *not* been migrated."""
    db = tmp_path / "af.db"
    monkeypatch.setenv("AF_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.delenv("AF_MCP_ELICIT_SETUP", raising=False)
    get_settings.cache_clear()
    reset_engine_for_tests()  # also clears the lazy-init flag
    yield db
    reset_engine_for_tests()
    get_settings.cache_clear()


def test_first_tool_call_auto_initializes(empty_file_db):
    assert not is_initialized()  # nothing migrated yet

    # A read tool on a never-initialized store must succeed, not crash.
    assert call(build_server(allow_writes=True), "list_layers") == []

    assert is_initialized()  # schema was created on first use


def test_write_works_immediately_on_fresh_store(empty_file_db):
    rw = build_server(allow_writes=True)
    layer = call(rw, "create_layer", name="requirements", weight=90, ordinal=0)
    assert layer["name"] == "requirements"
    fv = call(rw, "create_fact", layer="requirements", content={"claim": "x"})
    assert fv["version"] == 1


def test_elicit_flag_blocks_auto_create(empty_file_db, monkeypatch):
    monkeypatch.setenv("AF_MCP_ELICIT_SETUP", "1")
    rw = build_server(allow_writes=True)

    with pytest.raises(Exception) as excinfo:
        call(rw, "list_layers")
    assert "setup_store" in str(excinfo.value)
    assert not is_initialized()  # gated: nothing was created


def test_setup_store_falls_back_to_sqlite_without_client_elicitation(empty_file_db):
    # The in-process test harness has no elicitation-capable client, so setup_store
    # should catch the failed elicit and create the safe-default SQLite store.
    rw = build_server(allow_writes=True)
    result = call(rw, "setup_store")
    assert result["created"] is True
    assert result["status"] == "initialized"
    assert is_initialized()

    # Idempotent second call.
    again = call(rw, "setup_store")
    assert again["created"] is False
    assert again["status"] == "already_initialized"
