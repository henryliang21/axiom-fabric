"""Change cost and cascade staleness over the fact-version derivation DAG.

Two governance primitives, both computed from `fact_version_edges`:

- **Change cost** — before altering a fact-version, `change_cost` walks its
  descendant subtree (everything derived from it, transitively) and sums
  ``sum(weight x depth x temperature_penalty)``. The caller compares "rewrite a
  foundational rule" against "rewrite a leaf" *before* committing: it is a
  branch cost, not a point cost.

- **Cascade staleness** — when a fact-version is superseded or retracted, every
  downstream fact-version is flipped **stale** (`mark_subtree_stale`), never
  silently re-pinned. Staleness is a mutable governance annotation on the
  otherwise-immutable fact-version; it never touches `content`, so pinned
  generations still replay identically. Layer-version staleness is a *derived
  view*: a layer-version is stale iff any fact-version it pins is stale.

The descendant walk is a single dialect-agnostic recursive CTE — the same query
runs on SQLite and Postgres.

Temperature penalty: `temperature` is generation confidence in [0, 1]. It acts
as a change-cost *multiplier*, so facts the model was uncertain about (low
confidence) are cheaper to rewrite. A missing temperature defaults to 1.0 — the
most conservative (most expensive to change), so unknown-confidence facts are
never treated as cheap.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, literal, select, update
from sqlalchemy.orm import Session

from axiom_fabric.models import FactVersion, FactVersionEdge

# Multiplier used when a fact-version carries no temperature (confidence unknown):
# treat it as fully confident, i.e. most expensive to change.
DEFAULT_TEMPERATURE_PENALTY = 1.0


def _now() -> datetime:
    return datetime.now(UTC)


def temperature_penalty(temperature: float | None) -> float:
    """Confidence-as-multiplier: the temperature itself, or 1.0 when unknown."""
    return DEFAULT_TEMPERATURE_PENALTY if temperature is None else float(temperature)


@dataclass(frozen=True)
class Descendant:
    fact_version_id: uuid.UUID
    fact_id: uuid.UUID
    depth: int
    weight: int
    temperature: float | None


@dataclass(frozen=True)
class CostNode:
    fact_version_id: uuid.UUID
    fact_id: uuid.UUID
    depth: int
    weight: int
    temperature: float | None
    penalty: float
    contribution: float


@dataclass(frozen=True)
class ChangeCost:
    root_fact_version_id: uuid.UUID
    total: float
    descendant_count: int
    nodes: list[CostNode]


def _descendants_query(root_id: uuid.UUID):
    """A recursive CTE selecting (fv_id, depth) for every descendant of `root_id`.

    An edge points source_fv_id -> target_fv_id ("source derived_from target"), so
    the descendants of X — everything derived from X — are found by walking edges
    from target to source. A node reachable by several paths is collapsed to its
    shortest depth (its most direct dependency distance).
    """
    edges = FactVersionEdge.__table__

    base = (
        select(
            edges.c.source_fv_id.label("fv_id"),
            literal(1).label("depth"),
        )
        .where(edges.c.target_fv_id == root_id)
        .cte(name="descendants", recursive=True)
    )
    step = select(
        edges.c.source_fv_id,
        (base.c.depth + 1).label("depth"),
    ).select_from(edges.join(base, edges.c.target_fv_id == base.c.fv_id))
    walk = base.union(step)  # UNION dedupes identical (fv_id, depth) rows

    return (
        select(walk.c.fv_id, func.min(walk.c.depth).label("depth"))
        .group_by(walk.c.fv_id)
        .subquery()
    )


def descendants(session: Session, fv_id: uuid.UUID) -> list[Descendant]:
    """Every fact-version transitively derived from `fv_id`, with its depth."""
    walk = _descendants_query(fv_id)
    fv = FactVersion.__table__
    rows = session.execute(
        select(fv.c.id, fv.c.fact_id, walk.c.depth, fv.c.weight, fv.c.temperature)
        .select_from(walk.join(fv, fv.c.id == walk.c.fv_id))
        .order_by(walk.c.depth, fv.c.id)
    ).all()
    return [
        Descendant(
            fact_version_id=row.id,
            fact_id=row.fact_id,
            depth=int(row.depth),
            weight=int(row.weight),
            temperature=row.temperature,
        )
        for row in rows
    ]


def change_cost(session: Session, fv_id: uuid.UUID) -> ChangeCost:
    """Cost of altering `fv_id`, summed over its descendant subtree.

    ``sum(weight x depth x temperature_penalty)`` — the root itself is excluded
    (you are choosing where to alter the truth, so the cost is the blast radius on
    everything derived from it).
    """
    nodes: list[CostNode] = []
    total = 0.0
    for d in descendants(session, fv_id):
        penalty = temperature_penalty(d.temperature)
        contribution = d.weight * d.depth * penalty
        total += contribution
        nodes.append(
            CostNode(
                fact_version_id=d.fact_version_id,
                fact_id=d.fact_id,
                depth=d.depth,
                weight=d.weight,
                temperature=d.temperature,
                penalty=penalty,
                contribution=contribution,
            )
        )
    return ChangeCost(
        root_fact_version_id=fv_id,
        total=total,
        descendant_count=len(nodes),
        nodes=nodes,
    )


def is_stale(fv: FactVersion | None) -> bool:
    return fv is not None and fv.stale_since is not None


def mark_subtree_stale(
    session: Session, fv_id: uuid.UUID, *, now: datetime | None = None
) -> list[uuid.UUID]:
    """Flip every descendant of `fv_id` stale. Returns the ids newly marked.

    Idempotent: already-stale descendants keep their original `stale_since` and
    are not returned. The root `fv_id` is not marked — only what derives from it.
    """
    stamp = now or _now()
    target_ids = [d.fact_version_id for d in descendants(session, fv_id)]
    if not target_ids:
        return []
    already = set(
        session.execute(
            select(FactVersion.id).where(
                FactVersion.id.in_(target_ids),
                FactVersion.stale_since.is_not(None),
            )
        ).scalars()
    )
    to_mark = [tid for tid in target_ids if tid not in already]
    if not to_mark:
        return []
    session.execute(
        update(FactVersion).where(FactVersion.id.in_(to_mark)).values(stale_since=stamp)
    )
    return to_mark


def clear_stale(session: Session, fv_id: uuid.UUID) -> bool:
    """Mark a single fact-version fresh again (a resolved-by-review decision).

    Returns True if it was stale and is now cleared, False if it was already fresh
    or does not exist.
    """
    fv = session.get(FactVersion, fv_id)
    if fv is None or fv.stale_since is None:
        return False
    fv.stale_since = None
    session.flush()
    return True


def list_stale_fact_versions(session: Session) -> list[FactVersion]:
    """Every currently-stale fact-version, oldest staleness first."""
    return list(
        session.execute(
            select(FactVersion)
            .where(FactVersion.stale_since.is_not(None))
            .order_by(FactVersion.stale_since)
        )
        .scalars()
        .all()
    )


def layer_version_is_stale(session: Session, layer_version_id: uuid.UUID) -> bool:
    """A layer-version is stale iff any fact-version it pins is stale (derived view)."""
    return (
        session.scalar(
            select(func.count())
            .select_from(FactVersion)
            .where(
                FactVersion.layer_version_id == layer_version_id,
                FactVersion.stale_since.is_not(None),
            )
        )
        or 0
    ) > 0
