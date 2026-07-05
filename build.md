# Axiom Fabric — Architecture & Development Guide

Everything below the product surface: design principles, the data model, storage backends, repository layout, and how to build and run **Axiom Fabric** (`af`) from source on **macOS** and **Windows**.

For what the project *is* and the feature roadmap, see [`README.md`](./README.md).

---

## Design principles

These are the invariants the implementation is built around:

- **Separation of cognition and execution.** The LLM never writes to the truth store directly; promotion is always an explicit, gated step performed by the application (or a human).
- **Append-only history.** Every truth change is an append. Facts and layers grow new versions; old versions are never overwritten. The edge graph appends too — edges are never rewritten, only added when a new fact-version cites prior ones.
- **Layers vs. edges are orthogonal.** Layer assignment governs *policy* — write authority, default weight, promotion target, whether the LLM may mutate the fact. `fact_version_edges` govern *derivation* — which upstream fact-versions a given fact-version was built from. The two never need to align: a Living fact-version directly justified by a Canonical fact-version (skipping Episodic) is the normal case, not the exception.
- **Append-only ⇒ acyclic by construction.** A `fact_version` insert may only cite upstream fact-version IDs that already exist. The DAG only ever points backward in time, so cycles at the version level are physically impossible — no cycle-detection pass is needed. Apparent cycles at the *fact-identity* level (A.v2 cites B.v1 which cites A.v1) represent mutual refinement across versions, not pathology — surface them as a UI hint, never reject them.
- **Promotion direction.** Default: candidate facts promote into the **next layer up** from the highest layer present in the source generation's context. Callers can override.
- **Cascade re-evaluation.** When a fact-version is superseded (a new version appended) or retracted, every downstream fact-version reachable through `fact_version_edges` is marked **stale**, never silently re-pinned. The cascade runs *inside the writing transaction* (`facts.record_fact_version` → `cost.mark_subtree_stale`), so it is persisted atomically and is correct across concurrent writers — including a shared Postgres — without any notification layer: every reader is stateless and sees the flags on its next query. Layer-version staleness is a *derived view*: a layer-version is stale iff it pins any stale fact-version. Resolving a stale fact-version is its own decision, with its own cost.
- **Branch cost, not point cost.** Cost is `Σ(weight × depth × temperature)` summed over the **descendant subtree** in the edge graph, so the caller is choosing where to *alter the truth*, not just which row to edit.
- **Snapshots, not live reads.** Dynamic data (DB rows, API responses, crawl output) enters the store as snapshotted fact-versions with fetch provenance, never as live resolvers fired at generation time. A generation grounded in `inventory=47` must replay identically tomorrow even if the upstream system now reads `inventory=12`. Freshness is governed per-fact by refresh policy, not by reaching across the boundary mid-prompt.

## Data model

```text
Layer ──┬─< Fact ──< FactVersion
        │              │
        │              ├─ content (JSON)
        │              ├─ weight (0–100)
        │              ├─ justification (upstream layer-versions + fact-versions)
        │              └─ temperature (generation confidence)
        │
        └─ name, weight, ordinal
```

Six tables (defined in `axiom-fabric/src/axiom_fabric/models.py`):

| Table                | Purpose                                                  | Key columns                                                                      |
| -------------------- | -------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `layers`             | Truth layer = policy (write authority, default weight)   | `name`, `weight`, `ordinal`                                                      |
| `layer_versions`     | Immutable snapshots of a layer                           | `layer_id`, `version`, `weight`, `notes`                                         |
| `facts`              | Fact identity (stable across versions)                   | `layer_id`, `schema_ref`                                                         |
| `fact_versions`      | Versioned fact payloads                                  | `fact_id`, `layer_version_id`, `content` (JSON), `weight`, `justification`, `temperature`, `stale_since` |
| `fact_version_edges` | Derivation DAG between fact-versions                     | `source_fv_id`, `target_fv_id`, `edge_kind`                                      |
| `fact_sources`       | Optional 1:1 sidecar making a fact *dynamic* (sourced)   | `fact_id`, `kind`, `uri`, `params`, `refresh_policy`, `ttl_seconds`, `last_refreshed_at` |

Semantics:

- **`content`** — JSON payload. Free-form text claims and typed schema-bound records share the same column (`schema_ref` on `facts` is the optional pointer to a schema).
- **`weight`** — change-cost gravity at this version, 0–100. Defaults from the layer on create; carries forward on update.
- **`justification`** — JSON record of the upstream layer-versions and fact-versions this version was derived from; the human-readable audit blob. The same links are projected into `fact_version_edges` rows so the DAG is queryable for cascades and cost walks.
- **`temperature`** — LLM confidence (log-prob) at generation time, where available. Acts as a change-cost *multiplier*: facts the model was uncertain about are cheaper to rewrite later.
- **`edge_kind`** ∈ {`derived_from`, `evidence_of`, `refutes`, `supersedes`}. Edges always connect **fact-versions** (never fact identities) and may cross layers freely.
- **Write-path invariant:** every `target_fv_id` must already exist at insert time (`ForwardReferenceError` otherwise). That single check is the entire cycle-prevention mechanism.
- **Retraction** appends a tombstone version (`weight=0`, `content={}`, `note="retracted"`) — prior versions stay intact for audit.
- **`stale_since`** — NULL means fresh. Set by the cascade when an upstream derivation is superseded/retracted. A mutable governance annotation only; it never alters `content`, so a pinned generation still replays identically.

### Change cost & cascade staleness (implemented — `cost.py`)

Both primitives read the derivation DAG through one dialect-agnostic **recursive CTE** over `fact_version_edges` (`_descendants_query` — identical query text on SQLite and Postgres, built with SQLAlchemy Core), and are surfaced on the CLI (`af fact cost`, `af fact stale`) and MCP (`change_cost`, `list_stale`).

- **`change_cost(session, fv_id)`** walks the descendant subtree (everything transitively derived from `fv_id`, each node at its shortest depth) and returns `Cost = Σ (weight × depth × temperature_penalty)`. Used before altering a fact so the caller compares "rewrite a foundational rule" (large, deep subtree → high cost) vs. "rewrite a leaf" (zero cost) before committing. **Temperature penalty** = the fact-version's `temperature` (generation confidence in [0, 1]) as a multiplier, so low-confidence facts are cheaper to rewrite; a missing temperature defaults to `1.0` (most expensive).
- **`mark_subtree_stale` / `clear_stale` / `list_stale_fact_versions`** implement the cascade above. The cascade is triggered automatically inside `record_fact_version` whenever a fact gets a *later* version (v2+) or a tombstone — so a supersession/retraction flags its descendants in the same transaction. `layer_version_is_stale` is the derived layer-version view.

**Cross-process note (why there is no notify/outbox layer).** Because every frontend is stateless-read (CLI per-command, MCP per-tool-call, dashboard per-request all open a fresh `session_scope`), external writes to a shared Postgres are seen on the next read with no cache to invalidate, and the staleness cascade — being a committed DB write — is likewise visible to every reader. A push mechanism only becomes useful for a *long-lived cached consumer* (e.g. a live-updating dashboard). If that lands, the industrial-standard shape is a **transactional outbox** table (durable, ordered, replayable "what changed") as the source of truth, with a low-latency doorbell on top — `pg_notify` on Postgres, `PRAGMA data_version` on SQLite — behind a dialect-agnostic seam (mirroring the `VectorIndex` seam). Deliberately deferred until a cached consumer exists.

### Dynamic / sourced facts (implemented — `sources.py`)

A static `FactVersion` is a frozen snapshot written once; applications also need facts that track changing values (inventory, customer status, a scraped page). The design is **snapshot-on-refresh**, not live-resolve:

- The `fact_sources` table (1:1, optional, on `facts`) carries `kind` (`inline` / `python` / `sql` / `http` / `mcp_tool`), a kind-specific `uri`, `params`, and a refresh policy (`manual` / `on_read` / `ttl` / `scheduled`) with `ttl_seconds` / `schedule_cron`.
- **`refresh_fact`** fetches the current value via the source's resolver and appends a new `FactVersion` whose `justification` records fetch provenance — `source`, `fetched_at`, `fresh_until`. **`refresh_if_due`** consults the policy (`due_for_refresh`) and dedupes unchanged fetches so polling an `on_read`/`ttl` source does not accrete identical versions.
- Reads, version pinning, cost calculus, and cascade staleness all reuse the existing fact-version + edge path — a refresh is an ordinary supersession, so it cascades staleness to descendants for free. Reproducibility is preserved: pinned generations always see the snapshot value, never a live one.
- **Resolvers live behind a seam** (`register_resolver(kind, fn)`). `inline` (value carried in `params['value']`) and `python` (a dotted `module:callable`) ship wired; `sql` / `http` / `mcp_tool` are recognized and storable but raise an actionable "no resolver wired" error until a deployment registers one — keeping networked I/O and extra dependencies out of the core until needed. MCP is a natural ingress here: an external MCP tool (`inventory.get(sku)`, `weather.now(city)`) is a valid `mcp_tool` source — the ledger consumes MCP for sourcing *and* exposes MCP (`attach_source`, `refresh_fact`) for it.

## Repository layout

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
│       ├── graph.py           # load_graph — the shared whole-graph read path
│       ├── migrate.py         # Programmatic Alembic upgrade / downgrade
│       ├── models.py          # SQLAlchemy 2.0 ORM models
│       ├── mcp/               # af-mcp stdio server + agent_guide.md + install
│       └── migrations/        # Alembic migrations (dialect-agnostic)
└── axiom-fabric-dashboard/            # ── package: axiom-fabric-dashboard (web UI) ──
    ├── pyproject.toml                 #    depends on axiom-fabric; published separately
    ├── frontend/                      #    Vite + React + React Flow (built into static/)
    └── src/axiom_fabric_dashboard/
```

The core invariant: **CLI / dashboard / MCP frontends are presentation layers**. They never construct SQL or hold a `Session` longer than a request. All data access goes through repository functions in `layers.py` / `facts.py` / `graph.py`. The dashboard package imports `axiom_fabric` and reuses those same functions — it adds no data-access logic of its own. This is what lets the core ship to PyPI on its own and keeps the door open for alternative frontends without a premature abstraction.

## Storage backends

SQLAlchemy 2.0 + Alembic, two dialects out of the box, selected by `AF_DATABASE_URL`:

| URL                        | Backend           | Durable? | When to use                                            |
| -------------------------- | ----------------- | -------- | ------------------------------------------------------ |
| `sqlite:///./af.db`        | SQLite, file      | yes      | Local dev, single-user CLI, embedded use (the default) |
| `sqlite:///:memory:`       | SQLite, in-memory | no       | Tests, REPL, ephemeral agents                          |
| `postgresql+psycopg://...` | PostgreSQL        | yes      | Production; multi-process; JSONB-indexed queries       |

The schema is dialect-agnostic (`Uuid`, `JSON().with_variant(JSONB, "postgresql")`). On Postgres, `content` and `justification` are `JSONB` (indexable, queryable); on SQLite they degrade to plain `JSON` (TEXT) — fine for the common path, but queries leaning on JSONB operators are Postgres-only.

SQLite specifics handled by the engine layer (`db.py`):

- `:memory:` URLs share one in-memory DB across sessions in the same process (via `StaticPool`).
- `PRAGMA foreign_keys=ON` is enforced via a connect-time event listener so `ON DELETE CASCADE` / `RESTRICT` semantics actually apply.
- No new dependency — `sqlite3` is in Python's stdlib.

Default is `sqlite:///./af.db` — created in the working directory on first `af init`, zero infrastructure.

### Connecting to PostgreSQL

The driver (`psycopg[binary]`) ships prebuilt wheels — no `libpq` or build tools required on either platform.

1. **Create a database** (once):

   ```bash
   createdb axiom_fabric
   # or, via psql:  psql -U postgres -c 'CREATE DATABASE axiom_fabric;'
   ```

2. **Set the URL** — `postgresql+psycopg://<user>:<password>@<host>:<port>/<dbname>` (omit `:<password>` if your server trusts local connections):

   ```bash
   # macOS / Linux
   export AF_DATABASE_URL='postgresql+psycopg://postgres:secret@localhost:5432/axiom_fabric'
   ```

   ```powershell
   # Windows PowerShell
   $env:AF_DATABASE_URL = 'postgresql+psycopg://postgres:secret@localhost:5432/axiom_fabric'
   ```

3. **Migrate** against the new backend — same commands as SQLite:

   ```bash
   af init           # applies migrations to the Postgres database
   af layer list     # verify
   ```

---

## Prerequisites (both platforms)

| Requirement | Why                                                                                          |
| ----------- | -------------------------------------------------------------------------------------------- |
| Python ≥ 3.12 | Declared in `pyproject.toml` (`requires-python = ">=3.12"`).                               |
| `uv`        | Project uses `uv.lock`; `uv sync` is the canonical install path.                             |
| Git         | To clone the repo.                                                                           |
| Node 18+ / npm (optional) | Only to build the dashboard frontend.                                          |
| PostgreSQL ≥ 14 (optional) | Only for the Postgres backend. SQLite works out of the box.                   |

`uv` is the recommended dependency manager — it reads `pyproject.toml` + `uv.lock` and creates `.venv/` automatically. Plain `pip` works too; both paths are shown below.

---

## macOS

### 1. Install the toolchain

```bash
# Homebrew is the easiest path
brew install python@3.12 uv git

# Postgres (skip if you only want the SQLite backend)
brew install postgresql@16
brew services start postgresql@16
```

### 2. Clone and install

```bash
git clone <your-fork-or-remote-url> axiom-fabric
cd axiom-fabric

# Editable install with dev + optional LLM extras, into .venv/
uv sync --all-extras

# Equivalent without uv (installs the core package only):
# python3.12 -m venv .venv && source .venv/bin/activate
# pip install -e './axiom-fabric[dev,llm]'
```

### 3. Pick a backend

**SQLite (the default — zero setup):** nothing to do. With `AF_DATABASE_URL` unset, the default is `sqlite:///./af.db`, created in the working directory on the first `af init`. To set it explicitly (or use an in-memory DB for throwaway work):

```bash
export AF_DATABASE_URL='sqlite:///./af.db'
```

**Postgres (opt-in):** see [Connecting to PostgreSQL](#connecting-to-postgresql).

### 4. Run migrations + seed and verify

```bash
uv run af init --demo    # apply migrations + seed the example layers (omit --demo for a clean store)
uv run af layer list     # should print: canonical, episodic, living
```

---

## Windows

PowerShell is assumed (`pwsh` or Windows PowerShell 5.1). All commands below work in both.

### 1. Install the toolchain

Easiest path is `winget`:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id astral-sh.uv -e
winget install --id Git.Git -e

# Postgres (skip if you only want the SQLite backend)
winget install --id PostgreSQL.PostgreSQL.16 -e
```

After installing, **open a new PowerShell window** so the updated `PATH` is picked up. Verify:

```powershell
python --version    # 3.12.x
uv --version
git --version
```

### 2. Clone and install

```powershell
git clone <your-fork-or-remote-url> axiom-fabric
Set-Location axiom-fabric

# Editable install with dev + optional LLM extras, into .venv\
uv sync --all-extras

# Equivalent without uv (installs the core package only):
# py -3.12 -m venv .venv
# .\.venv\Scripts\Activate.ps1
# pip install -e "./axiom-fabric[dev,llm]"
```

If `Activate.ps1` is blocked by execution policy, run once per shell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3. Pick a backend

**SQLite (the default — zero setup):** nothing to do — with `AF_DATABASE_URL` unset, the default is `sqlite:///./af.db`, created on the first `af init`. To set it explicitly:

```powershell
$env:AF_DATABASE_URL = 'sqlite:///./af.db'
```

**Postgres (opt-in):** the Windows Postgres installer prompts for a password during install, so set the URL with your credentials:

```powershell
# Replace <password> and <dbname> with your values
$env:AF_DATABASE_URL = 'postgresql+psycopg://postgres:<password>@localhost:5432/<dbname>'
```

Create the database first if it doesn't exist (from a shell where `psql` is on `PATH` — typically `C:\Program Files\PostgreSQL\16\bin`):

```powershell
createdb -U postgres <dbname>
```

`psycopg[binary]` ships prebuilt wheels for Windows, so no `libpq` / Visual C++ build tools are required.

### 4. Run migrations + seed and verify

```powershell
uv run af init --demo    # apply migrations + seed the example layers (omit --demo for a clean store)
uv run af layer list     # should print: canonical, episodic, living
```

---

## Inspecting the data

Dialect-agnostic, via the CLI:

```powershell
uv run af status                           # DB URL, schema revision, row counts
uv run af layer list                       # all layers
uv run af layer history canonical          # version snapshots of a layer
uv run af layer version canonical 1        # one layer-version + its pinned fact-versions
uv run af fact edges <fact-version-uuid>   # derivation edges in/out of a fact-version
uv run af fact cost <fact-version-uuid>    # change cost: Σ(weight × depth × temperature) over descendants
uv run af fact stale                       # every fact-version flagged stale by a cascade
uv run af source show <fact-uuid>          # the source config on a dynamic fact (if any)
uv run af source refresh --fact-id <uuid>  # fetch + append a new snapshot version
```

Or dump the raw schema with SQLAlchemy's inspector:

```powershell
uv run python -c "from axiom_fabric.db import engine; from sqlalchemy import inspect; i=inspect(engine); [print(t, '->', [c['name'] for c in i.get_columns(t)]) for t in i.get_table_names()]"
```

Backend-specific tools work too:

- **SQLite:** `sqlite3 .\af.db ".schema"` (or `.tables` to list, `SELECT * FROM layers;` for rows)
- **Postgres:** `psql -U postgres -d axiom_fabric -c "\dt"` (or `\d+ fact_versions` for one table, `TABLE layers;` for rows)

A bigger end-to-end demo lives at `scripts/seed_demo_data.sh` — three layers, cross-layer edges, multi-version facts, and one retraction. Run it once for something interesting to view in the dashboard:

```bash
rm -f af.db                         # optional: clean slate
./scripts/seed_demo_data.sh
uv run af-dashboard                 # http://localhost:7373
```

---

## Persisting the database URL (both platforms)

Setting `AF_DATABASE_URL` in the shell only lasts for that session. Two durable options:

- **`.env` file in the directory you run `af` from** — read automatically by `pydantic-settings` (see `axiom-fabric/src/axiom_fabric/config.py`), and gitignored. The repo ships a template — copy and edit:

  ```bash
  cp .env.example .env          # macOS / Linux  (Windows: Copy-Item .env.example .env)
  ```

  ```
  AF_DATABASE_URL=sqlite:///./af.db
  ```

- **Shell profile** — append the `export` (macOS, `~/.zshrc`) or `[Environment]::SetEnvironmentVariable(...)` (Windows, user-scope) so it survives reboots.

Other settings honored via the `AF_` prefix: `AF_ECHO_SQL=true` to log every SQL statement.

---

## Optional: vector search extensions (planned semantic retrieval)

Semantic retrieval lands behind a small `VectorIndex` seam (see the README roadmap). The extensions are *not* required today — everything works without them — but installing now means no extra setup when the feature lands.

- **Postgres — pgvector.** macOS: `brew install pgvector`. Windows: install via Stack Builder, or `git clone https://github.com/pgvector/pgvector && nmake /F Makefile.win` from a Visual Studio Developer Prompt. Then, against your axiom-fabric database: `CREATE EXTENSION vector;`.
- **SQLite — sqlite-vec.** `uv pip install sqlite-vec` (loadable extension; the Python package ships the binary).

Application code stays dialect-agnostic — the `VectorIndex` seam dispatches to whichever backend `AF_DATABASE_URL` points at.

---

## MCP server (`af-mcp`)

`af-mcp` exposes the ledger to MCP-capable agents (Claude Code, Claude Desktop, Gemini CLI, Codex CLI) over **stdio**. Because Axiom Fabric is built to be driven by agents, the `mcp` dependency is part of the **default install** (not an opt-in extra), so `af-mcp` works out of the box. It is a thin adapter over the same `axiom_fabric.layers` / `axiom_fabric.facts` repository functions — `build_server(allow_writes)` in `mcp/server.py` registers the tools and the `axiom_fabric_usage` prompt.

```bash
uv sync                             # or: pip install axiom-fabric  (MCP is included by default)

# Wire it into an agent's config (writes .mcp.json / equivalent for that client)
uv run af-mcp install --client claude --allow-writes   # add --with-skill for guidance
uv run af-mcp install --client codex
```

Read tools are always on: `list_layers`, `list_facts`, `get_fact`, `get_fact_version`, `get_fact_edges`, `get_layer_history`, `search_facts`, `change_cost`, `list_stale`, `get_fact_source`. The write tools (`create_layer`, `create_fact`, `update_fact`, `retract_fact`, `attach_source`, `refresh_fact`) are registered only with `--allow-writes` (or `AF_MCP_ALLOW_WRITES=1`).

### Where each client's config lands

`af-mcp install` merges the right block into the client's config file (backing it up first; `--print` does a dry run that writes nothing):

| `--client`       | Config file                                                           | Entry                         |
| ---------------- | --------------------------------------------------------------------- | ----------------------------- |
| `claude`         | `./.mcp.json` (project) — or `~/.claude.json` with `--scope user`     | `mcpServers.axiom-fabric`     |
| `claude-desktop` | platform app-config dir (`~/Library/Application Support/Claude/...` on macOS, `%APPDATA%\Claude\...` on Windows) | `mcpServers.axiom-fabric`     |
| `gemini`         | `~/.gemini/settings.json`                                             | `mcpServers.axiom-fabric`     |
| `codex`          | `~/.codex/config.toml`                                                | `[mcp_servers.axiom-fabric]`  |

The generated block pins `AF_DATABASE_URL` to the **absolute** path of that directory's `af.db`, so the agent reaches the same store regardless of its working directory. A minimal hand-written `.mcp.json` (no `env` block → relative `./af.db` default):

```json
{ "mcpServers": { "axiom-fabric": { "command": "af-mcp", "args": ["serve", "--allow-writes"] } } }
```

### Activating and verifying

`af-mcp install` only **writes config** — the agent process spawns the stdio server itself on its next launch. For **Claude Code**: restart the session (or run `/mcp` to reload), approve the `axiom-fabric` project server when prompted, then verify with `/mcp` — the tools appear to the model as `mcp__axiom-fabric__<tool>`. For **Gemini / Codex CLI**, restart the CLI. For **Claude Desktop**, fully quit and reopen.

To run the server by hand for debugging (JSON-RPC on stdout, diagnostics on stderr; `Ctrl-C` to stop):

```bash
af-mcp serve --allow-writes      # drop the flag for a read-only server
```

### Per-project isolation and lazy auto-init

- **Per-project isolation = the SQLite default.** Because the stdio server is launched per directory, each project directory gets its own physically isolated store for free. A `project`/tenant column was evaluated and deliberately *not* added — it only pays off for a shared Postgres needing cross-project queries (deferred until that need is real).
- **No `af init` needed for MCP use.** A `_ensured_session()` chokepoint wraps every tool: the first tool call runs `ensure_schema()` (`migrate.py`), which checks the stamped Alembic revision and migrates to head if absent — SQLite creates its file on connect, so a missing `af.db` is created + migrated on first use. Init runs at *tool-call* time, not server startup, so a missing/unreachable DB surfaces as an actionable tool error the agent can relay, not a dead server. `ensure_schema()` is process-cached after the first call (reset by `reset_engine_for_tests()`).
- **Optional interactive setup (`AF_MCP_ELICIT_SETUP=1`, off by default).** With the flag on, a not-yet-created store is *not* silently auto-created; tools direct the agent to the `setup_store` tool, which uses MCP elicitation to ask *"create a local SQLite store here, or switch to Postgres?"*. Choosing SQLite migrates; choosing Postgres returns instructions to set `AF_DATABASE_URL` in `.mcp.json` and restart (a runtime DB switch can't persist across sessions). If the client can't elicit, `setup_store` degrades gracefully to creating the SQLite store. Default (flag off) is zero-setup: the first tool call silently creates the store.

To use **Postgres** instead of per-directory SQLite, set `AF_DATABASE_URL` in the server's `env` block in `.mcp.json` and restart the server.

### Agent guidance (skills)

Wiring the server gives the agent the *tools*; the guidance teaches it *how and when* to use them (read-before-act, append-only, weights, edges). The canonical text lives in `axiom-fabric/src/axiom_fabric/mcp/agent_guide.md` and is served live as the MCP prompt **`axiom_fabric_usage`** — any MCP client can fetch it.

For a **persistent** copy, add `--with-skill` to the install command — it generates the per-agent file from the canonical guide:

```bash
af-mcp install --client claude --with-skill   # writes .claude/skills/axiom-fabric/SKILL.md
af-mcp install --client gemini --with-skill   # merges a block into ./GEMINI.md
af-mcp install --client codex  --with-skill   # merges a block into ./AGENTS.md
```

Re-running is safe: the Claude `SKILL.md` is rewritten, and the Gemini/Codex blocks are wrapped in markers so they're replaced in place, never duplicated. `claude-desktop` has no skill file — use the MCP prompt there. The same files are committed under [`skills/`](skills/) for manual installs:

| Agent        | File                                            | Manual install                                                       |
| ------------ | ----------------------------------------------- | -------------------------------------------------------------------- |
| Claude Code  | [`skills/claude/SKILL.md`](skills/claude/SKILL.md) | Copy to `.claude/skills/axiom-fabric/SKILL.md` (or `~/.claude/skills/…`). |
| Gemini CLI   | [`skills/gemini/GEMINI.md`](skills/gemini/GEMINI.md) | Append to your project `GEMINI.md` (or `~/.gemini/GEMINI.md`).       |
| Codex CLI    | [`skills/codex/AGENTS.md`](skills/codex/AGENTS.md) | Append to your project `AGENTS.md`.                                  |

---

## Web dashboard

The `axiom-fabric-dashboard` workspace member serves a read-only web UI: a FastAPI backend — a thin presentation layer over the core's `load_graph` / `edges_for` repository functions, *not* a second data API — plus a Vite/React/React Flow frontend built into the package's `static/`.

The frontend bundle is **git-ignored** (a build artifact), so a fresh clone shows "frontend bundle has not been built" until you build it once (needs Node ≥ 18 + npm):

```bash
# One-time / on frontend change — build the React + React Flow bundle
npm --prefix axiom-fabric-dashboard/frontend install
npm --prefix axiom-fabric-dashboard/frontend run build

# Serve it — resolves the database from the current directory, exactly like `af`
uv run af-dashboard                      # opens http://localhost:7373
uv run af-dashboard --port 8080 --no-browser
```

If you launch without building first, the page explains how — the API at `/api/health` and `/api/graph` is still live. If the database isn't initialized, the server still starts and surfaces the problem at `/api/health` and as a 503 on `/api/graph`.

| Flag / env var          | Default     | Effect                                                   |
| ----------------------- | ----------- | -------------------------------------------------------- |
| `--host`                | `127.0.0.1` | Interface to bind.                                       |
| `--port` / `$AF_DASHBOARD_PORT` | `7373` | Starting port; auto-increments if busy.            |
| `--no-browser`          | off         | Don't open a browser window on start.                    |
| `$AF_DATABASE_URL`      | `sqlite:///./af.db` | Same resolution as `af` — Postgres opt-in.        |

### Frontend hot-reload (development)

```bash
cd axiom-fabric-dashboard/frontend
npm run dev                             # Vite dev server, proxies /api to af-dashboard
```

Run `af-dashboard` in another shell so the dev server has an API to proxy to.

---

## Development workflow

Run from the project root; `uv run` auto-activates `.venv/`.

```bash
# Tests
uv run pytest

# Lint + format
uv run ruff check .
uv run ruff format .

# Alembic (raw, if you need to author a migration) — run from the core package dir
cd axiom-fabric
uv run alembic -c alembic.ini revision --autogenerate -m "msg"
uv run alembic -c alembic.ini upgrade head
```

The CLI entry point is registered in `axiom-fabric/pyproject.toml` (`af = "axiom_fabric.cli:app"`); after `uv sync` you can also call `uv run af --help` from the workspace root for the full command surface.

## Engineering decisions (resolved)

- **Language.** Python only for now (`>=3.12`, targeting 3.14). Java SDKs deferred until a latency target is named.
- **Layer taxonomy.** Canonical / Episodic / Living ship as defaults; users add or remove layers per project. **Minimum of one layer must exist.**
- **Layer versioning.** First-class: each layer carries its own version history via `layer_versions`. Facts bind to a specific `(layer, layer_version)` pair. Layer-version staleness will be a *derived view* over fact-version staleness.
- **Dependency edges.** Normalized `fact_version_edges` adjacency table alongside the JSON `justification` blob — the blob stays human-readable, the table makes the DAG queryable for cascades and cost walks.
- **Fact content shape.** `content` JSON plus optional `schema_ref` on `facts` — typed schema-bound records and free-text claims share one table.
- **LLM interface scope (planned).** Anthropic SDK and OpenAI SDK behind a small provider-abstraction seam.
- **Vector indexing (planned).** pgvector on Postgres, sqlite-vec on SQLite, behind a `VectorIndex` seam so the query path stays dialect-agnostic.
- **Dynamic facts.** Snapshot-on-refresh, never live-resolve (implemented — see [Dynamic / sourced facts](#dynamic--sourced-facts-implemented--sourcespy)).
- **Change cost & staleness.** Recursive-CTE branch cost + in-transaction cascade staleness (implemented — see [Change cost & cascade staleness](#change-cost--cascade-staleness-implemented--costpy)). Cross-process change notification (outbox + `pg_notify`/`data_version`) deferred until a long-lived cached consumer exists.
- **Still proposed, not committed:** Temporal.io (durable execution), Outlines/Guidance (constraint manifolds), MAEB store + HITL toggle (governance). Confirm before adopting.

---

## Troubleshooting

| Symptom                                                              | Likely cause / fix                                                                                                                |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `uv: command not found` (macOS) or not recognized (Windows)          | Install step skipped or PATH not refreshed. Reopen the shell after install.                                                       |
| `connection to server at "localhost" ... failed`                     | Postgres not running, wrong port, or wrong credentials. Either start the service or switch to `sqlite:///./af.db`.                |
| `database "<name>" does not exist` after setting a Postgres URL      | The target DB hasn't been created yet. Run `createdb <name>` (or `CREATE DATABASE`), and check the user / password / port in `AF_DATABASE_URL`. |
| `ModuleNotFoundError: axiom_fabric`                                  | Editable install didn't take. Re-run `uv sync` from the repo root.                                                                |
| `No module named 'axiom_fabric.cli'` after moving the repo / switching OS | Stale `.venv` from another machine or layout. Rebuild: `rm -rf .venv && uv sync --all-extras`.                              |
| Windows: `Activate.ps1 cannot be loaded because running scripts is disabled` | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the shell, or just use `uv run` instead of activating. |
| Alembic complains about missing tables on first run                  | You skipped `af init`. That command runs migrations (use `--demo` to also seed the example layers).                               |
| Dashboard says "Can't load the truth store" after `af init`          | The default DB path (`./af.db`) is **relative to the launch directory** — running `af init` in one place and `af-dashboard` in another opens a different (empty) `af.db`. The health message names the exact file it opened; launch from the same directory, or set `AF_DATABASE_URL` to an absolute path (`sqlite:////abs/path/af.db` — four slashes on POSIX). |
