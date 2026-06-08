"""FastAPI application for the Axiom Fabric dashboard.

This is a presentation layer only: every endpoint calls into `axiom_fabric`
repository functions (`load_graph`, `edges_for`, ...) and serializes the result.
It holds no data-access logic of its own.

The same database resolution as the `af` CLI applies — `AF_DATABASE_URL` / `.env`
in the current working directory — because we import the core's engine/session.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from axiom_fabric.config import get_settings
from axiom_fabric.db import get_engine, session_scope
from axiom_fabric.facts import edges_for
from axiom_fabric.graph import load_graph
from axiom_fabric.migrate import current_revision
from axiom_fabric.models import Layer
from axiom_fabric_dashboard.schemas import (
    EdgeSchema,
    FactVersionEdgesSchema,
    GraphSchema,
    HealthSchema,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

_SQLITE_PREFIX = "sqlite:///"


def _database_backend() -> str:
    """Scheme of the configured database, e.g. 'sqlite' or 'postgresql'."""
    url = get_settings().database_url
    return url.split("://", 1)[0].split("+", 1)[0] or "unknown"


def _database_location() -> str:
    """Where the configured DB actually lives — the resolved absolute path for a
    SQLite file (the common 'launched from the wrong directory' footgun), or the
    URL itself for in-memory / non-SQLite backends."""
    url = get_settings().database_url
    if url.startswith(_SQLITE_PREFIX) and ":memory:" not in url:
        return str(Path(url[len(_SQLITE_PREFIX) :]).resolve())
    return url


def _not_initialized_message() -> str:
    return (
        f"No initialized Axiom Fabric database at {_database_location()}. "
        "Run `af init` in that directory and launch af-dashboard from the same "
        "place, or set AF_DATABASE_URL to an absolute path so the location no "
        "longer depends on your working directory."
    )


def _check_health() -> HealthSchema:
    backend = _database_backend()

    # 1. Is the database reachable at all?
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        return HealthSchema(
            status="error",
            initialized=False,
            database_backend=backend,
            revision=None,
            layer_count=None,
            message=f"Cannot connect to the {backend} database at {_database_location()}: {exc}",
        )

    # 2. Has it been migrated?
    revision = current_revision()
    if revision is None:
        return HealthSchema(
            status="uninitialized",
            initialized=False,
            database_backend=backend,
            revision=None,
            layer_count=None,
            message=_not_initialized_message(),
        )

    # 3. Migrated store is usable even when empty — a clean `af init` leaves it
    #    with zero layers, which is a valid (if empty) state, not an error.
    with session_scope() as session:
        layer_count = session.scalar(select(func.count()).select_from(Layer)) or 0

    empty_note = (
        "Connected — empty store; no layers yet. Create one with `af layer create` "
        "or let an agent create them via the MCP server."
    )
    return HealthSchema(
        status="ok",
        initialized=True,
        database_backend=backend,
        revision=revision,
        layer_count=layer_count,
        message="Connected." if layer_count >= 1 else empty_note,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="Axiom Fabric Dashboard",
        version="0.0.1",
        description="Read-only view of the Axiom Fabric truth store.",
    )

    # Local tool: the Vite dev server (a different origin) needs to reach the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthSchema)
    def health() -> HealthSchema:
        return _check_health()

    @app.get("/api/graph", response_model=GraphSchema)
    def graph() -> GraphSchema:
        health_state = _check_health()
        if not health_state.initialized:
            raise HTTPException(status_code=503, detail=health_state.message)
        with session_scope() as session:
            snapshot = load_graph(session)
        return GraphSchema.from_snapshot(snapshot)

    @app.get(
        "/api/fact-versions/{fv_id}/edges",
        response_model=FactVersionEdgesSchema,
    )
    def fact_version_edges(fv_id: uuid.UUID) -> FactVersionEdgesSchema:
        with session_scope() as session:
            outgoing, incoming = edges_for(session, fv_id)
            return FactVersionEdgesSchema(
                fact_version_id=fv_id,
                outgoing=[EdgeSchema.from_edge(e) for e in outgoing],
                incoming=[EdgeSchema.from_edge(e) for e in incoming],
            )

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA at '/', or a placeholder if it hasn't been built."""
    index = STATIC_DIR / "index.html"
    if index.exists():
        # html=True serves index.html at '/'. Mounted last so /api/* wins.
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
        return

    @app.get("/", response_class=HTMLResponse)
    def placeholder() -> str:
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Axiom Fabric Dashboard</title></head>"
            "<body style='font-family:system-ui;max-width:40rem;margin:4rem auto;color:#222'>"
            "<h1>Axiom Fabric Dashboard</h1>"
            "<p>The API is running, but the frontend bundle has not been built.</p>"
            "<p>From <code>axiom-fabric-dashboard/frontend</code> run "
            "<code>npm install &amp;&amp; npm run build</code>, then restart "
            "<code>af-dashboard</code>.</p>"
            "<p>The API is live at <a href='/api/health'>/api/health</a> and "
            "<a href='/api/graph'>/api/graph</a>.</p>"
            "</body></html>"
        )


# Module-level instance for `uvicorn axiom_fabric_dashboard.app:app`.
app = create_app()
