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

Default `AF_DATABASE_URL` is `sqlite:///./af.db` — a file-backed SQLite database created in the working directory on first `af init`, so the tool works with zero infrastructure out of the box. Override per environment to point at any supported backend:

```bash
export AF_DATABASE_URL='sqlite:///:memory:'   # ephemeral, e.g. tests
```

#### Connecting to PostgreSQL

For production or multi-process use, point `AF_DATABASE_URL` at a Postgres instance. The driver (`psycopg[binary]`) ships prebuilt wheels, so no `libpq` or build tools are required.

1. **Create a database** (once), using whatever credentials your server has:

   ```bash
   createdb axiom_fabric
   # or, via psql:  psql -U postgres -c 'CREATE DATABASE axiom_fabric;'
   ```

2. **Set the URL.** The shape is `postgresql+psycopg://<user>:<password>@<host>:<port>/<dbname>` (omit `:<password>` if your server trusts local connections):

   ```bash
   # macOS / Linux
   export AF_DATABASE_URL='postgresql+psycopg://postgres:secret@localhost:5432/axiom_fabric'
   ```

   ```powershell
   # Windows PowerShell
   $env:AF_DATABASE_URL = 'postgresql+psycopg://postgres:secret@localhost:5432/axiom_fabric'
   ```

   To make it durable rather than per-shell, put it in a `.env` file in the directory you run `af` from (read automatically by `pydantic-settings`, and gitignored). The repo ships a template — copy it and edit:

   ```bash
   cp .env.example .env          # macOS / Linux  (Windows: Copy-Item .env.example .env)
   ```

   ```
   # .env
   AF_DATABASE_URL=postgresql+psycopg://postgres:secret@localhost:5432/axiom_fabric
   ```

3. **Migrate + seed** against the new backend — same commands as SQLite:

   ```bash
   af init           # applies migrations to the Postgres database
   af layer list     # verify
   ```

On Postgres, `content` and `justification` use `JSONB`; the rest of the schema is identical across both backends.

### Repository layout

The repo is a `uv` workspace with two independently publishable packages:

```text
axiom-fabric-internal/                 # workspace root (shared ruff/pytest config)
├── pyproject.toml                     # [tool.uv.workspace] members
├── axiom-fabric/                      # ── package: axiom-fabric (core + cli + mcp) ──
│   ├── pyproject.toml                 #    the only thing `pip install axiom-fabric` pulls in
│   ├── alembic.ini
│   ├── tests/
│   └── src/axiom_fabric/
│       ├── cli.py             # `af` CLI (Typer) — presentation layer only
│       ├── config.py          # AF_DATABASE_URL + Settings (pydantic-settings)
│       ├── db.py              # Engine, sessionmaker, session_scope context manager
│       ├── layers.py          # Repository functions over Layer / LayerVersion
│       ├── facts.py           # Repository functions over Fact / FactVersion / edges
│       ├── migrate.py         # Programmatic Alembic upgrade / downgrade
│       ├── models.py          # SQLAlchemy 2.0 ORM models
│       └── migrations/        # Alembic migrations (dialect-agnostic)
└── axiom-fabric-dashboard/            # ── package: axiom-fabric-dashboard (web UI) ──
    ├── pyproject.toml                 #    depends on axiom-fabric; published separately
    └── src/axiom_fabric_dashboard/    #    never included in the core wheel
```

The core invariant: **CLI / dashboard / MCP frontends are presentation layers**. They never construct SQL or hold a `Session` longer than a request. All data access goes through repository functions in `layers.py` / `facts.py`. The dashboard package imports `axiom_fabric` and reuses those same functions — it adds no data-access logic of its own. This is what lets the core ship to PyPI on its own and keeps the door open for alternative frontends without a premature abstraction.

## Integration points

### CLI

Installed as `af` (Typer-based). Commands are grouped by noun (`layer`, `fact`); `init` is the only top-level verb. Every write resolves the database the same way as everything else in the system — `AF_DATABASE_URL` or `./af.db` in the working directory.

#### Initialization & diagnostics

| Command                  | What it does                                                                                  |
| ------------------------ | --------------------------------------------------------------------------------------------- |
| `af init`                | Apply migrations and seed the three default layers (idempotent).                              |
| `af init --skip-seed`    | Migrations only — leave layers empty (for fully custom hierarchies).                          |
| `af status`              | Show DB URL, schema revision, and row counts (layers, fact-versions, edges). Reports `not migrated` instead of crashing if `af init` hasn't run. |

#### Layers

| Command                                                                            | What it does                                                                       |
| ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `af layer create --name <slug> --weight 0-100 --ordinal <int> [--display <text>]`  | Add a custom layer + its v1 layer-version. Both `name` and `ordinal` must be unique. |
| `af layer list`                                                                    | All layers, ordered by ordinal (foundational first).                               |
| `af layer history <name>`                                                          | Every layer-version snapshot of `<name>`, oldest first.                            |
| `af layer version <name> <n>`                                                      | One layer-version's metadata + the fact-versions pinned to it.                     |

`layer update` and `layer retract` aren't exposed yet — both require cascade-staleness mechanics to be safe (changing a layer's weight invalidates every downstream fact-version's effective cost). Planned alongside Phase 1.5 / 2.

#### Facts

The truth ledger is append-only, which dictates the verb set:

- `create` adds a new fact identity (its v1 fact-version).
- `update` **appends** a new version to an existing fact — it does not mutate the prior version.
- `retract` appends a tombstone version (`weight=0`, `content={}`, `note="retracted"`) — the prior versions stay intact for audit.
- `list` is the table view; `show` drills into one fact identity; `version` drills into one specific fact-version (full content + edges); `edges` is a focused subset of `version` that prints only the edges.

| Command                                                | Required flags                       | Optional flags                                                                                                                                |
| ------------------------------------------------------ | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `af fact create`                                       | `--layer <name>`, `--content '<json>'` | `--weight 0-100` (default: layer's weight), `--edges-to <fv-uuid>` (repeatable), `--edge-kind <kind>` (default: `derived_from`), `--note <text>`, `--schema-ref <id>` |
| `af fact update`                                       | `--fact-id <uuid>`, `--content '<json>'` | `--weight 0-100` (default: carry the prior version's weight), `--edges-to <fv-uuid>` (repeatable), `--edge-kind <kind>` (default: `derived_from`), `--note <text>`   |
| `af fact retract`                                      | `--fact-id <uuid>`                   | `--note <text>` (default: `retracted`)                                                                                                        |
| `af fact list`                                         | —                                    | `--layer <name>`, `--latest-only` / `--all-versions` (default: latest-only)                                                                   |
| `af fact show <fact-id>`                               | positional `<fact-id>`               | —                                                                                                                                             |
| `af fact version <fv-uuid>`                            | positional `<fv-uuid>`               | —                                                                                                                                             |
| `af fact edges <fv-uuid>`                              | positional `<fv-uuid>`               | —                                                                                                                                             |

Notes on the flag shape:

- **`--content` is always inline JSON** and must parse to a JSON **object** (a dict). Strings, arrays, and scalars are rejected with a clear error. Quote it according to your shell — single quotes on POSIX, escaped double quotes inside double quotes on PowerShell.
- **`--edges-to` accepts a fact-version UUID** (an `fv-uuid`, not a `fact-id`). Targets must already exist — forward references are rejected at write time, which is the single mechanism preventing cycles in the derivation DAG.
- **`--edge-kind`** is one of `derived_from` (default), `evidence_of`, `refutes`, `supersedes`. All `--edges-to` UUIDs in a single command share the same kind; for mixed kinds, append multiple versions or split the write.
- **`--weight`** is bounded `0..100`. On `create` it defaults to the layer's policy weight; on `update` it carries forward from the prior version.
- **`af fact show`** prints the fact's layer, every version row, and the **full** latest content (no truncation, syntax-highlighted JSON). Use it when the `list` table preview chops things off.
- **`af fact version`** is the full per-version drill-down: full content + justification JSON, plus both edge directions. `af fact edges` prints only the edge tables (subset of `version`).

#### Worked example

```bash
af init

# Create a canonical fact and capture its fact-version UUID from the output.
af fact create \
    --layer canonical \
    --content '{"rule": "pricing_tiers", "free": 0, "pro": 10, "enterprise": 100}'
# -> Created fact d1c... (fv 7017b6be-..., v1, weight=90)

# Episodic fact cites the canonical one (cross-layer derivation edge).
af fact create \
    --layer episodic \
    --content '{"user": "alice", "event": "upgrade", "to": "pro"}' \
    --edges-to 7017b6be-...

# Append v2 to an existing fact.
af fact update \
    --fact-id d1c... \
    --content '{"rule": "pricing_tiers", "free": 0, "pro": 12, "enterprise": 100}' \
    --note "Pro tier raised $2"

# Retract a fact (tombstone, not deletion).
af fact retract --fact-id d1c... --note "Pricing rule rewritten as separate facts"

# Inspect.
af fact list --all-versions
af fact edges <fv-uuid>
```

A bigger end-to-end demo lives at `scripts/seed_demo_data.sh` — three layers, cross-layer edges, multi-version facts, and one retraction. Run it once for something interesting to view in the dashboard:

```bash
rm -f af.db                         # optional: clean slate
./scripts/seed_demo_data.sh
uv run af-dashboard                 # http://localhost:7373
```

#### Planned

```bash
af generate --provider anthropic    # LLM call against assembled context
af promote <candidate>              # Commit a candidate fact into the next layer up
af cost <change>                    # Branch-cost preview before commit
af stale / af reevaluate            # Staleness inspection / re-evaluation
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

### Web dashboard

A local, read-only web UI (the separate `axiom-fabric-dashboard` package) for exploring the truth store: facts grouped into layers, connected by their fact-version edges, with a stacked-card affordance for multi-version facts and a side panel showing version history and per-version lineage.

It's a FastAPI backend — a thin presentation layer over the same `axiom_fabric` repository functions (`load_graph`, `edges_for`), *not* a second data API — plus a Vite/React/React Flow frontend shipped prebuilt in the wheel. It resolves the database the same way the `af` CLI does (`AF_DATABASE_URL` or `./af.db` in the current directory). Read-only today; write actions become additive once the core's write APIs land.

#### Run from an installed package

```bash
pip install axiom-fabric-dashboard      # depends on axiom-fabric
af init                                 # in the directory holding your af.db
af-dashboard                            # opens http://localhost:7373
```

#### Run from this repo (workspace)

The frontend bundle is git-ignored (a build artifact); build it once, then serve:

```bash
# 1. Build the React + React Flow bundle into src/axiom_fabric_dashboard/static/
npm --prefix axiom-fabric-dashboard/frontend install
npm --prefix axiom-fabric-dashboard/frontend run build

# 2. Serve — uses the database in the current directory, same as `af`
uv run af-dashboard                     # opens http://localhost:7373
```

If you launch without building first, the page explains how — the API at `/api/health` and `/api/graph` is still live.

#### CLI flags and environment

| Flag / env var          | Default     | Effect                                                   |
| ----------------------- | ----------- | -------------------------------------------------------- |
| `--host`                | `127.0.0.1` | Interface to bind.                                       |
| `--port` / `$AF_DASHBOARD_PORT` | `7373` | Starting port; auto-increments if busy.            |
| `--no-browser`          | off         | Don't open a browser window on start.                    |
| `$AF_DATABASE_URL`      | `sqlite:///./af.db` | Same resolution as `af` — Postgres opt-in.        |

If the database isn't initialized (no Alembic revision or no seeded layers), the server still starts and surfaces the problem at `/api/health` and as a 503 on `/api/graph`.

#### Frontend hot-reload (development)

```bash
cd axiom-fabric-dashboard/frontend
npm run dev                             # Vite dev server, proxies /api to af-dashboard
```

Run `af-dashboard` in another shell so the dev server has an API to proxy to.

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
- **DONE:** Read-only web dashboard (`axiom-fabric-dashboard`) — FastAPI graph API over shared repository functions + a React Flow frontend, served by `af-dashboard`.
- **DONE:** Fact write CLI — `af fact create / update / retract / list / show / version / edges` with cross-layer edges in all four kinds (`derived_from`, `evidence_of`, `refutes`, `supersedes`) and append-only retraction tombstones.
- **DONE:** Custom layers + diagnostics — `af layer create` for non-default hierarchies; `af status` reports DB URL, schema revision, and row counts.
- **Next:** First-class layer versions — `layer_versions` table, history CLI, cascade-staleness mechanics.
- **Later — core loop:** Context assembly + LLM call, write-back loop with gated promotion, branch-cost + cascade re-evaluation, MCP server.
- **Later — sourced facts:** `FactSource` extension for dynamic data (SQL / HTTP / Python / MCP-tool), snapshot-on-refresh with TTL / cron / on-read policies, fetch provenance recorded in `justification`.
- **Later — beyond:** Dependency-directed backtracking, declarative agent blueprints, constrained decoding, durable execution, human-in-the-loop governance.

Treat anything beyond what's marked DONE as design-in-flight — the code is the authoritative source for what actually exists today.

## Setup

```bash
# Install (editable, with dev + optional LLM extras)
uv sync --all-extras                     # or: pip install -e './axiom-fabric[dev,llm]'

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
