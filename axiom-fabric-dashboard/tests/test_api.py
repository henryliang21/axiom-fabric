from __future__ import annotations

from axiom_fabric.config import get_settings
from axiom_fabric.db import reset_engine_for_tests, session_scope
from axiom_fabric.facts import FactSpec, create_layer_version
from axiom_fabric.layers import get_layer_by_name, seed_default_layers


def _seed_graph_with_edge() -> str:
    """Seed layers + a parent/child fact pair with one edge. Return child fv id."""
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        lv2 = create_layer_version(
            session,
            canonical,
            fact_specs=[FactSpec(content={"claim": "parent"}, weight=90)],
        )
        parent_fv_id = lv2.fact_versions[0].id
        lv3 = create_layer_version(
            session,
            canonical,
            fact_specs=[
                FactSpec(content={"claim": "child"}, weight=80, edges_to=(parent_fv_id,)),
            ],
        )
        return str(lv3.fact_versions[0].id)


def test_health_ok_after_seed(client):
    with session_scope() as session:
        seed_default_layers(session)

    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["initialized"] is True
    assert body["database_backend"] == "sqlite"
    assert body["layer_count"] == 3
    assert body["revision"] is not None


def test_health_uninitialized_when_not_migrated(client):
    # Drop to a brand-new, unmigrated in-memory database.
    reset_engine_for_tests()
    get_settings.cache_clear()

    body = client.get("/api/health").json()
    assert body["status"] == "uninitialized"
    assert body["initialized"] is False
    assert body["layer_count"] is None


def test_graph_endpoint_returns_facts_and_edges(client):
    _seed_graph_with_edge()

    resp = client.get("/api/graph")
    assert resp.status_code == 200
    body = resp.json()

    assert [layer["name"] for layer in body["layers"]] == ["canonical", "episodic", "living"]
    assert body["fact_count"] == 2
    assert body["fact_version_count"] == 2
    assert len(body["edges"]) == 1
    assert body["edges"][0]["edge_kind"] == "derived_from"

    canonical = next(layer for layer in body["layers"] if layer["name"] == "canonical")
    a_fact = canonical["facts"][0]
    assert a_fact["latest_version_id"] is not None
    assert len(a_fact["versions"]) >= 1


def test_graph_returns_503_when_uninitialized(client):
    reset_engine_for_tests()
    get_settings.cache_clear()

    resp = client.get("/api/graph")
    assert resp.status_code == 503


def test_fact_version_edges_endpoint(client):
    child_fv_id = _seed_graph_with_edge()

    body = client.get(f"/api/fact-versions/{child_fv_id}/edges").json()
    assert body["fact_version_id"] == child_fv_id
    assert len(body["outgoing"]) == 1  # child derived_from parent
    assert body["incoming"] == []


def test_root_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
