# axiom-fabric — Development Plan

## 1. Goal

Build a system (and reference template for apps) that manages a stack of **versioned truth layers** governing how an application uses an LLM. The core loop:

1. **Read.** App assembles context from a chosen set of layers, each pinned to a specific **layer version**. That context regulates the LLM call.
2. **Generate.** LLM produces output grounded in that pinned context.
3. **Promote (selective).** Chosen outputs become candidate facts in the **next layer up** from the ones that fed the generation. The promoted fact's `Justification` links — both as a JSONB audit blob and as rows in `fact_version_edges` — to the exact lower fact-versions it was derived from.
4. **Re-version a layer.** A foundational layer can be re-created as a new version. Every fact-version that pinned the prior layer-version is marked **stale** via the edge graph, and any higher layer-version containing a stale fact-version is therefore stale by derivation. Re-evaluation is a deliberate, cost-priced act, never automatic.
5. **Cost.** `Σ(weight × depth × temperature)` over the **descendant subtree** in `fact_version_edges` — the whole DAG of fact-versions reachable downstream from the thing being altered. Lets the caller compare "rewrite a foundational rule" vs. "rewrite a leaf flavor fact" before committing.

This plan maps the loop onto the wider Truth Glue Framework described in `brief.md`. The user-stated requirements drive v1 scope (Phases 1–3); the brief's bigger concepts (TMS backtracking, constrained decoding, MCP, Temporal) are sequenced as later phases so we don't over-build before the core loop works.

## 2. Decisions

Resolved:

- **Implementation language.** Python only for v1 (targeting `>=3.12`, running on 3.14). Java SDKs deferred until a latency target is named.
- **Storage backend.** Postgres as the production default (Postgres.app locally at `postgres@localhost:5432/henryliang`, no password). Move to a Postgres docker for shared/CI use later. **SQLite is a supported second backend** for local dev (file-backed) and tests / ephemeral agents (`:memory:`); selected via `AF_DATABASE_URL`. Schema is dialect-agnostic (`Uuid`, `JSON().with_variant(JSONB, "postgresql")`); SQLite engine uses `StaticPool` for `:memory:` and a `PRAGMA foreign_keys=ON` listener so cascade / restrict FKs are enforced. Phase 4+ queries that lean on JSONB operators remain Postgres-only; the recursive-CTE staleness walk (Phase 4) is written to run identically on both dialects.
- **Layer taxonomy.** Three layers ship as defaults — **Canonical**, **Episodic**, **Living** — and users may add or remove layers per project. **Minimum of one layer must exist;** the system refuses to start otherwise.
- **Layer versioning.** **First-class (Option B).** Each layer carries its own version history via a `layer_versions` table. Facts belong to a specific `(layer, layer_version)` pair, and adding/removing/mutating facts in a layer triggers a new layer version. Layer-version staleness is a *derived view* over fact-version staleness (see "Dependency edges" below): a layer-version is stale iff any fact-version it pins has been invalidated.
- **Dependency edges.** A normalized `fact_version_edges (source_fv_id, target_fv_id, edge_kind)` adjacency table sits alongside the JSONB `justification` blob on `fact_versions`. The JSONB stays human-readable; the adjacency table makes the DAG queryable for cascades and cost walks. Edges are always between **fact-versions** (not fact identities) and may cross layers freely — there is no requirement that an edge walk the layer ladder. `edge_kind` ∈ {`derived_from`, `evidence_of`, `refutes`, `supersedes`}. Write-path invariant: every `target_fv_id` must already exist at insert time. That single check is the entire cycle-prevention mechanism (see §3, "Append-only ⇒ acyclic").
- **Fact content shape.** `content JSONB` plus an optional `schema_ref` column on `facts`. Supports both typed schema-bound records and free-text claims through the same table.
- **LLM interface scope.** Anthropic SDK and OpenAI SDK both supported in v1, behind a small provider-abstraction seam.
- **Vector indexing.** pgvector on Postgres, sqlite-vec on SQLite, behind a `VectorIndex` seam. Lands in Phase 2 alongside the Query API rather than being deferred — semantic retrieval is needed for the assembly step to do better than layer-ordered concatenation.
- **Dynamic / sourced facts.** **Snapshot-on-refresh (Option B)** rather than live-resolve at generation time. A new `FactSource` row (1:1, optional, on `Fact`) carries `kind` (`sql` / `http` / `python` / `mcp_tool`), `uri`, `params`, `refresh_policy` (`on_read` / `ttl` / `manual` / `scheduled`), and `ttl_seconds` / `schedule_cron`. Each refresh writes a new `FactVersion` whose `justification` records fetch provenance (`source`, `fetched_at`, `fresh_until`) and projects edges to whatever upstream fact-versions the resolver consulted. Reads, version pinning, cost calculus, and cascade staleness all reuse the existing fact-version + edge path — dynamic and static facts are indistinguishable to consumers. Reproducibility is preserved: pinned generations always see the snapshot value, never a moving target. Out of scope for v1; lands as Phase 4.5 (depends on Phase 1.5 layer-versions + edge graph + Phase 4 cascade machinery).

## 3. Guiding principles (carried from brief, sharpened by §1)

- **Cognition / execution separation.** The LLM never writes to the truth store directly; promotion is always an explicit, gated step.
- **Append-only history.** Every truth change is an append. Both facts and layers grow new versions; old versions are never overwritten. The edge graph appends too — edges are never rewritten, only added when a new fact-version cites prior ones.
- **Fact metadata.** A fact-version carries `Layer Weight`, `Justification` (JSONB blob + projected `fact_version_edges` rows pointing at upstream fact-versions), and `Temperature Influence` (generation confidence). All populated from Phase 1.5 onward even where unused.
- **Layers vs. edges are orthogonal.** Layer assignment governs *policy* — write authority, default weight, promotion target, whether the LLM may mutate the fact. `fact_version_edges` govern *derivation* — which upstream fact-versions a given fact-version was built from. The two never need to align. A Living fact-version directly justified by a Canonical fact-version (skipping Episodic) is the normal case, not the exception. There is no rule that edges must walk the layer ordinal.
- **Append-only ⇒ acyclic by construction.** A `fact_version` insert may only cite upstream `fact_version` IDs that already exist. The DAG only ever points backward in time, so cycles at the version level are physically impossible — no cycle-detection pass is needed. Apparent cycles at the *fact-identity* level (A.v2 cites B.v1 which cites A.v1) represent mutual refinement across versions; surface them as a UI "refinement cluster" hint, do not reject them.
- **Promotion direction.** Default: candidate facts promote into the **next layer up** from the highest layer present in the source generation's context. Callers can override.
- **Cascade re-evaluation.** When a layer-version is re-created (or a sourced fact-version refreshes), every downstream fact-version reachable through `fact_version_edges` is marked **stale**, never silently re-pinned. Layer-version staleness is the derived view: a layer-version is stale iff it contains any stale fact-version. Resolving a stale fact-version is its own decision, with its own cost.
- **Branch cost, not point cost.** Cost is summed over the descendant subtree in the edge graph, so the user is choosing where to *alternate* the truth, not just which row to edit.
- **Snapshots, not live reads.** Dynamic data (DB rows, API responses, crawl output) enters the truth store as snapshotted fact-versions with fetch provenance, never as live resolvers fired at generation time. Reproducibility of a pinned generation is non-negotiable — a generation grounded in `inventory=47` must replay identically tomorrow even if the upstream POS now reads `inventory=12`. Freshness is governed per-fact by `FactSource.refresh_policy`, not by reaching across the boundary mid-prompt.

## 4. Phased roadmap

### Phase 1 — Core truth store (DONE)

Deliverables:

- Postgres schema: `layers`, `facts`, `fact_versions` (append-only), with foreign keys for parent fact and parent version.
- `Fact` node data model: id, layer, content, parent ids, weight, justification, temperature, created_at, version.
- Startup check: refuses to boot if zero layers; seeds the three default layers on first init unless `--skip-seed`.
- `axiom-fabric` DSL v0 stub deferred to Phase 2 (see below).
- CLI `af` with: `af init`, `af layer list`.

Exit criteria: ✓ schema migrated, default layers seeded, both commands working against `postgres@localhost/henryliang`.

### Phase 1.5 — First-class layer versions + fact-version edge graph

Deliverables:

- New `layer_versions` table: `(id, layer_id, version, weight, ordinal, created_at, notes)`. Unique on `(layer_id, version)`. Each row is the contract for one snapshot of a layer.
- `facts.layer_version_id` becomes the binding (replacing or augmenting `facts.layer_id`). Adding/removing/mutating facts in a layer creates a new `layer_version` row.
- New `fact_version_edges` table: `(source_fv_id, target_fv_id, edge_kind)`, PRIMARY KEY `(source_fv_id, target_fv_id)`. Both columns FK to `fact_versions.id`. Indexed in both directions for upstream and downstream traversal. Replaces the current single-link `parent_fact_id` / `parent_version_id` columns on `fact_versions` (they're subsumed — backfill into edges, then drop).
- Write-path enforcement: every `target_fv_id` referenced in a new edge must already exist at insert time. This is the entire mechanism preventing cycles — no other check needed.
- Each default-seeded layer gets a v1 row on `af init`.
- `fact_versions.justification` schema (JSONB) standardized to record the *list* of upstream layer-version ids + fact-version ids the fact was derived from. On insert, the service layer projects this list into `fact_version_edges` rows so the adjacency table is always in sync with the audit blob.
- CLI: `af layer history <name>`, `af layer version <name> <n>` (inspect), `af fact edges <fv-id>` (show incoming and outgoing edges).

Exit criteria: every fact belongs to a specific layer-version; re-versioning a layer is a single CLI call that produces a new row without mutating prior ones; every justification recorded in JSONB has a matching set of `fact_version_edges` rows; forward-reference inserts are rejected.

### Phase 2 — Context assembly + LLM call

Deliverables:

- Layer DSL v0: YAML schema declaring layers and seeding canonical facts (each YAML load = a new layer-version).
- Query API: "give me the truth relevant to prompt X". Initial implementation is layer-ordered concatenation from the latest non-stale layer-versions, with a token budget. **Semantic retrieval lands in this phase** — pgvector on Postgres, sqlite-vec on SQLite, behind a small `VectorIndex` seam so the query path is dialect-agnostic.
- Deterministic serialization of selected facts into a prompt block (so two calls with the same fact-version set produce byte-identical context).
- Provider-abstraction seam with Anthropic SDK and OpenAI SDK adapters. Same assembled context → provider-specific call.
- CLI: `af generate --provider {anthropic|openai}` consuming the assembled context.

Exit criteria: an `af generate` call against either provider produces text grounded in the pinned layer-versions, reproducible given the same set; semantic retrieval works against both backends.

### Phase 3 — Write-back loop (Living layer + promotion)

Deliverables:

- Episodic log: every prompt/response pair stored as a fact in the Episodic layer, regardless of promotion. Justification pins the source layer-versions and source fact-versions via `fact_version_edges`.
- Candidate-fact construction from LLM output: pulls `Temperature Influence` from log-probs where available (OpenAI yes, Anthropic limited), records parent layer-versions and fact-versions through the edge graph.
- `af promote <candidate>` commits the candidate into the **next layer up** from the highest-pinned source layer (default), with `--layer <name>` to override. Promotion creates a new layer-version on the destination layer and writes edges from the new fact-version to every source fact-version that fed the generation.
- A re-served promoted fact is fully traceable: from any fact-version, walk `fact_version_edges` backward to the prompt and the source fact-versions.

Exit criteria: a generated fact can be promoted, re-served as context to a later generation, and traced backward through the edge graph to its source prompt and exact upstream versions.

### Phase 4 — Layer weights, branch cost, cascade re-evaluation

Deliverables:

- Enforce `Layer Weight` on writes: changes to higher-weight facts/layers require an explicit override flag.
- **Branch-cost computation.** Given a proposed change to a fact-version or a layer-version, walk descendants via `fact_version_edges` (recursive CTE) and compute `Σ(weight × depth × temperature)`. Returned on every proposed change.
- **Cascade re-evaluation via recursive CTE.** Same query text runs on both Postgres and SQLite — a recursive CTE over `fact_version_edges` walks every downstream fact-version and marks it `stale`. Layer-version staleness is a **derived view**: a layer-version is stale iff it contains any stale fact-version. Stale layer-versions are excluded from default context assembly. `af reevaluate <fv-id | layer-version>` offers two paths: (a) programmatic re-derivation if `justification` records a deterministic recipe, (b) LLM re-generation under the new context (which itself produces a new fact-version + layer-version with new cost).
- CLI: `af cost <change>`, `af stale`, `af reevaluate`.

Exit criteria: changing a canonical fact reports the full branch cost up-front, marks downstream fact-versions stale via the edge graph, derives layer-version staleness, and offers a re-evaluation path per stale node.

### Phase 4.5 — Dynamic / sourced facts (FactSource + refresh runner)

Deliverables:

- Schema: new `fact_sources` table (1:1 with `facts`, optional) — `(id, fact_id, kind, uri, params JSONB, refresh_policy, ttl_seconds, schedule_cron, next_refresh_at, last_refreshed_at)`. `kind` ∈ {`sql`, `http`, `python`, `mcp_tool`}. `refresh_policy` ∈ {`on_read`, `ttl`, `manual`, `scheduled`}.
- `fact_versions.justification` schema extended: in addition to the upstream-derivation links from Phase 1.5, sourced versions carry `{"source": "...", "fetched_at": "...", "fresh_until": "..."}`. Edges still project from any upstream fact-versions the resolver consulted.
- Resolver registry: dispatch table mapping `kind` → callable that takes `(uri, params)` and returns a JSON payload + optional log-prob / confidence.
- Refresh runner: a service-layer function that walks `fact_sources` whose `next_refresh_at ≤ now()` (or whose `refresh_policy='on_read'` is being requested at assembly time), fires the resolver, and writes a new `fact_version` row + edges. Reuses the same atomic write used by `create_layer_version` so a refresh = a layer-version bump in the layer hosting the sourced fact.
- Cascade staleness generalizes: refreshing a sourced fact-version marks downstream fact-versions stale via the Phase 4 edge walk — same code path as "canonical layer re-versioned."
- Default layer addition (proposed, to be confirmed during build): an `observed` (or `live`) layer between Canonical and Episodic, default weight 50. Optional — users can also attach `FactSource` to facts in any other layer.
- CLI: `af source list`, `af source attach <fact> --kind sql --uri ...`, `af source refresh <fact>`, `af source policy <fact> --ttl 60`.

Exit criteria: a `FactSource` of each kind can be attached, refreshed manually + on TTL, produces a new fact-version with fetch provenance in `justification`, and triggers downstream staleness through the same machinery as re-versioning. Re-running a generation pinned to an old fact-version reproduces the exact value seen at original generation time.

Storage caveat: lands Postgres-only at first. SQLite can store sourced fact-versions, but the JSONB-operator queries some refresh policies need remain Postgres-only.

### Phase 5 — MCP interface (brief's Priority 1) (DONE)

Deliverables:

- MCP server exposing the truth store as a queryable resource (list layers, list layer-versions, list facts, get fact-version, get history, query by layer, walk edges).
- Read-only first; write tools (promote, override, reevaluate) gated behind a flag.
- **Local-first packaging.** The MCP server ships as both a Python wheel (`pip install axiom-fabric-mcp`) and a portable executable (PyInstaller / Nuitka) so an IDE-embedded agent (Cursor, Claude Desktop) can launch it against a local SQLite file with no Python toolchain on the host.

Exit criteria: an external MCP-capable client can browse and query the truth store without using the CLI; the server runs against a local SQLite file from an IDE plugin context.

**As built:**

- The server lives in the core package as `af-mcp` (extra `[mcp]`, dep `mcp`). It's a thin stdio adapter — each tool opens one session and calls an existing `layers` / `facts` / `graph` repository function, then serializes. `build_server(allow_writes)` in `mcp/server.py` registers the read tools always, write tools (`create_layer` / `create_fact` / `update_fact` / `retract_fact`) only when `--allow-writes` (or `AF_MCP_ALLOW_WRITES=1`). The `axiom_fabric_usage` prompt is single-sourced from `mcp/agent_guide.md`. `af-mcp install --client {claude|claude-desktop|gemini|codex} [--with-skill]` wires up agent configs.
- **Per-project isolation = the SQLite default (no project table).** Because the stdio server is launched per directory, each project directory gets its own physically-isolated store for free (`af-mcp install` pins that directory's `af.db` as an absolute `AF_DATABASE_URL`; a hand-written config with no `env` uses the relative `sqlite:///./af.db` default). A `project`/tenant column was evaluated and deliberately *not* added — it only pays off for a shared Postgres needing cross-project queries (deferred until that need is real).
- **Lazy store auto-init (no `af init` required for MCP use).** A `_ensured_session()` chokepoint wraps every tool: on the first tool call it runs `ensure_schema()` (added to `migrate.py`), which checks the stamped Alembic revision and migrates to head if absent — SQLite creates its file on connect, so a missing `af.db` is created + migrated on first use. Init runs at *tool-call* time, not server startup, so a missing/unreachable DB surfaces as an actionable tool error the agent can relay rather than a server that only shows as "failed" in the client's `/mcp` panel. `ensure_schema()` is process-cached (one check after the first call) via a module flag, reset by `reset_engine_for_tests()`.
- **Optional interactive setup via MCP elicitation (opt-in, off by default).** With `AF_MCP_ELICIT_SETUP=1`, a not-yet-created store is *not* silently auto-created; instead tools return a directive to call the `setup_store` tool, which uses `ctx.elicit(...)` to ask "create a local SQLite store here, or switch to Postgres?". Choosing SQLite migrates; choosing Postgres (while configured for SQLite) returns instructions to set `AF_DATABASE_URL` in `.mcp.json` and restart — the Postgres choice is routed to launch-time config because a runtime DB-switch can't persist across sessions. If the client can't elicit, `setup_store` degrades gracefully to creating the safe-default SQLite store. The default (flag off) is zero-setup: first tool call silently creates the store.
- Tests: `tests/test_mcp_autoinit.py` covers first-call auto-init on a never-migrated file DB, immediate writes on a fresh store, the elicit-flag gate blocking auto-create, and the `setup_store` no-client fallback + idempotency. `tests/test_mcp_tools.py` and `tests/test_mcp_gating.py` cover the tool surface and read/write gating.

### Phase 6 — Execution Gateway and TMS (brief's Priority 3)

Deliverables:

- Gateway intercepts every LLM-proposed truth change before commit.
- TMS: the `fact_version_edges` DAG *is* the dependency graph; Phase 4's cascade mechanics generalize into full dependency-directed backtracking.
- Given an impasse, return the minimal-cost set of nodes to rewrite (including the option of stepping down a layer rather than rewriting a leaf).

Exit criteria: when a proposal contradicts existing high-weight facts, the gateway returns either a rejection or a backtracking plan with cost.

### Phase 7 — Cognitive Blueprints (brief's Priority 2)

Deliverables:

- Extend the DSL so agent identity, allowed tools, and per-layer weight policies are declared as version-controlled artifacts.
- Loader that boots an agent from a blueprint file.

Exit criteria: switching projects = swapping a blueprint; no code change.

### Phase 8 — Constraint Manifolds (brief's Priority 4)

Deliverables:

- Integrate Outlines or Guidance to mask tokens that would violate Canonical facts at decode time.
- Limited to providers that expose token-level control (rules out hosted Anthropic/OpenAI for this phase; requires a local-model path).

Exit criteria: in a controlled test, a model cannot emit a string that contradicts a declared canonical fact.

### Phase 9 — Durable execution

Deliverables:

- Replace ad-hoc commit logic with Temporal workflows for: promotion, gateway adjudication, cascade re-evaluation, backtracking rollback.
- Replay-driven recovery for partial failures.

Exit criteria: killing the process mid-promotion or mid-reevaluation leaves the store in a consistent state on restart.

### Phase 10 — Governance / HITL

Deliverables:

- Cost-threshold trigger: branch-cost above configured threshold requires human approval before commit.
- MAEB (Minimum Action-Evidence Bundle) writer: every truth-altering decision snapshots policy version, input context, and decision record.

Exit criteria: an auditor can reconstruct *why* any given fact-version or layer-version exists.

## 5. Out of scope for v1 (Phases 1–3)

- Multi-tenant access control.
- Distributed / multi-writer truth stores.
- UI — CLI and MCP only.
- Cross-project fact federation.
- Automatic cascade re-evaluation (Phase 4); v1 only flags staleness, doesn't act on it.
- Dynamic / sourced facts (Phase 4.5); v1 facts are statically authored only, via DSL or direct seeding.

## 6. Next concrete tasks (Phase 1 done → Phase 1.5)

1. Migration: add `layer_versions` table, backfill v1 for the three seeded layers, add `layer_version_id` to `facts` (and decide whether to keep `facts.layer_id` as a denormalized convenience or drop it).
2. Migration: add `fact_version_edges` table with both-direction indexes; backfill existing `fact_versions.parent_fact_id` / `parent_version_id` rows into edges, then drop those columns.
3. SQLAlchemy models: `LayerVersion`, `FactVersionEdge`; update `Fact` to bind via `layer_version_id`; replace `FactVersion`'s parent columns with `edges_out` / `edges_in` relationships.
4. Service layer: `create_layer_version(layer, fact_specs)` — atomic write that creates the version row and the facts pinned to it; `record_fact_version(fact, content, justification)` — single entry point that writes the `fact_version` row AND projects `justification` into `fact_version_edges`, enforcing that every `target_fv_id` already exists.
5. Update seeding: `af init` creates v1 of each default layer.
6. CLI: `af layer history <name>`, `af layer version <name> <n>`, `af fact edges <fv-id>`.
7. Tests: unit tests for the layer-version write path; unit tests for edge insertion rejecting forward references; integration test for `af init` end-to-end producing 3 layers × 1 version each.
8. Only after that: Phase 2 — DSL loader + `af generate` + `VectorIndex` seam (pgvector / sqlite-vec).
