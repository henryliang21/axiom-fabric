"""Dynamic / sourced facts: snapshot-on-refresh, never live-resolve.

A sourced fact tracks a changing external value (a DB row, an API response, a
scraped page). Refreshing it *fetches once* and appends a new FactVersion whose
justification records fetch provenance (`source`, `fetched_at`, `fresh_until`);
it never reaches across the boundary at read time. Consequences:

- **Reproducibility.** A generation grounded in `inventory=47` replays
  identically tomorrow even if the upstream now reads `12` — it is pinned to the
  snapshot version, not a live value.
- **Uniformity.** Reads, cost, and cascade staleness reuse the ordinary
  fact-version + edge path. Static and dynamic facts are indistinguishable to
  consumers, and because a refresh appends a new version it cascades staleness to
  descendants exactly like any other supersession.

`kind` selects the resolver that performs the fetch. `inline` and `python` ship
by default; `sql` / `http` / `mcp_tool` are recognized but not wired — register
one with :func:`register_resolver` (kept behind a seam so no networked I/O or
extra dependency lands in the core until a deployment needs it).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from axiom_fabric.facts import append_fact_version
from axiom_fabric.models import (
    REFRESH_POLICIES,
    SOURCE_KINDS,
    Fact,
    FactSource,
    FactVersion,
)

# A resolver takes the configured source and returns the freshly-fetched content
# (a JSON object). It performs the fetch only; snapshotting is the caller's job.
Resolver = Callable[[FactSource], dict]


class SourceError(ValueError):
    """Base class for sourced-fact configuration/resolution errors."""


class SourceAlreadyAttachedError(SourceError):
    """A fact already has a source; detach it before attaching another."""


class SourceNotConfiguredError(SourceError):
    """Refresh was requested for a fact that has no source."""


class ResolverError(SourceError):
    """The resolver for a source kind failed or is not wired in this build."""


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware_utc(value: datetime) -> datetime:
    """Coerce to tz-aware UTC. SQLite stores `DateTime(timezone=True)` naively and
    reads it back without a tzinfo, so timestamps round-tripped through it must be
    treated as UTC before arithmetic against `_now()`."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---- resolver registry -----------------------------------------------------


def _inline_resolver(source: FactSource) -> dict:
    """Return the value carried directly in `params['value']` (no external fetch).

    Useful for manually-maintained-but-versioned facts and for tests/demos.
    """
    params = source.params or {}
    if "value" not in params:
        raise ResolverError("inline source requires params['value'] (a JSON object)")
    value = params["value"]
    if not isinstance(value, dict):
        raise ResolverError("inline source params['value'] must be a JSON object")
    return value


def _python_resolver(source: FactSource) -> dict:
    """Import a dotted `module:callable` from `uri` and call it with `params`.

    The callable receives the source's params dict and must return a JSON object.
    """
    uri = (source.uri or "").strip()
    module_path, sep, attr = uri.partition(":")
    if not sep or not module_path or not attr:
        raise ResolverError(f"python source uri must be 'module.path:callable', got {source.uri!r}")
    try:
        module = importlib.import_module(module_path)
        func = getattr(module, attr)
    except (ImportError, AttributeError) as exc:
        raise ResolverError(f"could not import python resolver {uri!r}: {exc}") from exc
    result = func(source.params or {})
    if not isinstance(result, dict):
        raise ResolverError(
            f"python resolver {uri!r} must return a JSON object (dict), got {type(result).__name__}"
        )
    return result


def _unwired_resolver(kind: str) -> Resolver:
    def resolver(_source: FactSource) -> dict:
        raise ResolverError(
            f"no resolver wired for source kind {kind!r}; register one with "
            f"axiom_fabric.sources.register_resolver({kind!r}, fn)"
        )

    return resolver


_RESOLVERS: dict[str, Resolver] = {
    "inline": _inline_resolver,
    "python": _python_resolver,
    "sql": _unwired_resolver("sql"),
    "http": _unwired_resolver("http"),
    "mcp_tool": _unwired_resolver("mcp_tool"),
}


def register_resolver(kind: str, resolver: Resolver) -> None:
    """Register (or override) the resolver for a source kind.

    This is the seam for wiring networked fetchers (`sql`, `http`, `mcp_tool`) or
    a project-specific fetcher without changing the core.
    """
    if kind not in SOURCE_KINDS:
        raise ValueError(f"unknown source kind: {kind!r}; expected one of {SOURCE_KINDS}")
    _RESOLVERS[kind] = resolver


def get_resolver(kind: str) -> Resolver:
    try:
        return _RESOLVERS[kind]
    except KeyError:
        raise ValueError(f"unknown source kind: {kind!r}; expected one of {SOURCE_KINDS}") from None


# ---- attach / inspect / detach ---------------------------------------------


@dataclass
class FactSourceSpec:
    kind: str
    uri: str | None = None
    params: dict | None = None
    refresh_policy: str = "manual"
    ttl_seconds: int | None = None
    schedule_cron: str | None = None


def _validate_spec(spec: FactSourceSpec) -> None:
    if spec.kind not in SOURCE_KINDS:
        raise ValueError(f"unknown source kind: {spec.kind!r}; expected one of {SOURCE_KINDS}")
    if spec.refresh_policy not in REFRESH_POLICIES:
        raise ValueError(
            f"unknown refresh policy: {spec.refresh_policy!r}; expected one of {REFRESH_POLICIES}"
        )
    if spec.refresh_policy == "ttl" and not spec.ttl_seconds:
        raise ValueError("refresh_policy 'ttl' requires a positive ttl_seconds")
    if spec.ttl_seconds is not None and spec.ttl_seconds < 0:
        raise ValueError("ttl_seconds must be >= 0")


def attach_source(session: Session, fact: Fact, spec: FactSourceSpec) -> FactSource:
    """Attach a source to `fact`. Raises if one is already attached."""
    _validate_spec(spec)
    existing = get_source(session, fact)
    if existing is not None:
        raise SourceAlreadyAttachedError(
            f"fact {fact.id} already has a {existing.kind} source; detach it first"
        )
    source = FactSource(
        fact_id=fact.id,
        kind=spec.kind,
        uri=spec.uri,
        params=spec.params,
        refresh_policy=spec.refresh_policy,
        ttl_seconds=spec.ttl_seconds,
        schedule_cron=spec.schedule_cron,
    )
    session.add(source)
    session.flush()
    return source


def get_source(session: Session, fact: Fact) -> FactSource | None:
    return session.scalar(select(FactSource).where(FactSource.fact_id == fact.id))


def detach_source(session: Session, fact: Fact) -> bool:
    """Remove a fact's source. Returns True if one was removed."""
    source = get_source(session, fact)
    if source is None:
        return False
    session.delete(source)
    session.flush()
    return True


# ---- freshness + refresh ---------------------------------------------------


def due_for_refresh(source: FactSource, *, now: datetime | None = None) -> bool:
    """Whether an *automatic* refresh is due under the source's policy.

    - `on_read`  → always (the read path should snapshot every time).
    - `ttl`      → never fetched yet, or older than `ttl_seconds`.
    - `manual`   → never automatically (only an explicit `refresh_fact`).
    - `scheduled`→ never in-process; an external scheduler calls `refresh_fact`
                   on the cron. (Cron evaluation is intentionally out of scope.)
    """
    if source.refresh_policy == "on_read":
        return True
    if source.refresh_policy == "ttl":
        if source.last_refreshed_at is None:
            return True
        stamp = now or _now()
        age = stamp - _as_aware_utc(source.last_refreshed_at)
        return age >= timedelta(seconds=source.ttl_seconds or 0)
    return False


def _latest_version(session: Session, fact: Fact) -> FactVersion | None:
    """The current latest fact-version, queried fresh (the ORM `fact.versions`
    collection can be stale within a session that already appended a version)."""
    return session.scalar(
        select(FactVersion)
        .where(FactVersion.fact_id == fact.id)
        .order_by(FactVersion.version.desc())
        .limit(1)
    )


def _resolved_weight(session: Session, fact: Fact) -> int:
    prior = _latest_version(session, fact)
    if prior is not None:
        return prior.weight
    layer = fact.layer
    return layer.weight if layer is not None else 0


def refresh_fact(
    session: Session,
    fact: Fact,
    *,
    now: datetime | None = None,
    skip_if_unchanged: bool = False,
) -> FactVersion:
    """Fetch the source's current value and append it as a new snapshot version.

    Records fetch provenance in the new version's justification and updates the
    source's `last_refreshed_at`. Because this appends a version, descendants of
    the prior version are cascade-marked stale like any supersession.

    With `skip_if_unchanged=True`, an identical fetch does not append a duplicate
    version — `last_refreshed_at` is still bumped (so TTL resets) and the current
    latest version is returned.
    """
    source = get_source(session, fact)
    if source is None:
        raise SourceNotConfiguredError(f"fact {fact.id} has no source to refresh")

    stamp = now or _now()
    content = get_resolver(source.kind)(source)

    fresh_until = None
    if source.refresh_policy == "ttl" and source.ttl_seconds:
        fresh_until = stamp + timedelta(seconds=source.ttl_seconds)

    latest = _latest_version(session, fact)
    if skip_if_unchanged and latest is not None and latest.content == content:
        source.last_refreshed_at = stamp
        session.flush()
        return latest

    justification = {
        "source": {"kind": source.kind, "uri": source.uri, "params": source.params},
        "fetched_at": stamp.isoformat(),
        "fresh_until": fresh_until.isoformat() if fresh_until else None,
    }
    fv = append_fact_version(
        session,
        fact,
        content=content,
        weight=_resolved_weight(session, fact) if latest is None else latest.weight,
        justification=justification,
        note="sourced refresh",
    )
    source.last_refreshed_at = stamp
    session.flush()
    return fv


def refresh_if_due(
    session: Session, fact: Fact, *, now: datetime | None = None
) -> FactVersion | None:
    """Refresh only when the policy says a snapshot is due; else return None.

    Deduplicates unchanged fetches so polling an `on_read`/`ttl` source does not
    accrete identical versions.
    """
    source = get_source(session, fact)
    if source is None or not due_for_refresh(source, now=now):
        return None
    return refresh_fact(session, fact, now=now, skip_if_unchanged=True)


# Kept importable for callers that need the fact's layer weight default.
__all__ = [
    "FactSourceSpec",
    "Resolver",
    "ResolverError",
    "SourceAlreadyAttachedError",
    "SourceError",
    "SourceNotConfiguredError",
    "attach_source",
    "detach_source",
    "due_for_refresh",
    "get_resolver",
    "get_source",
    "refresh_fact",
    "refresh_if_due",
    "register_resolver",
]
