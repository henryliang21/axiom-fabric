# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

Phase 1 of `plan.md` is complete. The repo is a `uv` workspace of two packages: **`axiom-fabric/`** — the core (Python 3.12+, SQLAlchemy 2.0 + Alembic, Typer CLI named `af`) under `axiom-fabric/src/axiom_fabric/` — and **`axiom-fabric-dashboard/`** — the web UI, which depends on the core and reuses its repository functions (scaffold only so far). The workspace root holds shared `ruff`/`pytest` config; each package has its own `pyproject.toml` and ships as a separate wheel. Schema is dialect-agnostic across SQLite (default, `sqlite:///./af.db`) and Postgres (opt-in via `AF_DATABASE_URL=postgresql+psycopg://...`). Current schema covers `layers`, `facts`, `fact_versions`; Phase 1.5 (first-class layer-versions + a `fact_version_edges` adjacency table) is the next slice.

See `plan.md` for the phased roadmap, `brief.md` for the design vision, and `build.md` for how to run it locally.

## Project vision

`axiom-fabric` is the working name for the **Truth Glue Framework** — a versioned truth layer for agentic AI. `brief.md` is the authoritative source for scope and intent. Key concepts:

- **Integration Paradox** — the problem being solved: stochastic LLM output vs. deterministic application "truth".
- **Versioned Truth Tree** — fact-versions carry `Layer Weight` (gravity, 0–100), `Justification` (structured links to upstream fact-versions), and `Temperature Influence` (log-prob confidence acting as a change-cost multiplier).
- **Truth Hierarchies** — Canonical (immutable laws), Episodic (interaction history), Living (LLM-generated facts that may become truth). Default set; users may add or remove layers per project.
- **Layers vs. edges are orthogonal.** Layer = *policy* attribute (write authority, default weight, promotion target, whether the LLM may mutate the fact). `fact_version_edges` = *derivation* DAG between specific fact-versions. Edges may cross layers freely — a Living fact-version directly justified by a Canonical fact-version is the normal case, not an exception.
- **Append-only ⇒ acyclic by construction.** A new fact-version can only cite upstream IDs that already exist, so `fact_version_edges` only point backward in time. Cycles at the version level are physically impossible. What looks like a cycle at the fact-identity level (A.v2 cites B.v1 which cites A.v1) is mutual refinement, not a bug.
- **Four integration priorities** from the brief, in brief's ordering: (1) MCP as the wire protocol exposing the Truth Ledger, (2) Cognitive Blueprints — declarative YAML/JSON for agent laws and layer weights, (3) Execution Gateways — authorization plane that intercepts LLM proposals and triggers dependency-directed backtracking, (4) Constraint Manifolds — constrained decoding to mask "untruthful" tokens at generation time.
- **Change Cost** governs backtracking: `Σ (Layer Weight × Depth × Temperature Penalty)`, summed over the descendant subtree in `fact_version_edges`. Layer-version staleness is a *derived view* over fact-version staleness — a layer-version is stale iff any fact-version it pins has been invalidated.

## Committed stack

- **Language:** Python 3.12+ (targeting 3.14).
- **ORM + migrations:** SQLAlchemy 2.0 + Alembic.
- **CLI:** Typer (`af`).
- **Storage:** SQLite default (`sqlite:///./af.db`, zero-setup); Postgres opt-in via `AF_DATABASE_URL`. Dialect-agnostic types (`Uuid`, `JSON().with_variant(JSONB, "postgresql")`); SQLite engine uses `StaticPool` for `:memory:` and a `PRAGMA foreign_keys=ON` listener.
- **Vector indexing (Phase 2+):** pgvector on Postgres, sqlite-vec on SQLite, behind a `VectorIndex` seam so the query path stays dialect-agnostic.
- **Cascade query (Phase 4):** dialect-agnostic recursive CTE over `fact_version_edges` — same query text runs on both backends.

Still proposed, not yet committed: Temporal.io (Phase 9 durable execution), Outlines/Guidance (Phase 8 constraint manifolds), MAEB store + HITL toggle (Phase 10 governance), Java SDKs for low-latency components. Confirm with the user before adopting any of these.

## Next steps named in the brief

1. **Truth Glue DSL** — YAML schema for canonical vs. living fact weights (Phase 2).
2. **MCP Mediator** — server translating app databases ↔ LLM context (Phase 5).
3. **Backtracking loop** — TMS reasoning wired into Temporal signals/queries (Phase 6 + 9).

If the user references "the DSL", "the mediator", or "the backtracking loop" without further context, these are what they mean.
