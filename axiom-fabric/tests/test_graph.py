from __future__ import annotations

from axiom_fabric.db import session_scope
from axiom_fabric.facts import FactSpec, create_layer_version
from axiom_fabric.graph import load_graph
from axiom_fabric.layers import get_layer_by_name, seed_default_layers


def test_load_graph_empty_after_seed():
    """After plain seeding: three layers, each with one layer-version, no facts."""
    with session_scope() as session:
        seed_default_layers(session)

    with session_scope() as session:
        graph = load_graph(session)

    assert [layer.name for layer in graph.layers] == ["canonical", "episodic", "living"]
    assert all(len(layer.versions) == 1 for layer in graph.layers)
    assert graph.fact_count == 0
    assert graph.fact_version_count == 0
    assert graph.edges == []


def test_load_graph_with_facts_versions_and_edges():
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")

        # v2: a parent fact.
        lv2 = create_layer_version(
            session,
            canonical,
            fact_specs=[FactSpec(content={"claim": "parent"}, weight=90)],
        )
        parent_fv_id = lv2.fact_versions[0].id
        parent_fact_id = lv2.fact_versions[0].fact_id

        # v3: a child fact derived from the parent (one edge), plus a second
        # version of the parent fact.
        create_layer_version(
            session,
            canonical,
            fact_specs=[
                FactSpec(content={"claim": "child"}, weight=80, edges_to=(parent_fv_id,)),
                FactSpec(fact_id=parent_fact_id, content={"claim": "parent v2"}, weight=90),
            ],
        )

    with session_scope() as session:
        graph = load_graph(session)

    canonical = next(layer for layer in graph.layers if layer.name == "canonical")
    assert canonical.facts, "expected facts in canonical"
    assert graph.fact_count == 2  # parent + child
    assert graph.fact_version_count == 3  # parent v1, parent v2, child v1

    parent = next(f for f in canonical.facts if f.id == parent_fact_id)
    assert [v.version for v in parent.versions] == [1, 2]
    assert parent.latest.version == 2

    assert len(graph.edges) == 1
    assert graph.edges[0].target_fv_id == parent_fv_id
    assert graph.edges[0].edge_kind == "derived_from"
