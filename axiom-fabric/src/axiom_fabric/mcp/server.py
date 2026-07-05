"""The Axiom Fabric MCP server.

A thin protocol adapter: every tool opens one `session_scope()`, calls an
existing repository function from `axiom_fabric.layers` / `.facts` / `.graph`,
and serializes the result. No SQL, no data-access logic of its own.

Read tools are always registered. Write tools are registered only when
`allow_writes=True` (set by `af-mcp serve --allow-writes` or
`AF_MCP_ALLOW_WRITES=1`), so a read-only server never exposes mutation to the
agent.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from axiom_fabric.config import get_settings
from axiom_fabric.cost import change_cost, list_stale_fact_versions
from axiom_fabric.db import session_scope
from axiom_fabric.facts import (
    append_fact,
    append_fact_version,
    edges_for,
    get_fact,
    get_fact_version,
    list_facts,
    retract_fact,
)
from axiom_fabric.layers import (
    create_layer,
    get_layer_by_name,
    list_layer_versions,
    list_layers,
)
from axiom_fabric.mcp import serializers as S
from axiom_fabric.mcp.guide import read_agent_guide
from axiom_fabric.migrate import ensure_schema, is_initialized
from axiom_fabric.sources import (
    FactSourceSpec,
    attach_source,
    get_source,
    refresh_fact,
)

SERVER_NAME = "axiom-fabric"

_INSTRUCTIONS = (
    "Axiom Fabric is a versioned, append-only truth ledger. Read the relevant "
    "facts before acting, and (when write tools are available) record new "
    "conclusions as facts with provenance. Fetch the `axiom_fabric_usage` prompt "
    "for the full guide. Never mutate or delete — updates and retractions append "
    "new versions."
)


def _coerce_content(content: Any) -> dict[str, Any]:
    """Accept a JSON object (dict). Tolerate a JSON-encoded string from clients
    that can't send structured args. Reject anything that isn't an object."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"content must be a JSON object; could not parse string: {exc}") from exc
    if not isinstance(content, dict):
        raise ValueError(f"content must be a JSON object (dict), got {type(content).__name__}")
    return content


def _elicit_setup_enabled() -> bool:
    """Whether a not-yet-created store should be confirmed via elicitation (opt-in).

    Off by default: the zero-setup path silently creates a local SQLite store on
    first use. Set AF_MCP_ELICIT_SETUP=1 to instead route first-time setup through
    the interactive `setup_store` tool.
    """
    return os.environ.get("AF_MCP_ELICIT_SETUP", "").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def _ensured_session() -> Iterator[Session]:
    """Open a DB session, lazily creating/migrating the store on first use.

    Auto-init runs here rather than at server startup so a missing or unreachable
    database surfaces as an actionable tool error the agent can relay — not a dead
    server that only shows as 'failed' in the client's /mcp panel.

    When AF_MCP_ELICIT_SETUP is set, a not-yet-created store is *not* auto-created;
    the agent is told to call `setup_store`, which confirms SQLite-vs-Postgres via
    MCP elicitation first.
    """
    if _elicit_setup_enabled() and not is_initialized():
        raise ValueError(
            "No Axiom Fabric store exists here yet. Call the `setup_store` tool first "
            "to create one (it confirms a local SQLite store or points you to Postgres)."
        )
    try:
        ensure_schema()
    except Exception as exc:
        url = get_settings().database_url
        raise ValueError(
            f"Axiom Fabric could not initialize its database at {url!r}: {exc}. "
            "For SQLite, check the directory is writable; for Postgres, check the server "
            "is reachable and AF_DATABASE_URL is correct."
        ) from exc
    with session_scope() as session:
        yield session


class _StoreSetupChoice(BaseModel):
    backend: str = Field(
        default="sqlite",
        description=(
            "'sqlite' to create a local store in this directory now, or 'postgres' "
            "if you will instead point AF_DATABASE_URL at an external Postgres "
            "database (no local SQLite store is created in that case)."
        ),
    )


async def _setup_store(ctx: Context) -> dict[str, Any]:
    """Initialize the Axiom Fabric store for this directory. If none exists yet, asks (via elicitation) whether to create a local SQLite store here or use external Postgres, then migrates accordingly. Idempotent: a no-op if a store already exists."""
    url = get_settings().database_url
    if is_initialized():
        return {"status": "already_initialized", "database_url": url, "created": False}

    is_sqlite = url.startswith("sqlite")
    choice = "sqlite"
    elicited = False
    try:
        result = await ctx.elicit(
            message=(
                f"No Axiom Fabric store exists yet (configured URL: {url}). "
                "Create a local SQLite store here now, or switch to Postgres?"
            ),
            schema=_StoreSetupChoice,
        )
        elicited = True
        if result.action == "accept" and result.data is not None:
            choice = (result.data.backend or "sqlite").strip().lower()
        elif result.action in ("decline", "cancel"):
            return {
                "status": "cancelled",
                "created": False,
                "message": "Store setup cancelled; no database was created.",
            }
    except Exception:
        # Client can't elicit — fall back to the safe default for the configured URL.
        choice = "sqlite" if is_sqlite else "postgres"

    if choice.startswith("p") and is_sqlite:
        return {
            "status": "needs_postgres_config",
            "created": False,
            "message": (
                "To use Postgres, set AF_DATABASE_URL to your postgresql+psycopg://... "
                "URL in this project's .mcp.json (env block) and restart the MCP server, "
                "then call a tool again."
            ),
        }

    try:
        fresh = ensure_schema()
    except Exception as exc:
        raise ValueError(f"Could not initialize the store at {url!r}: {exc}") from exc
    return {
        "status": "initialized" if fresh else "already_initialized",
        "database_url": url,
        "created": bool(fresh),
        "elicited": elicited,
    }


def build_server(allow_writes: bool = False) -> FastMCP:
    server = FastMCP(SERVER_NAME, instructions=_INSTRUCTIONS)

    # ---- Prompt: how to use the store -------------------------------------
    @server.prompt(
        name="axiom_fabric_usage",
        title="How to use Axiom Fabric",
        description="Guidance for using Axiom Fabric as a versioned fact store.",
    )
    def axiom_fabric_usage() -> str:
        return read_agent_guide()

    # ---- Read tools (always available) ------------------------------------
    def list_all_layers() -> list[dict[str, Any]]:
        """List every layer (policy bucket) with its weight, ordinal, and version count, ordered foundational-first."""
        with _ensured_session() as session:
            return [S.serialize_layer(layer) for layer in list_layers(session)]

    def list_facts_tool(
        layer: str | None = None,
        latest_only: bool = True,
        include_retracted: bool = False,
    ) -> list[dict[str, Any]]:
        """List facts, optionally filtered to one layer. Returns each fact's latest version by default; set latest_only=False for full history, include_retracted=True to include tombstoned facts."""
        with _ensured_session() as session:
            target = None
            if layer is not None:
                target = get_layer_by_name(session, layer)
                if target is None:
                    raise ValueError(f"No such layer: {layer!r}. Call list_layers to see available layers.")
            facts = list_facts(session, target)
            out = []
            for fact in facts:
                serialized = S.serialize_fact(fact, latest_only=latest_only)
                if serialized["retracted"] and not include_retracted:
                    continue
                out.append(serialized)
            return out

    def get_fact_tool(fact_id: str) -> dict[str, Any]:
        """Get one fact identity with its full version history by fact UUID."""
        fid = S.parse_uuid(fact_id, field="fact_id")
        with _ensured_session() as session:
            fact = get_fact(session, fid)
            if fact is None:
                raise ValueError(f"No fact with id {fact_id}")
            return S.serialize_fact(fact, latest_only=False)

    def get_fact_version_tool(fv_id: str) -> dict[str, Any]:
        """Get one specific fact-version (full content + justification) by its UUID."""
        vid = S.parse_uuid(fv_id, field="fv_id")
        with _ensured_session() as session:
            fv = get_fact_version(session, vid)
            if fv is None:
                raise ValueError(f"No fact-version with id {fv_id}")
            return S.serialize_fact_version(fv)

    def get_fact_edges_tool(fv_id: str) -> dict[str, Any]:
        """Get the derivation edges for a fact-version: {outgoing: what it derives from, incoming: what derives from it}."""
        vid = S.parse_uuid(fv_id, field="fv_id")
        with _ensured_session() as session:
            outgoing, incoming = edges_for(session, vid)
            return {
                "fact_version_id": fv_id,
                "outgoing": [S.serialize_edge(e) for e in outgoing],
                "incoming": [S.serialize_edge(e) for e in incoming],
            }

    def get_layer_history_tool(layer_name: str) -> dict[str, Any]:
        """Get a layer's snapshot history: every layer-version, oldest first."""
        with _ensured_session() as session:
            layer = get_layer_by_name(session, layer_name)
            if layer is None:
                raise ValueError(f"No such layer: {layer_name!r}. Call list_layers to see available layers.")
            return {
                "layer": S.serialize_layer(layer),
                "versions": [S.serialize_layer_version(lv) for lv in list_layer_versions(session, layer)],
            }

    def search_facts_tool(query: str, layer: str | None = None) -> list[dict[str, Any]]:
        """Find facts whose latest content contains `query` (case-insensitive substring match over the JSON content). Non-semantic; use to locate facts by keyword."""
        needle = query.lower()
        with _ensured_session() as session:
            target = None
            if layer is not None:
                target = get_layer_by_name(session, layer)
                if target is None:
                    raise ValueError(f"No such layer: {layer!r}. Call list_layers to see available layers.")
            out = []
            for fact in list_facts(session, target):
                latest = fact.versions[-1] if fact.versions else None
                if latest is None or S.is_retracted(latest):
                    continue
                if needle in json.dumps(latest.content, default=str).lower():
                    out.append(S.serialize_fact(fact, latest_only=True))
            return out

    def change_cost_tool(fv_id: str) -> dict[str, Any]:
        """Price a change to a fact-version: sum(weight x depth x temperature) over everything derived from it (its descendant subtree). Use before updating/retracting a fact to see the blast radius - high total = a foundational truth, low/zero = a leaf."""
        vid = S.parse_uuid(fv_id, field="fv_id")
        with _ensured_session() as session:
            fv = get_fact_version(session, vid)
            if fv is None:
                raise ValueError(f"No fact-version with id {fv_id}")
            return S.serialize_change_cost(change_cost(session, vid))

    def list_stale_tool() -> list[dict[str, Any]]:
        """List every fact-version currently flagged stale — its derivation was superseded or retracted upstream and may no longer hold. Each is a candidate for re-derivation (append a new version) or review."""
        with _ensured_session() as session:
            return [S.serialize_fact_version(fv) for fv in list_stale_fact_versions(session)]

    def get_fact_source_tool(fact_id: str) -> dict[str, Any]:
        """Get the dynamic source configured on a fact (kind, refresh policy, last refresh), or {configured: false} if it is a static fact."""
        fid = S.parse_uuid(fact_id, field="fact_id")
        with _ensured_session() as session:
            fact = get_fact(session, fid)
            if fact is None:
                raise ValueError(f"No fact with id {fact_id}")
            source = get_source(session, fact)
            if source is None:
                return {"fact_id": fact_id, "configured": False}
            return {"configured": True, **S.serialize_source(source)}

    for fn, name in (
        (_setup_store, "setup_store"),
        (list_all_layers, "list_layers"),
        (list_facts_tool, "list_facts"),
        (get_fact_tool, "get_fact"),
        (get_fact_version_tool, "get_fact_version"),
        (get_fact_edges_tool, "get_fact_edges"),
        (get_layer_history_tool, "get_layer_history"),
        (search_facts_tool, "search_facts"),
        (change_cost_tool, "change_cost"),
        (list_stale_tool, "list_stale"),
        (get_fact_source_tool, "get_fact_source"),
    ):
        server.add_tool(fn, name=name)

    if not allow_writes:
        return server

    # ---- Write tools (only when allow_writes) -----------------------------
    def create_layer_tool(
        name: str,
        weight: int,
        ordinal: int,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a new layer (policy bucket). weight 0-100 is its change-cost gravity; ordinal sets order (lower = more foundational); both name and ordinal must be unique."""
        with _ensured_session() as session:
            layer = create_layer(
                session,
                name=name,
                weight=weight,
                ordinal=ordinal,
                display_name=display_name,
            )
            return S.serialize_layer(layer)

    def create_fact_tool(
        layer: str,
        content: dict[str, Any],
        weight: int | None = None,
        edges_to: list[str] | None = None,
        edge_kind: str = "derived_from",
        note: str | None = None,
        schema_ref: str | None = None,
    ) -> dict[str, Any]:
        """Create a new fact (its v1 version) in `layer`. content is a JSON object. weight defaults to the layer's weight. edges_to lists upstream fact-version UUIDs this fact derives from (they must already exist)."""
        parsed = _coerce_content(content)
        edge_ids = [S.parse_uuid(e, field="edges_to") for e in (edges_to or [])]
        with _ensured_session() as session:
            target = get_layer_by_name(session, layer)
            if target is None:
                raise ValueError(f"No such layer: {layer!r}. Create it first with create_layer.")
            fv = append_fact(
                session,
                target,
                content=parsed,
                weight=weight if weight is not None else target.weight,
                edges_to=edge_ids,
                edge_kind=edge_kind,
                note=note,
                schema_ref=schema_ref,
            )
            return S.serialize_fact_version(fv)

    def update_fact_tool(
        fact_id: str,
        content: dict[str, Any],
        weight: int | None = None,
        edges_to: list[str] | None = None,
        edge_kind: str = "derived_from",
        note: str | None = None,
    ) -> dict[str, Any]:
        """Append a NEW version to an existing fact (append-only — the prior version is preserved). content is a JSON object. weight carries forward from the prior version unless given."""
        fid = S.parse_uuid(fact_id, field="fact_id")
        parsed = _coerce_content(content)
        edge_ids = [S.parse_uuid(e, field="edges_to") for e in (edges_to or [])]
        with _ensured_session() as session:
            fact = get_fact(session, fid)
            if fact is None:
                raise ValueError(f"No fact with id {fact_id}")
            if weight is None:
                prior = fact.versions[-1] if fact.versions else None
                resolved_weight = prior.weight if prior is not None else fact.layer.weight
            else:
                resolved_weight = weight
            fv = append_fact_version(
                session,
                fact,
                content=parsed,
                weight=resolved_weight,
                edges_to=edge_ids,
                edge_kind=edge_kind,
                note=note,
            )
            return S.serialize_fact_version(fv)

    def retract_fact_tool(fact_id: str, note: str | None = None) -> dict[str, Any]:
        """Retract a fact: append a tombstone version (weight 0, empty content). Append-only — prior versions remain for audit."""
        fid = S.parse_uuid(fact_id, field="fact_id")
        with _ensured_session() as session:
            fact = get_fact(session, fid)
            if fact is None:
                raise ValueError(f"No fact with id {fact_id}")
            fv = retract_fact(session, fact, note=note)
            return S.serialize_fact_version(fv)

    def attach_source_tool(
        fact_id: str,
        kind: str,
        uri: str | None = None,
        params: dict[str, Any] | None = None,
        refresh_policy: str = "manual",
        ttl_seconds: int | None = None,
        schedule_cron: str | None = None,
    ) -> dict[str, Any]:
        """Make a fact dynamic by attaching a source (kind: inline | python | sql | http | mcp_tool). Refreshing it later appends a new snapshot version with fetch provenance. inline carries its value in params['value']; python resolves uri 'module:callable'. refresh_policy 'ttl' needs ttl_seconds."""
        fid = S.parse_uuid(fact_id, field="fact_id")
        with _ensured_session() as session:
            fact = get_fact(session, fid)
            if fact is None:
                raise ValueError(f"No fact with id {fact_id}")
            source = attach_source(
                session,
                fact,
                FactSourceSpec(
                    kind=kind,
                    uri=uri,
                    params=params,
                    refresh_policy=refresh_policy,
                    ttl_seconds=ttl_seconds,
                    schedule_cron=schedule_cron,
                ),
            )
            return S.serialize_source(source)

    def refresh_fact_tool(fact_id: str) -> dict[str, Any]:
        """Fetch a sourced fact's current value and append it as a NEW snapshot version (append-only; the prior version stays pinned for reproducibility). This cascades staleness to anything derived from the prior version."""
        fid = S.parse_uuid(fact_id, field="fact_id")
        with _ensured_session() as session:
            fact = get_fact(session, fid)
            if fact is None:
                raise ValueError(f"No fact with id {fact_id}")
            fv = refresh_fact(session, fact)
            return S.serialize_fact_version(fv)

    for fn, name in (
        (create_layer_tool, "create_layer"),
        (create_fact_tool, "create_fact"),
        (update_fact_tool, "update_fact"),
        (retract_fact_tool, "retract_fact"),
        (attach_source_tool, "attach_source"),
        (refresh_fact_tool, "refresh_fact"),
    ):
        server.add_tool(fn, name=name)

    return server
