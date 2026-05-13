# Axiom Fabric

The Versioned Truth Layer for Agentic AI.

Axiom Fabric is a versioned, hierarchical **truth ledger** that sits between an application and the LLMs reasoning over it. The goal is to close the gap between **stochastic LLM output** and **deterministic application truth**: rather than re-prompting the model with a static system prompt and hoping it stays consistent, the application maintains an explicit, versioned record of what is true *right now*, and every LLM call is grounded in a specific pinned snapshot of that record.

## Why

LLMs are great at generating plausible language and bad at remaining consistent with the application's actual state across time. A typical agentic system patches this with retrieval, system prompts, and ad-hoc validation — but those leave no audit trail, no way to ask "where did this 'fact' come from?", and no way to *change* a foundational rule and have everything downstream of it re-evaluate.

Axiom Fabric treats truth as a first-class, versioned data structure with:

- An **append-only history** of every fact change.
- An explicit **dependency graph** — every generated fact links back to the exact upstream facts and layer-versions that produced it.
- A **change cost** — rewriting a foundational rule is expensive (and the system can tell you *how* expensive) before you commit.
- A **gated promotion path** — LLM output never silently becomes truth; promotion is an explicit, traceable step.

## How "truth" is organized

The data model is a small set of tables that compose into a versioned tree.

### Layers, Facts, and FactVersions

```text
Layer ──┬─< Fact ──< FactVersion
        │              │
        │              ├─ content (JSON)
        │              ├─ weight (0–100)
        │              ├─ justification (parent layer-versions + fact-versions)
        │              └─ temperature (generation confidence)
        │
        └─ name, weight, ordinal
```

- **Layer** — a container with a name, an `ordinal` (foundational layers come first), and a default `weight` (the "gravity" of facts in that layer, 0–100).
- **Fact** — a stable identity bound to a layer. Holds no content itself; it's the handle the version chain hangs off.
- **FactVersion** — an append-only snapshot. Each version has:
  - `content` — JSON payload. Free-form text claims and typed schema-bound records share the same column (`schema_ref` on `Fact` is the optional pointer to a schema).
  - `weight` — change-cost gravity at this version.
  - `justification` — JSON list of upstream layer-versions and fact-versions this fact was derived from. This is the dependency graph.
  - `temperature` — LLM confidence (log-prob) at generation time, where available. Acts as a *change-cost multiplier*: facts the model was uncertain about are cheaper to rewrite later.

Old versions are never overwritten — every truth change is an append.

### Default layer hierarchy

Three layers ship out of the box (users may add or remove their own per project, minimum of one required):

| Layer       | Default weight | Purpose                                                          |
| ----------- | -------------- | ---------------------------------------------------------------- |
| `canonical` | 90             | Immutable laws - physics of the system, schema invariants.       |
| `episodic`  | 30             | Interaction history - every prompt/response logged here.         |
| `living`    | 10             | LLM-generated facts - candidates that may become future truth.   |

Higher weight = higher gravity = more expensive to overturn.

### Integrating with an LLM (the read / generate / promote loop)

1. **Read.** The application assembles a context window from a chosen set of layer-versions, pinned to specific versions. That context regulates the LLM call.
2. **Generate.** The LLM produces output grounded in that pinned context. Reproducibility is intentional — same pinned facts → byte-identical context.
3. **Promote (selective).** Chosen outputs become candidate facts in the **next layer up** from the highest layer present in the source generation. The new fact's `justification` records the exact upstream layer-versions and fact-versions it was derived from.
4. **Re-version.** When a foundational layer is re-created, every higher layer-version that pinned the prior version is marked **stale** — never silently re-pinned. Re-evaluation is a deliberate, cost-priced act.
5. **Cost.** `Σ (weight × depth × temperature)` over the dependency *subtree* — so the caller is choosing where to alternate the truth, not just which row to edit.

### Dynamic / sourced facts (planned)

The model above is static — every `FactVersion` is a frozen JSON snapshot, written once. But applications routinely need to ground LLM calls on values that change between calls: inventory levels, customer status, the latest scraped page, today's weather, a row from a transactional database. The chosen design for this is **snapshot-on-refresh**, not live-resolve at generation time.

Resolving live would break the reproducibility guarantee — replaying yesterday's prompt with today's inventory would produce a different answer with no audit trail of what the model actually saw. Snapshotting keeps every generation pinned to an exact, recoverable view of the world.

The schema extension is additive:

- A new `FactSource` row (1:1, optional, attached to `Fact`) carries the source `kind` (`sql` / `http` / `python` / `mcp_tool`), the connection URI, params, and a refresh policy (`on_read` / `ttl` / `manual` / `scheduled`) with `ttl_seconds` or `schedule_cron`.
- Each refresh writes a new `FactVersion` whose `justification` records fetch provenance — `source`, `fetched_at`, `fresh_until` — instead of (or alongside) the upstream-derivation links that static facts carry.
- Reads, version pinning, cost calculus, and cascade-staleness mechanics all reuse the existing `FactVersion` path. Dynamic and static facts are indistinguishable to consumers.
- MCP becomes a natural ingress: external MCP tools (`inventory.get(sku)`, `weather.now(city)`) are valid `FactSource.kind`s, so the planned MCP server work and the dynamic-facts work reinforce each other — the truth store consumes MCP for sourcing *and* exposes MCP for reading.

Refresh-driven snapshots land after first-class layer versions and the cascade-staleness mechanics, since "this sourced fact just got refreshed" propagates downstream staleness through the same machinery as "this canonical layer got re-versioned."

## Architecture

### Storage backends

Axiom Fabric uses SQLAlchemy + Alembic and supports two dialects out of the box. Pick via the `AF_DATABASE_URL` environment variable.

| URL                        | Backend           | Durable? | When to use                                            |
| -------------------------- | ----------------- | -------- | ------------------------------------------------------ |
| `postgresql+psycopg://...` | PostgreSQL        | yes      | Production; multi-process; JSONB-indexed queries       |
| `sqlite:///./af.db`        | SQLite, file      | yes      | Local dev, single-user CLI / TUI, embedded use         |
| `sqlite:///:memory:`       | SQLite, in-memory | no       | Tests, REPL, ephemeral agents                          |

On Postgres, `content` and `justification` are stored as `JSONB` (indexable, queryable). On SQLite they degrade to plain `JSON` (TEXT) — fine for the common path, but queries that lean on JSONB operators will only run on Postgres.

SQLite specifics handled by the engine layer:

- `:memory:` URLs share one in-memory DB across sessions in the same process (via `StaticPool`).
- `PRAGMA foreign_keys=ON` is enforced via a connect-time event listener so the schema's `ON DELETE CASCADE` / `RESTRICT` semantics actually apply.
- No new dependency — `sqlite3` is in Python's stdlib.

Default `AF_DATABASE_URL` is `postgresql+psycopg://postgres@localhost:5432/henryliang`. Override per environment:

```bash
export AF_DATABASE_URL='sqlite:///./af.db'
```

### Module layout

```text
src/axiom_fabric/
├── __init__.py        # package metadata
├── cli.py             # `af` CLI (Typer) — presentation layer only
├── config.py          # AF_DATABASE_URL + Settings (pydantic-settings)
├── db.py              # Engine, sessionmaker, session_scope context manager
├── layers.py          # Repository functions over Layer (pure, Session-taking)
├── migrate.py         # Programmatic Alembic upgrade / downgrade
├── models.py          # SQLAlchemy 2.0 ORM models — Layer / Fact / FactVersion
└── migrations/        # Alembic migrations (dialect-agnostic)
```

The core invariant: **CLI / TUI / MCP frontends are presentation layers**. They never construct SQL or hold a `Session` longer than a request. All data access goes through repository functions in `layers.py` (and forthcoming `facts.py`, `services/...`). This is what keeps the door open for alternative backends or a DTO boundary without locking us into a premature abstraction today.

## Integration points

### CLI

Installed as `af` (Typer-based). Currently:

```bash
af init                 # Run migrations + seed default layers
af init --skip-seed     # Migrations only
af layer list           # Show all layers, ordered by ordinal
```

Planned:

```bash
af layer history <name>            # Layer-version history
af layer version <name> <n>        # Inspect a specific layer-version
af generate --provider anthropic   # LLM call against assembled context
af promote <candidate>             # Commit a candidate fact into the next layer up
af cost <change>                   # Branch-cost preview before commit
af stale / af reevaluate           # Staleness inspection / re-evaluation
```

### Python API

The package is importable as `axiom_fabric` and consumers compose the repository functions directly:

```python
from axiom_fabric.db import session_scope
from axiom_fabric.layers import list_layers, seed_default_layers
from axiom_fabric.migrate import upgrade_to_head

upgrade_to_head()                       # Apply migrations idempotently
with session_scope() as session:
    seed_default_layers(session)        # No-op if already seeded
    for layer in list_layers(session):
        print(layer.name, layer.weight)
```

A curated public re-export surface (`axiom_fabric.__init__`) will land alongside the TUI / MCP frontends so callers don't have to reach into submodules.

### MCP server (planned)

The Model Context Protocol server is the canonical wire-level integration. An MCP-capable LLM client (Claude Desktop, Cursor, an agentic runner) will be able to browse and query the truth store as a structured resource, *without* the application doing prompt-stuffing.

Planned shape:

- **Resources:** `layers://`, `layers://<name>`, `layers://<name>/versions/<n>`, `facts://<id>`, `facts://<id>/versions/<n>`.
- **Read tools:** `list_layers`, `list_facts`, `get_fact_version`, `get_layer_history`, `query_by_layer`.
- **Write tools (gated behind a flag):** `promote_candidate`, `propose_change`, `reevaluate_stale`. The gateway enforces `Layer Weight` overrides and returns branch-cost on every proposed change.
- Same `axiom_fabric.layers` / `axiom_fabric.facts` repository functions back both the CLI and the MCP server — the MCP layer is a thin protocol adapter, not a re-implementation.

### TUI (planned)

A terminal UI for exploring layer history, inspecting fact lineage, and previewing branch cost before committing a re-version. Same repository backing as CLI and MCP.

## Status

- **DONE:** Core truth store on Postgres — `Layer` / `Fact` / `FactVersion` schema, `af init`, `af layer list`.
- **DONE:** SQLite supported as a second backend — in-memory and file modes, FK enforcement, dialect-agnostic JSON / UUID columns.
- **Next:** First-class layer versions — `layer_versions` table, history CLI, cascade-staleness mechanics.
- **Later — core loop:** Context assembly + LLM call, write-back loop with gated promotion, branch-cost + cascade re-evaluation, MCP server.
- **Later — sourced facts:** `FactSource` extension for dynamic data (SQL / HTTP / Python / MCP-tool), snapshot-on-refresh with TTL / cron / on-read policies, fetch provenance recorded in `justification`.
- **Later — beyond:** Dependency-directed backtracking, declarative agent blueprints, constrained decoding, durable execution, human-in-the-loop governance.

Treat anything beyond what's marked DONE as design-in-flight — the code is the authoritative source for what actually exists today.

## Setup

```bash
# Install (editable, with dev + optional LLM extras)
uv sync                                  # or: pip install -e '.[dev,llm]'

# Pick a backend
export AF_DATABASE_URL='sqlite:///./af.db'   # zero-setup local
# or leave unset to use Postgres at the default URL

# Run migrations + seed
uv run af init

# Browse
uv run af layer list
```

Requires Python `>=3.12`.

## License

MIT — see [`LICENSE`](./LICENSE).
