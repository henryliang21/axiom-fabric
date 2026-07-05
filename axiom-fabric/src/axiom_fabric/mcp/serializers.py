"""Pure ORM-instance -> JSON-safe dict helpers for the MCP layer.

These run *inside* a session scope (so relationship access is cheap) and return
plain dicts with UUIDs stringified and datetimes ISO-formatted, ready to hand
back over MCP. They hold no data-access logic of their own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from axiom_fabric.cost import ChangeCost
from axiom_fabric.cost import is_stale as _is_stale
from axiom_fabric.facts import RETRACTION_NOTE
from axiom_fabric.models import (
    Fact,
    FactSource,
    FactVersion,
    FactVersionEdge,
    Layer,
    LayerVersion,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def serialize_layer(layer: Layer) -> dict[str, Any]:
    return {
        "id": str(layer.id),
        "name": layer.name,
        "display_name": layer.display_name,
        "weight": layer.weight,
        "ordinal": layer.ordinal,
        "version_count": len(layer.versions),
        "created_at": _iso(layer.created_at),
    }


def serialize_layer_version(lv: LayerVersion) -> dict[str, Any]:
    return {
        "id": str(lv.id),
        "layer_id": str(lv.layer_id),
        "version": lv.version,
        "weight": lv.weight,
        "ordinal": lv.ordinal,
        "notes": lv.notes,
        "created_at": _iso(lv.created_at),
    }


def is_retracted(fv: FactVersion | None) -> bool:
    """A fact-version is a tombstone if it carries the retraction note, or has
    weight 0 with empty content (the shape `retract_fact` writes)."""
    if fv is None:
        return False
    if fv.note == RETRACTION_NOTE:
        return True
    return fv.weight == 0 and not fv.content


def serialize_fact_version(fv: FactVersion) -> dict[str, Any]:
    return {
        "id": str(fv.id),
        "fact_id": str(fv.fact_id),
        "layer_version_id": str(fv.layer_version_id),
        "version": fv.version,
        "content": fv.content,
        "weight": fv.weight,
        "justification": fv.justification,
        "temperature": fv.temperature,
        "note": fv.note,
        "retracted": is_retracted(fv),
        "stale": _is_stale(fv),
        "stale_since": _iso(fv.stale_since),
        "created_at": _iso(fv.created_at),
    }


def serialize_fact(fact: Fact, *, latest_only: bool = False) -> dict[str, Any]:
    versions = list(fact.versions)  # ascending by version number
    latest = versions[-1] if versions else None
    out: dict[str, Any] = {
        "fact_id": str(fact.id),
        "layer_id": str(fact.layer_id),
        "layer": fact.layer.name if fact.layer is not None else None,
        "schema_ref": fact.schema_ref,
        "version_count": len(versions),
        "retracted": is_retracted(latest),
        "latest_version": serialize_fact_version(latest) if latest is not None else None,
        "created_at": _iso(fact.created_at),
    }
    if not latest_only:
        out["versions"] = [serialize_fact_version(fv) for fv in versions]
    return out


def serialize_edge(edge: FactVersionEdge) -> dict[str, Any]:
    return {
        "source_fv_id": str(edge.source_fv_id),
        "target_fv_id": str(edge.target_fv_id),
        "edge_kind": edge.edge_kind,
        "created_at": _iso(edge.created_at),
    }


def serialize_source(source: FactSource) -> dict[str, Any]:
    return {
        "id": str(source.id),
        "fact_id": str(source.fact_id),
        "kind": source.kind,
        "uri": source.uri,
        "params": source.params,
        "refresh_policy": source.refresh_policy,
        "ttl_seconds": source.ttl_seconds,
        "schedule_cron": source.schedule_cron,
        "last_refreshed_at": _iso(source.last_refreshed_at),
        "created_at": _iso(source.created_at),
    }


def serialize_change_cost(cost: ChangeCost, *, include_nodes: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "root_fact_version_id": str(cost.root_fact_version_id),
        "total": cost.total,
        "descendant_count": cost.descendant_count,
    }
    if include_nodes:
        out["nodes"] = [
            {
                "fact_version_id": str(n.fact_version_id),
                "fact_id": str(n.fact_id),
                "depth": n.depth,
                "weight": n.weight,
                "temperature": n.temperature,
                "penalty": n.penalty,
                "contribution": n.contribution,
            }
            for n in cost.nodes
        ]
    return out


def parse_uuid(value: str, *, field: str) -> UUID:
    """Parse a UUID string, raising a clear ValueError naming the offending field."""
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{field} is not a valid UUID: {value!r}") from exc
