"""Dynamic / sourced facts: attach, refresh (snapshot-on-refresh), policy, cascade."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from axiom_fabric.cost import list_stale_fact_versions
from axiom_fabric.db import session_scope
from axiom_fabric.facts import append_fact, get_fact
from axiom_fabric.layers import get_layer_by_name, seed_default_layers
from axiom_fabric.models import FactVersion
from axiom_fabric.sources import (
    FactSourceSpec,
    ResolverError,
    SourceAlreadyAttachedError,
    SourceNotConfiguredError,
    attach_source,
    detach_source,
    due_for_refresh,
    get_source,
    refresh_fact,
    refresh_if_due,
)


def double_resolver(params):
    """Module-level callable used by the `python` resolver test."""
    return {"v": params["x"] * 2}


def _fact(content=None, weight=10, layer="living"):
    with session_scope() as session:
        seed_default_layers(session)
        target = get_layer_by_name(session, layer)
        fv = append_fact(session, target, content=content or {}, weight=weight)
        return fv.fact_id


def _version_count(session, fact_id):
    return session.scalar(
        select(func.count()).select_from(FactVersion).where(FactVersion.fact_id == fact_id)
    )


def test_attach_get_detach_roundtrip():
    fid = _fact()
    with session_scope() as session:
        fact = get_fact(session, fid)
        attach_source(session, fact, FactSourceSpec(kind="inline", params={"value": {"x": 1}}))
    with session_scope() as session:
        source = get_source(session, get_fact(session, fid))
        assert source is not None and source.kind == "inline"
    with session_scope() as session:
        assert detach_source(session, get_fact(session, fid)) is True
    with session_scope() as session:
        assert get_source(session, get_fact(session, fid)) is None


def test_attach_twice_rejected():
    fid = _fact()
    with session_scope() as session:
        attach_source(
            session, get_fact(session, fid), FactSourceSpec(kind="inline", params={"value": {}})
        )
    with session_scope() as session, pytest.raises(SourceAlreadyAttachedError):
        attach_source(
            session, get_fact(session, fid), FactSourceSpec(kind="inline", params={"value": {}})
        )


def test_ttl_policy_requires_ttl_seconds():
    fid = _fact()
    with session_scope() as session, pytest.raises(ValueError, match="ttl_seconds"):
        attach_source(
            session, get_fact(session, fid), FactSourceSpec(kind="inline", refresh_policy="ttl")
        )


def test_refresh_appends_snapshot_with_provenance():
    fid = _fact(content={"inv": 0})
    with session_scope() as session:
        attach_source(
            session,
            get_fact(session, fid),
            FactSourceSpec(
                kind="inline", params={"value": {"inv": 47}}, refresh_policy="ttl", ttl_seconds=60
            ),
        )
    with session_scope() as session:
        fv = refresh_fact(session, get_fact(session, fid))
        assert fv.version == 2
        assert fv.content == {"inv": 47}
        assert fv.note == "sourced refresh"
        assert fv.justification["source"]["kind"] == "inline"
        assert fv.justification["fetched_at"] is not None
        assert fv.justification["fresh_until"] is not None  # ttl policy sets it


def test_inline_resolver_requires_value():
    fid = _fact()
    with session_scope() as session:
        attach_source(session, get_fact(session, fid), FactSourceSpec(kind="inline", params={}))
    with session_scope() as session, pytest.raises(ResolverError, match="params\\['value'\\]"):
        refresh_fact(session, get_fact(session, fid))


def test_python_resolver():
    fid = _fact()
    with session_scope() as session:
        attach_source(
            session,
            get_fact(session, fid),
            FactSourceSpec(kind="python", uri=f"{__name__}:double_resolver", params={"x": 21}),
        )
    with session_scope() as session:
        fv = refresh_fact(session, get_fact(session, fid))
        assert fv.content == {"v": 42}


def test_unwired_kind_raises_on_refresh():
    fid = _fact()
    with session_scope() as session:
        attach_source(session, get_fact(session, fid), FactSourceSpec(kind="http", uri="https://x"))
    with session_scope() as session, pytest.raises(ResolverError, match="no resolver wired"):
        refresh_fact(session, get_fact(session, fid))


def test_refresh_without_source_raises():
    fid = _fact()
    with session_scope() as session, pytest.raises(SourceNotConfiguredError):
        refresh_fact(session, get_fact(session, fid))


def test_refresh_cascades_staleness_to_descendants():
    # sourced fact S with a downstream fact D derived from S.v1
    with session_scope() as session:
        seed_default_layers(session)
        living = get_layer_by_name(session, "living")
        s = append_fact(session, living, content={"inv": 0}, weight=10)
        d = append_fact(
            session, living, content={"note": "depends on inv"}, weight=10, edges_to=(s.id,)
        )
        sid, did = s.fact_id, d.id
    with session_scope() as session:
        attach_source(
            session,
            get_fact(session, sid),
            FactSourceSpec(kind="inline", params={"value": {"inv": 99}}),
        )
    with session_scope() as session:
        refresh_fact(session, get_fact(session, sid))
    with session_scope() as session:
        assert {fv.id for fv in list_stale_fact_versions(session)} == {did}


def test_due_for_refresh_policies():
    fid = _fact()
    now = datetime.now(UTC)
    with session_scope() as session:
        on_read = attach_source(
            session,
            get_fact(session, fid),
            FactSourceSpec(kind="inline", params={"value": {}}, refresh_policy="on_read"),
        )
        assert due_for_refresh(on_read, now=now) is True

    fid2 = _fact()
    with session_scope() as session:
        ttl = attach_source(
            session,
            get_fact(session, fid2),
            FactSourceSpec(
                kind="inline", params={"value": {}}, refresh_policy="ttl", ttl_seconds=60
            ),
        )
        assert due_for_refresh(ttl, now=now) is True  # never refreshed
        ttl.last_refreshed_at = now
        assert due_for_refresh(ttl, now=now + timedelta(seconds=30)) is False
        assert due_for_refresh(ttl, now=now + timedelta(seconds=61)) is True

    fid3 = _fact()
    with session_scope() as session:
        manual = attach_source(
            session,
            get_fact(session, fid3),
            FactSourceSpec(kind="inline", params={"value": {}}, refresh_policy="manual"),
        )
        assert due_for_refresh(manual, now=now) is False


def test_refresh_if_due_dedups_unchanged():
    fid = _fact(content={"inv": 0})
    with session_scope() as session:
        attach_source(
            session,
            get_fact(session, fid),
            FactSourceSpec(
                kind="inline", params={"value": {"inv": 5}}, refresh_policy="ttl", ttl_seconds=60
            ),
        )
    with session_scope() as session:
        refresh_fact(session, get_fact(session, fid))  # -> v2 {inv:5}
        assert _version_count(session, fid) == 2
    later = datetime.now(UTC) + timedelta(seconds=61)
    with session_scope() as session:
        # due again, but value unchanged -> no new version, last_refreshed bumped
        refresh_if_due(session, get_fact(session, fid), now=later)
        assert _version_count(session, fid) == 2
