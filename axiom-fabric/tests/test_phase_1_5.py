from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from axiom_fabric.db import session_scope
from axiom_fabric.facts import (
    FactSpec,
    ForwardReferenceError,
    create_layer_version,
    edges_for,
    record_fact_version,
)
from axiom_fabric.layers import (
    DEFAULT_LAYERS,
    get_layer_by_name,
    get_layer_version,
    list_layer_versions,
    list_layers,
    seed_default_layers,
)
from axiom_fabric.models import Fact, FactVersion, FactVersionEdge, Layer, LayerVersion


def test_seed_default_layers_creates_three_layers_with_v1():
    with session_scope() as session:
        seed_default_layers(session)

    with session_scope() as session:
        layers = list_layers(session)
        assert [layer.name for layer in layers] == [s.name for s in DEFAULT_LAYERS]

        for layer in layers:
            versions = list_layer_versions(session, layer)
            assert len(versions) == 1
            assert versions[0].version == 1
            assert versions[0].weight == layer.weight
            assert versions[0].ordinal == layer.ordinal


def test_seed_is_idempotent():
    with session_scope() as session:
        seed_default_layers(session)
    with session_scope() as session:
        seed_default_layers(session)  # second call should not duplicate

    with session_scope() as session:
        layer_count = session.scalar(select(func.count()).select_from(Layer))
        lv_count = session.scalar(select(func.count()).select_from(LayerVersion))
        assert layer_count == len(DEFAULT_LAYERS)
        assert lv_count == len(DEFAULT_LAYERS)


def test_create_layer_version_increments_version_number():
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        lv2 = create_layer_version(
            session,
            canonical,
            fact_specs=[FactSpec(content={"claim": "fire is hot"}, weight=90)],
            notes="add fire-is-hot",
        )
        lv3 = create_layer_version(
            session,
            canonical,
            fact_specs=[FactSpec(content={"claim": "ice is cold"}, weight=90)],
            notes="add ice-is-cold",
        )
        assert lv2.version == 2
        assert lv3.version == 3

        history = list_layer_versions(session, canonical)
        assert [lv.version for lv in history] == [1, 2, 3]


def test_record_fact_version_writes_edges_from_edges_to():
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        # Create a v2 with one fact (parent).
        lv2 = create_layer_version(
            session,
            canonical,
            fact_specs=[FactSpec(content={"claim": "parent"}, weight=80)],
        )
        parent_fv = lv2.fact_versions[0]
        parent_fv_id = parent_fv.id

        # Create a v3 with a fact derived from the parent.
        lv3 = create_layer_version(
            session,
            canonical,
            fact_specs=[
                FactSpec(
                    content={"claim": "child"},
                    weight=80,
                    edges_to=(parent_fv_id,),
                )
            ],
        )
        child_fv = lv3.fact_versions[0]

        out, inc = edges_for(session, child_fv.id)
        assert len(out) == 1
        assert out[0].target_fv_id == parent_fv_id
        assert out[0].edge_kind == "derived_from"
        assert inc == []

        out_parent, inc_parent = edges_for(session, parent_fv_id)
        assert out_parent == []
        assert len(inc_parent) == 1
        assert inc_parent[0].source_fv_id == child_fv.id


def test_record_fact_version_rejects_forward_reference():
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        ghost_id = uuid.uuid4()  # a fact-version that doesn't exist

        with pytest.raises(ForwardReferenceError) as excinfo:
            create_layer_version(
                session,
                canonical,
                fact_specs=[
                    FactSpec(
                        content={"claim": "depends on a ghost"},
                        weight=80,
                        edges_to=(ghost_id,),
                    )
                ],
            )
        assert str(ghost_id) in str(excinfo.value)


def test_no_self_loop_in_edges():
    """The DB-level CHECK constraint forbids source_fv_id == target_fv_id."""
    from sqlalchemy.exc import IntegrityError

    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        lv2 = create_layer_version(
            session,
            canonical,
            fact_specs=[FactSpec(content={"claim": "lonely"}, weight=80)],
        )
        fv = lv2.fact_versions[0]

    with pytest.raises(IntegrityError), session_scope() as session:
        session.add(
            FactVersionEdge(source_fv_id=fv.id, target_fv_id=fv.id, edge_kind="derived_from")
        )


def test_fact_version_bound_to_layer_version():
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        lv2 = create_layer_version(
            session,
            canonical,
            fact_specs=[FactSpec(content={"claim": "x"}, weight=80)],
        )
        fv = lv2.fact_versions[0]
        assert fv.layer_version_id == lv2.id
        assert fv.layer_version.layer_id == canonical.id


def test_existing_fact_can_get_new_version_in_later_snapshot():
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        lv2 = create_layer_version(
            session,
            canonical,
            fact_specs=[FactSpec(content={"claim": "v1 content"}, weight=80)],
        )
        fact_id = lv2.fact_versions[0].fact_id

        lv3 = create_layer_version(
            session,
            canonical,
            fact_specs=[
                FactSpec(
                    fact_id=fact_id,
                    content={"claim": "v2 content"},
                    weight=80,
                )
            ],
        )
        new_fv = lv3.fact_versions[0]
        assert new_fv.fact_id == fact_id
        assert new_fv.version == 2

        # And the fact now has two versions in total.
        fact = session.get(Fact, fact_id)
        assert len(fact.versions) == 2


def test_record_fact_version_rejects_unknown_edge_kind():
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        lv2 = create_layer_version(
            session,
            canonical,
            fact_specs=[FactSpec(content={"claim": "x"}, weight=80)],
        )
        parent_fv = lv2.fact_versions[0]
        # Create a separate fact to receive the bad edge.
        fact = Fact(layer_id=canonical.id)
        session.add(fact)
        session.flush()
        lv_ref = get_layer_version(session, canonical, 1)
        with pytest.raises(ValueError, match="unknown edge_kind"):
            record_fact_version(
                session,
                fact,
                content={"claim": "bad edge"},
                weight=80,
                layer_version=lv_ref,
                edges_to=(parent_fv.id,),
                edge_kind="bogus",
            )


def test_fact_version_v1_pinned_to_layer_v1_via_seeding_only():
    """After plain seeding, no fact-versions exist, but the v1 layer-versions do."""
    with session_scope() as session:
        seed_default_layers(session)
        fv_count = session.scalar(select(func.count()).select_from(FactVersion))
        assert fv_count == 0
        lv_count = session.scalar(select(func.count()).select_from(LayerVersion))
        assert lv_count == len(DEFAULT_LAYERS)
