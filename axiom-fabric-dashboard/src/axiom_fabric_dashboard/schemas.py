"""Pydantic response models for the dashboard API.

These mirror the detached dataclasses in `axiom_fabric.graph` and exist only to
serialize them to JSON (and to document the API via OpenAPI). No business logic
lives here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from axiom_fabric.graph import (
    EdgeInfo,
    FactNode,
    GraphSnapshot,
    LayerNode,
    LayerVersionInfo,
)
from axiom_fabric.models import FactVersionEdge


class FactVersionSchema(BaseModel):
    id: uuid.UUID
    version: int
    weight: int
    content: dict
    layer_version_id: uuid.UUID
    justification: dict | None
    temperature: float | None
    note: str | None
    created_at: datetime


class FactSchema(BaseModel):
    id: uuid.UUID
    layer_id: uuid.UUID
    schema_ref: str | None
    created_at: datetime
    latest_version_id: uuid.UUID | None
    versions: list[FactVersionSchema]

    @classmethod
    def from_node(cls, node: FactNode) -> FactSchema:
        return cls(
            id=node.id,
            layer_id=node.layer_id,
            schema_ref=node.schema_ref,
            created_at=node.created_at,
            latest_version_id=node.latest.id if node.latest else None,
            versions=[
                FactVersionSchema(
                    id=v.id,
                    version=v.version,
                    weight=v.weight,
                    content=v.content,
                    layer_version_id=v.layer_version_id,
                    justification=v.justification,
                    temperature=v.temperature,
                    note=v.note,
                    created_at=v.created_at,
                )
                for v in node.versions
            ],
        )


class LayerVersionSchema(BaseModel):
    id: uuid.UUID
    version: int
    weight: int
    ordinal: int
    notes: str | None
    created_at: datetime

    @classmethod
    def from_info(cls, info: LayerVersionInfo) -> LayerVersionSchema:
        return cls(
            id=info.id,
            version=info.version,
            weight=info.weight,
            ordinal=info.ordinal,
            notes=info.notes,
            created_at=info.created_at,
        )


class LayerSchema(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str | None
    weight: int
    ordinal: int
    created_at: datetime
    versions: list[LayerVersionSchema]
    facts: list[FactSchema]

    @classmethod
    def from_node(cls, node: LayerNode) -> LayerSchema:
        return cls(
            id=node.id,
            name=node.name,
            display_name=node.display_name,
            weight=node.weight,
            ordinal=node.ordinal,
            created_at=node.created_at,
            versions=[LayerVersionSchema.from_info(v) for v in node.versions],
            facts=[FactSchema.from_node(f) for f in node.facts],
        )


class EdgeSchema(BaseModel):
    source_fv_id: uuid.UUID
    target_fv_id: uuid.UUID
    edge_kind: str
    created_at: datetime

    @classmethod
    def from_info(cls, info: EdgeInfo) -> EdgeSchema:
        return cls(
            source_fv_id=info.source_fv_id,
            target_fv_id=info.target_fv_id,
            edge_kind=info.edge_kind,
            created_at=info.created_at,
        )

    @classmethod
    def from_edge(cls, edge: FactVersionEdge) -> EdgeSchema:
        return cls(
            source_fv_id=edge.source_fv_id,
            target_fv_id=edge.target_fv_id,
            edge_kind=edge.edge_kind,
            created_at=edge.created_at,
        )


class GraphSchema(BaseModel):
    layers: list[LayerSchema]
    edges: list[EdgeSchema]
    fact_count: int
    fact_version_count: int

    @classmethod
    def from_snapshot(cls, snap: GraphSnapshot) -> GraphSchema:
        return cls(
            layers=[LayerSchema.from_node(layer) for layer in snap.layers],
            edges=[EdgeSchema.from_info(e) for e in snap.edges],
            fact_count=snap.fact_count,
            fact_version_count=snap.fact_version_count,
        )


class HealthSchema(BaseModel):
    status: str  # "ok" | "uninitialized" | "error"
    initialized: bool
    database_backend: str  # "sqlite" | "postgresql" | ...
    revision: str | None
    layer_count: int | None
    message: str


class FactVersionEdgesSchema(BaseModel):
    fact_version_id: uuid.UUID
    outgoing: list[EdgeSchema]  # this version was derived from these
    incoming: list[EdgeSchema]  # these were derived from this version
