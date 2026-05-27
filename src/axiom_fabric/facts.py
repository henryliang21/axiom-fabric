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
from sqlalchemy.orm import Session

from axiom_fabric.models import (
    EDGE_KINDS,
    Fact,
    FactVersion,
    FactVersionEdge,
    Layer,
    LayerVersion,
)


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
