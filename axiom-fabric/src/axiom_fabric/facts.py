"""Service layer for fact-version and layer-version writes.

Two entry points cover Phase 1.5 needs:

- `create_layer_version` — atomic write of a new layer snapshot plus the
  fact-versions pinned to it.
- `record_fact_version` — append a single fact-version under an existing
  layer-version, projecting its justification into `fact_version_edges` and
  rejecting any forward reference to a not-yet-existing target.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from axiom_fabric.models import (
    EDGE_KINDS,
    Fact,
    FactVersion,
    FactVersionEdge,
    Layer,
    LayerVersion,
)

RETRACTION_NOTE = "retracted"


class ForwardReferenceError(ValueError):
    """Raised when an edge target_fv_id refers to a fact-version that does not yet exist.

    This is the entire mechanism preventing cycles in the fact-version DAG: every
    target must already be in the database at insert time, so edges only ever
    point backward in time.
    """


@dataclass
class FactSpec:
    """One entry in a layer-version snapshot.

    - `fact_id=None` creates a new Fact identity in the target layer.
    - `fact_id` set appends a new FactVersion to an existing Fact (which must
      live in the target layer).
    """

    content: dict
    weight: int
    fact_id: uuid.UUID | None = None
    schema_ref: str | None = None
    edges_to: Sequence[uuid.UUID] = field(default_factory=tuple)
    justification: dict | None = None
    temperature: float | None = None
    note: str | None = None


def _next_layer_version_number(session: Session, layer: Layer) -> int:
    current = session.scalar(
        select(func.coalesce(func.max(LayerVersion.version), 0)).where(
            LayerVersion.layer_id == layer.id
        )
    )
    return int(current or 0) + 1


def _next_fact_version_number(session: Session, fact: Fact) -> int:
    current = session.scalar(
        select(func.coalesce(func.max(FactVersion.version), 0)).where(
            FactVersion.fact_id == fact.id
        )
    )
    return int(current or 0) + 1


def _assert_targets_exist(session: Session, target_ids: Iterable[uuid.UUID]) -> None:
    ids = list(target_ids)
    if not ids:
        return
    found = set(
        session.execute(
            select(FactVersion.id).where(FactVersion.id.in_(ids))
        ).scalars()
    )
    missing = [tid for tid in ids if tid not in found]
    if missing:
        raise ForwardReferenceError(
            f"edge target_fv_id(s) do not exist: {[str(m) for m in missing]}"
        )


def create_layer_version(
    session: Session,
    layer: Layer,
    fact_specs: Sequence[FactSpec],
    *,
    notes: str | None = None,
    weight: int | None = None,
    ordinal: int | None = None,
) -> LayerVersion:
    """Create a new LayerVersion of `layer` and the FactVersions it contains.

    Pre-existing facts referenced by `fact_id` must already belong to `layer`.
    """
    new_version = _next_layer_version_number(session, layer)
    layer_version = LayerVersion(
        layer_id=layer.id,
        version=new_version,
        weight=weight if weight is not None else layer.weight,
        ordinal=ordinal if ordinal is not None else layer.ordinal,
        notes=notes,
    )
    session.add(layer_version)
    session.flush()

    for spec in fact_specs:
        if spec.fact_id is None:
            fact = Fact(layer_id=layer.id, schema_ref=spec.schema_ref)
            session.add(fact)
            session.flush()
        else:
            fact = session.get(Fact, spec.fact_id)
            if fact is None:
                raise ValueError(f"unknown fact_id: {spec.fact_id}")
            if fact.layer_id != layer.id:
                raise ValueError(
                    f"fact {fact.id} lives in layer {fact.layer_id}, "
                    f"not {layer.id} ({layer.name})"
                )
        record_fact_version(
            session,
            fact,
            content=spec.content,
            weight=spec.weight,
            layer_version=layer_version,
            edges_to=spec.edges_to,
            justification=spec.justification,
            temperature=spec.temperature,
            note=spec.note,
        )

    return layer_version


def record_fact_version(
    session: Session,
    fact: Fact,
    *,
    content: dict,
    weight: int,
    layer_version: LayerVersion,
    edges_to: Sequence[uuid.UUID] = (),
    edge_kind: str = "derived_from",
    justification: dict | None = None,
    temperature: float | None = None,
    note: str | None = None,
) -> FactVersion:
    """Append a new FactVersion to `fact` and project edges from its justification.

    Raises ForwardReferenceError if any target_fv_id in `edges_to` does not
    already exist in the database. That single check is the only thing keeping
    the fact-version DAG acyclic.
    """
    if edge_kind not in EDGE_KINDS:
        raise ValueError(f"unknown edge_kind: {edge_kind!r}; expected one of {EDGE_KINDS}")
    _assert_targets_exist(session, edges_to)

    fv = FactVersion(
        fact_id=fact.id,
        layer_version_id=layer_version.id,
        version=_next_fact_version_number(session, fact),
        content=content,
        weight=weight,
        justification=justification,
        temperature=temperature,
        note=note,
    )
    session.add(fv)
    session.flush()

    for target_id in edges_to:
        session.add(
            FactVersionEdge(
                source_fv_id=fv.id, target_fv_id=target_id, edge_kind=edge_kind
            )
        )
    session.flush()
    return fv


def list_facts(session: Session, layer: Layer | None = None) -> list[Fact]:
    """Return facts (optionally filtered to one layer) with their versions eagerly loaded."""
    stmt = select(Fact).options(selectinload(Fact.versions))
    if layer is not None:
        stmt = stmt.where(Fact.layer_id == layer.id)
    stmt = stmt.order_by(Fact.created_at)
    return list(session.execute(stmt).scalars().all())


def get_fact(session: Session, fact_id: uuid.UUID) -> Fact | None:
    return session.get(Fact, fact_id)


def append_fact(
    session: Session,
    layer: Layer,
    *,
    content: dict,
    weight: int,
    edges_to: Sequence[uuid.UUID] = (),
    note: str | None = None,
    schema_ref: str | None = None,
) -> FactVersion:
    """Create a new Fact in `layer` with its v1 FactVersion, wrapped in a fresh layer-version."""
    lv = create_layer_version(
        session,
        layer,
        fact_specs=[
            FactSpec(
                content=content,
                weight=weight,
                edges_to=edges_to,
                note=note,
                schema_ref=schema_ref,
            )
        ],
    )
    return lv.fact_versions[0]


def append_fact_version(
    session: Session,
    fact: Fact,
    *,
    content: dict,
    weight: int,
    edges_to: Sequence[uuid.UUID] = (),
    note: str | None = None,
) -> FactVersion:
    """Append a new version to `fact`, wrapped in a fresh layer-version of the fact's layer."""
    layer = session.get(Layer, fact.layer_id)
    if layer is None:  # FK guarantees this, but be explicit.
        raise ValueError(f"fact {fact.id} references missing layer {fact.layer_id}")
    lv = create_layer_version(
        session,
        layer,
        fact_specs=[
            FactSpec(
                fact_id=fact.id,
                content=content,
                weight=weight,
                edges_to=edges_to,
                note=note,
            )
        ],
    )
    return lv.fact_versions[0]


def retract_fact(
    session: Session,
    fact: Fact,
    *,
    note: str | None = None,
) -> FactVersion:
    """Append a tombstone fact-version: weight=0, empty content, note marks retraction.

    Append-only — the prior versions remain. Downstream readers should treat any
    fact whose latest version has note == RETRACTION_NOTE (or weight == 0 with
    no content) as no longer authoritative.
    """
    return append_fact_version(
        session,
        fact,
        content={},
        weight=0,
        note=note or RETRACTION_NOTE,
    )


def edges_for(session: Session, fv_id: uuid.UUID) -> tuple[list[FactVersionEdge], list[FactVersionEdge]]:
    """Return (outgoing, incoming) edges for a given fact-version id."""
    out = list(
        session.execute(
            select(FactVersionEdge).where(FactVersionEdge.source_fv_id == fv_id)
        )
        .scalars()
        .all()
    )
    inc = list(
        session.execute(
            select(FactVersionEdge).where(FactVersionEdge.target_fv_id == fv_id)
        )
        .scalars()
        .all()
    )
    return out, inc
