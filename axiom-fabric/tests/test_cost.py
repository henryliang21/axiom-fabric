"""Change cost + cascade staleness over the fact-version DAG."""

from __future__ import annotations

from axiom_fabric.cost import (
    change_cost,
    clear_stale,
    descendants,
    is_stale,
    layer_version_is_stale,
    list_stale_fact_versions,
    mark_subtree_stale,
)
from axiom_fabric.db import session_scope
from axiom_fabric.facts import (
    append_fact,
    append_fact_version,
    get_fact,
    get_fact_version,
    retract_fact,
)
from axiom_fabric.layers import get_layer_by_name, seed_default_layers


def _chain():
    """A <- B <- C (B derived_from A, C derived_from B). Returns their fv ids."""
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        living = get_layer_by_name(session, "living")
        a = append_fact(session, canonical, content={"c": "A"}, weight=90, temperature=1.0)
        b = append_fact(
            session, living, content={"c": "B"}, weight=40, edges_to=(a.id,), temperature=0.5
        )
        c = append_fact(session, living, content={"c": "C"}, weight=20, edges_to=(b.id,))
        return a.id, b.id, c.id, a.fact_id


def test_descendants_depths():
    a, b, c, _ = _chain()
    with session_scope() as session:
        ds = {d.fact_version_id: d.depth for d in descendants(session, a)}
    assert ds == {b: 1, c: 2}


def test_change_cost_sums_weight_depth_temperature():
    a, b, c, _ = _chain()
    with session_scope() as session:
        report = change_cost(session, a)
    # B: 40 x 1 x 0.5 = 20 ; C: 20 x 2 x 1.0 (no temp -> penalty 1) = 40
    assert report.total == 60.0
    assert report.descendant_count == 2
    contrib = {n.fact_version_id: n.contribution for n in report.nodes}
    assert contrib[b] == 20.0
    assert contrib[c] == 40.0


def test_leaf_change_is_free():
    _a, _b, c, _ = _chain()
    with session_scope() as session:
        report = change_cost(session, c)
    assert report.total == 0.0
    assert report.nodes == []


def test_update_cascades_staleness_to_descendants():
    _a, b, c, a_fact = _chain()
    with session_scope() as session:
        fact = get_fact(session, a_fact)
        append_fact_version(session, fact, content={"c": "A2"}, weight=90)
    with session_scope() as session:
        stale = {fv.id for fv in list_stale_fact_versions(session)}
    assert stale == {b, c}  # everything derived from the superseded A.v1


def test_retract_cascades_staleness():
    _a, b, c, a_fact = _chain()
    with session_scope() as session:
        retract_fact(session, get_fact(session, a_fact))
    with session_scope() as session:
        assert {fv.id for fv in list_stale_fact_versions(session)} == {b, c}


def test_new_fact_v1_does_not_cascade():
    with session_scope() as session:
        seed_default_layers(session)
        canonical = get_layer_by_name(session, "canonical")
        append_fact(session, canonical, content={"c": "solo"}, weight=90)
    with session_scope() as session:
        assert list_stale_fact_versions(session) == []


def test_mark_subtree_stale_is_idempotent_and_returns_new_marks():
    a, b, c, _ = _chain()
    with session_scope() as session:
        first = set(mark_subtree_stale(session, a))
        assert first == {b, c}
    with session_scope() as session:
        # already stale -> nothing newly marked
        assert mark_subtree_stale(session, a) == []


def test_clear_stale():
    a, b, _c, _ = _chain()
    with session_scope() as session:
        mark_subtree_stale(session, a)
    with session_scope() as session:
        assert clear_stale(session, b) is True
        assert is_stale(get_fact_version(session, b)) is False
    with session_scope() as session:
        # clearing an already-fresh version is a no-op
        assert clear_stale(session, b) is False


def test_layer_version_staleness_is_derived():
    a, b, _c, a_fact = _chain()
    with session_scope() as session:
        fact = get_fact(session, a_fact)
        append_fact_version(session, fact, content={"c": "A2"}, weight=90)
    with session_scope() as session:
        b_fv = get_fact_version(session, b)
        assert layer_version_is_stale(session, b_fv.layer_version_id) is True
        # A brand-new fact's own layer-version pins only a fresh version.
        a_fv = get_fact_version(session, a)
        assert layer_version_is_stale(session, a_fv.layer_version_id) is False
