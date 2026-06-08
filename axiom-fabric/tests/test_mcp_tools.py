"""End-to-end tests for the MCP tool surface, driven through FastMCP.call_tool.

Uses the autouse fresh in-memory DB from conftest. call_tool returns
(content_blocks, structured_result); we assert on the structured result.
"""

from __future__ import annotations

import asyncio

import pytest

from axiom_fabric.mcp import build_server


def _run(coro):
    return asyncio.run(coro)


def call(server, tool_name, /, **arguments):
    """Invoke a tool and return its structured result (unwrapping list results).

    `tool_name` is positional-only so tools with a `name` argument (e.g.
    create_layer) don't collide with the helper's own parameter.
    """
    _content, structured = _run(server.call_tool(tool_name, arguments))
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


@pytest.fixture
def rw():
    return build_server(allow_writes=True)


# ---- reads on an empty store ----------------------------------------------

def test_list_layers_empty(rw):
    assert call(rw, "list_layers") == []


def test_list_facts_empty(rw):
    assert call(rw, "list_facts") == []


# ---- create layer + fact ---------------------------------------------------

def test_create_layer_then_fact(rw):
    layer = call(rw, "create_layer", name="requirements", weight=90, ordinal=0)
    assert layer["name"] == "requirements"
    assert layer["weight"] == 90
    assert layer["version_count"] == 1

    fv = call(rw, "create_fact", layer="requirements", content={"claim": "ship by Q3"})
    assert fv["version"] == 1
    assert fv["weight"] == 90  # defaulted to the layer's weight
    assert fv["content"] == {"claim": "ship by Q3"}
    assert fv["retracted"] is False

    facts = call(rw, "list_facts", layer="requirements")
    assert len(facts) == 1
    assert facts[0]["latest_version"]["content"] == {"claim": "ship by Q3"}


def test_create_fact_explicit_weight(rw):
    call(rw, "create_layer", name="scratch", weight=10, ordinal=100)
    fv = call(rw, "create_fact", layer="scratch", content={"x": 1}, weight=42)
    assert fv["weight"] == 42


# ---- append-only update + retract -----------------------------------------

def test_update_appends_version_and_carries_weight(rw):
    call(rw, "create_layer", name="decisions", weight=60, ordinal=50)
    v1 = call(rw, "create_fact", layer="decisions", content={"db": "sqlite"})
    fact_id = v1["fact_id"]

    v2 = call(rw, "update_fact", fact_id=fact_id, content={"db": "postgres"})
    assert v2["version"] == 2
    assert v2["weight"] == 60  # carried forward from v1

    full = call(rw, "get_fact", fact_id=fact_id)
    assert full["version_count"] == 2
    assert [v["version"] for v in full["versions"]] == [1, 2]
    # v1 is preserved unchanged (append-only).
    assert full["versions"][0]["content"] == {"db": "sqlite"}


def test_retract_is_tombstone_not_delete(rw):
    call(rw, "create_layer", name="living", weight=10, ordinal=100)
    v1 = call(rw, "create_fact", layer="living", content={"guess": True})
    fact_id = v1["fact_id"]

    tomb = call(rw, "retract_fact", fact_id=fact_id, note="superseded")
    assert tomb["retracted"] is True
    assert tomb["weight"] == 0

    # Default list excludes retracted; include_retracted surfaces it; history intact.
    assert call(rw, "list_facts", layer="living") == []
    with_retracted = call(rw, "list_facts", layer="living", include_retracted=True)
    assert len(with_retracted) == 1
    full = call(rw, "get_fact", fact_id=fact_id)
    assert full["version_count"] == 2


# ---- edges + provenance ----------------------------------------------------

def test_edges_to_records_provenance(rw):
    call(rw, "create_layer", name="canonical", weight=90, ordinal=0)
    call(rw, "create_layer", name="living", weight=10, ordinal=100)
    parent = call(rw, "create_fact", layer="canonical", content={"rule": "x"})
    child = call(
        rw,
        "create_fact",
        layer="living",
        content={"derived": "y"},
        edges_to=[parent["id"]],
        edge_kind="derived_from",
    )
    edges = call(rw, "get_fact_edges", fv_id=child["id"])
    assert len(edges["outgoing"]) == 1
    assert edges["outgoing"][0]["target_fv_id"] == parent["id"]
    assert edges["outgoing"][0]["edge_kind"] == "derived_from"
    # Parent sees the incoming edge.
    parent_edges = call(rw, "get_fact_edges", fv_id=parent["id"])
    assert len(parent_edges["incoming"]) == 1


def test_forward_reference_edge_rejected(rw):
    call(rw, "create_layer", name="living", weight=10, ordinal=100)
    bogus = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(Exception) as exc:  # surfaces as ToolError
        call(rw, "create_fact", layer="living", content={"a": 1}, edges_to=[bogus])
    assert "exist" in str(exc.value).lower()


# ---- search + layer history -----------------------------------------------

def test_search_facts_substring(rw):
    call(rw, "create_layer", name="notes", weight=20, ordinal=10)
    call(rw, "create_fact", layer="notes", content={"text": "the API base url is https://x"})
    call(rw, "create_fact", layer="notes", content={"text": "unrelated"})
    hits = call(rw, "search_facts", query="api base")
    assert len(hits) == 1
    assert "API base" in hits[0]["latest_version"]["content"]["text"]


def test_get_layer_history(rw):
    call(rw, "create_layer", name="reqs", weight=90, ordinal=0)
    # Each create_fact wraps a new layer-version, so history grows.
    call(rw, "create_fact", layer="reqs", content={"a": 1})
    hist = call(rw, "get_layer_history", layer_name="reqs")
    assert hist["layer"]["name"] == "reqs"
    assert len(hist["versions"]) >= 2  # v1 (seed) + the create


# ---- error surfaces --------------------------------------------------------

def test_unknown_layer_errors(rw):
    with pytest.raises(Exception) as exc:
        call(rw, "create_fact", layer="missing", content={"a": 1})
    assert "no such layer" in str(exc.value).lower()


def test_bad_uuid_errors(rw):
    with pytest.raises(Exception) as exc:
        call(rw, "get_fact", fact_id="not-a-uuid")
    assert "uuid" in str(exc.value).lower()
