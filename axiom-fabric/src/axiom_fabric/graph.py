"""Whole-graph read model.

`load_graph` materializes the entire truth store — layers, their versions, the
facts in each layer, every fact-version, and all edges — into plain, detached
dataclasses. This is the shared read path for any frontend that wants to render
or traverse the graph (the dashboard today, the CLI later): it runs inside a
Session and returns DTOs, so callers never hold a Session open.

It is deliberately read-only and does no view-level interpretation (e.g. which
edges are "current"). That projection belongs to the consumer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from axiom_fabric.models import Fact, FactVersionEdge, Layer


@dataclass(frozen=True)
class FactVersionNode:
    id: uuid.UUID
    version: int
    weight: int
    content: dict
    layer_version_id: uuid.UUID
    justification: dict | None
    temperature: float | None
    note: str | None
    created_at: datetime


@dataclass(frozen=True)
class FactNode:
    id: uuid.UUID
    layer_id: uuid.UUID
    schema_ref: str | None
    created_at: datetime
    versions: list[FactVersionNode]  # ascending by version number

    @property
    def latest(self) -> FactVersionNode | None:
        return self.versions[-1] if self.versions else None


@dataclass(frozen=True)
class LayerVersionInfo:
    id: uuid.UUID
    version: int
    weight: int
    ordinal: int
    notes: str | None
    created_at: datetime


@dataclass(frozen=True)
class LayerNode:
    id: uuid.UUID
    name: str
    display_name: str | None
    weight: int
    ordinal: int
    created_at: datetime
    versions: list[LayerVersionInfo]  # ascending by version number
    facts: list[FactNode]


@dataclass(frozen=True)
class EdgeInfo:
    source_fv_id: uuid.UUID
    target_fv_id: uuid.UUID
    edge_kind: str
    created_at: datetime


@dataclass(frozen=True)
class GraphSnapshot:
    layers: list[LayerNode]  # ascending by ordinal (foundational first)
    edges: list[EdgeInfo]
    fact_count: int = field(default=0)
    fact_version_count: int = field(default=0)


def load_graph(session: Session) -> GraphSnapshot:
    """Read the entire truth store into detached dataclasses.

    Uses eager (`selectinload`) loading so the whole tree is fetched in a fixed
    number of queries regardless of how many layers/facts/versions exist.
    """
    layers = (
        session.execute(
            select(Layer)
            .order_by(Layer.ordinal)
            .options(
                selectinload(Layer.versions),
                selectinload(Layer.facts).selectinload(Fact.versions),
            )
        )
        .scalars()
        .all()
    )

    layer_nodes: list[LayerNode] = []
    fact_count = 0
    fact_version_count = 0
    for layer in layers:
        fact_nodes: list[FactNode] = []
        for fact in layer.facts:
            version_nodes = [
                FactVersionNode(
                    id=fv.id,
                    version=fv.version,
                    weight=fv.weight,
                    content=fv.content,
                    layer_version_id=fv.layer_version_id,
                    justification=fv.justification,
                    temperature=fv.temperature,
                    note=fv.note,
                    created_at=fv.created_at,
                )
                for fv in fact.versions
            ]
            fact_version_count += len(version_nodes)
            fact_count += 1
            fact_nodes.append(
                FactNode(
                    id=fact.id,
                    layer_id=fact.layer_id,
                    schema_ref=fact.schema_ref,
                    created_at=fact.created_at,
                    versions=version_nodes,
                )
            )
        layer_nodes.append(
            LayerNode(
                id=layer.id,
                name=layer.name,
                display_name=layer.display_name,
                weight=layer.weight,
                ordinal=layer.ordinal,
                created_at=layer.created_at,
                versions=[
                    LayerVersionInfo(
                        id=lv.id,
                        version=lv.version,
                        weight=lv.weight,
                        ordinal=lv.ordinal,
                        notes=lv.notes,
                        created_at=lv.created_at,
                    )
                    for lv in layer.versions
                ],
                facts=fact_nodes,
            )
        )

    edges = [
        EdgeInfo(
            source_fv_id=edge.source_fv_id,
            target_fv_id=edge.target_fv_id,
            edge_kind=edge.edge_kind,
            created_at=edge.created_at,
        )
        for edge in session.execute(select(FactVersionEdge)).scalars().all()
    ]

    return GraphSnapshot(
        layers=layer_nodes,
        edges=edges,
        fact_count=fact_count,
        fact_version_count=fact_version_count,
    )
