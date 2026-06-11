# Axiom Fabric

**The governance ledger for agentic AI.**

Agent memory systems answer *"what do I remember that's relevant?"* Axiom Fabric answers a different question: **"what is true, how do I know, what breaks if it changes, and who authorized the change?"**

It is a versioned, append-only **truth ledger** that sits between an application and the LLMs reasoning over it. Every fact carries explicit provenance (which upstream facts it was derived from), an explicit trust weight, and a full version history. LLM output never silently becomes truth — promotion is a gated, traceable step — and when a foundational fact changes, everything derived from it can be found, priced, and re-evaluated.

```bash
pip install axiom-fabric        # `af` CLI — Python ≥ 3.12, zero infrastructure (SQLite built in)
```

## Why a governance ledger, not another memory system

Modern agents (Claude Code, Codex, Hermes, OpenClaw, …) already manage memory: durable instructions in `CLAUDE.md`-style files, curated notes in `MEMORY.md`, an episodic transcript, transient tool results. That stack is good at *recall* — and structurally bad at *governance*:

- **Provenance is destroyed at compaction.** When a long transcript is summarized, *"we chose Postgres because the user said multi-writer"* becomes *"chose Postgres"*. The justification chain is gone forever, and there is no way to ask "where did this 'fact' come from?"
- **The trust hierarchy is implicit.** System prompt outranks user message outranks tool result — by position, not by policy. Nothing distinguishes an immutable business rule from a half-confident inference the model made an hour ago.
- **Invalidation doesn't exist.** If the user corrects a premise mid-task, or a source the agent read yesterday changes, nothing downstream gets flagged. Conclusions built on retracted ground stay in play.

This is the **Integration Paradox**: stochastic LLM output meets deterministic application truth, and the glue between them — retrieval, prompts, ad-hoc validation — leaves no audit trail and no invalidation path.

Axiom Fabric is the layer *beneath* memory that fixes those three gaps, drawing on a lineage of ideas from **Reason Maintenance Systems (TMS)**, durable execution, and declarative agent architectures:

| Gap | Axiom Fabric mechanism |
| --- | --- |
| Lost provenance | Every fact-version records a `justification` and **derivation edges** to the exact upstream fact-versions it was built from — a queryable DAG, kept forever. |
| Implicit trust | Facts live in **layers** with explicit weights (0–100). A canonical law at weight 90 and a tentative inference at weight 10 are different things, by policy, not by prompt position. |
| No invalidation | **Cascade staleness + change cost** (in development): change one fact and every downstream derivation is flagged stale — never silently re-pinned — with a priced re-evaluation plan. |

It is **complementary to** memory systems, not a replacement: let your agent's memory own recall and friction-free capture; Axiom Fabric is where load-bearing facts get recorded, audited, promoted, and invalidated. The truth your agent can't silently rewrite.

## How truth is organized

Three small concepts compose into a versioned truth graph:

- **Layer** — a *policy* container: a name, an ordinal (foundational layers first), and a default weight — the "gravity" of facts in it. Three ship as a default hierarchy (use them, or define your own):

  | Layer       | Weight | Purpose                                                        |
  | ----------- | ------ | -------------------------------------------------------------- |
  | `canonical` | 90     | Immutable laws — the physics of the system, schema invariants.  |
  | `episodic`  | 30     | Interaction history — what happened, when.                      |
  | `living`    | 10     | LLM-generated facts — candidates that may become future truth.  |

- **Fact** — a stable identity bound to a layer. It holds no content; it's the handle a version chain hangs off.
- **FactVersion** — an append-only snapshot: JSON `content`, a `weight`, a `justification` (the upstream fact-versions it was derived from, also projected as graph edges), and a `temperature` (generation confidence, where available — facts the model was unsure about are cheaper to overturn later).

Old versions are never overwritten. Updates append; retractions append a tombstone; the history is the audit trail.

Two properties fall out of the design rather than being enforced by machinery:

- **Layers ≠ edges.** Layer assignment is *policy* (write authority, default weight, promotion target). Derivation edges are *lineage*, and cross layers freely — a living fact derived straight from a canonical law is the normal case.
- **Append-only ⇒ acyclic.** A new fact-version can only cite versions that already exist, so derivation edges only point backward in time. Cycles are physically impossible — no cycle detection needed.

## Quickstart

```bash
pip install axiom-fabric        # or: pipx install axiom-fabric
af init                         # creates ./af.db (SQLite) — or `af init --demo` for example layers

# Record a canonical rule; note the fact-version UUID in the output.
af fact create \
    --layer canonical \
    --content '{"rule": "pricing_tiers", "free": 0, "pro": 10, "enterprise": 100}'
# -> Created fact d1c... (fv 7017b6be-..., v1, weight=90)

# Record an event derived from it (a cross-layer provenance edge).
af fact create \
    --layer episodic \
    --content '{"user": "alice", "event": "upgrade", "to": "pro"}' \
    --edges-to 7017b6be-...

# Revise by appending — v1 stays, forever, for audit.
af fact update \
    --fact-id d1c... \
    --content '{"rule": "pricing_tiers", "free": 0, "pro": 12, "enterprise": 100}' \
    --note "Pro tier raised $2"

# Retract = append a tombstone, not delete.
af fact retract --fact-id d1c... --note "Pricing rewritten as separate facts"

# Inspect.
af fact list --all-versions
af fact show d1c...             # full history + latest content
af fact edges 7017b6be-...      # what this version was derived from / what depends on it
```

Storage is SQLite by default (`./af.db`, zero setup); point `AF_DATABASE_URL` at Postgres for production or multi-process use — same schema, same commands. See [`build.md`](./build.md) for backends, configuration, and the full data model.

### CLI reference

Commands are grouped by noun. `af status` shows the resolved DB, schema revision, and row counts.

| Command | Purpose |
| --- | --- |
| `af init [--demo]` | Apply migrations; `--demo` seeds the three example layers. |
| `af layer create --name <slug> --weight 0-100 --ordinal <int>` | Add a custom layer. |
| `af layer list` / `af layer history <name>` / `af layer version <name> <n>` | Browse layers and their version snapshots. |
| `af fact create --layer <name> --content '<json>' [--edges-to <fv-uuid>]...` | New fact (v1). Content must be a JSON object; weight defaults to the layer's. |
| `af fact update --fact-id <uuid> --content '<json>'` | Append a new version (never mutates). |
| `af fact retract --fact-id <uuid>` | Append a tombstone version. |
| `af fact list [--layer <name>] [--all-versions]` | Table view. |
| `af fact show <fact-id>` / `af fact version <fv-uuid>` / `af fact edges <fv-uuid>` | Drill into a fact, one version, or its derivation edges. |

`--edges-to` takes fact-version UUIDs (targets must already exist — that's the cycle prevention); `--edge-kind` is one of `derived_from` (default), `evidence_of`, `refutes`, `supersedes`.

## Connect an agent (MCP)

The `af-mcp` server lets any MCP-capable agent (Claude Code, Claude Desktop, Gemini CLI, Codex CLI, …) use the ledger as a **live fact store during execution** — read the facts that constrain a task, record conclusions with provenance as it works — without prompt-stuffing.

```bash
pip install "axiom-fabric[mcp]"                  # provides `af` and `af-mcp`
af-mcp install --client claude --allow-writes --with-skill
# then restart the agent and approve the server
```

- **Read tools (always on):** `list_layers`, `list_facts`, `get_fact`, `get_fact_version`, `get_fact_edges`, `get_layer_history`, `search_facts`.
- **Write tools (opt-in):** `create_layer`, `create_fact`, `update_fact`, `retract_fact` — only registered when the server runs with `--allow-writes` (or `AF_MCP_ALLOW_WRITES=1`). A read-only server physically lacks them.
- **Zero setup:** the server auto-creates and migrates the store on the first tool call. Each project directory gets its own isolated ledger.
- **Built-in guidance:** the MCP prompt `axiom_fabric_usage` teaches the agent the model (read-before-act, append-only, weights, provenance edges); `--with-skill` drops a persistent per-agent guidance file.

`af-mcp install` supports `--client claude | claude-desktop | gemini | codex`, backing up any existing config before merging its block. The agent's working loop:

1. **Read the relevant truth first** — `list_layers`, then `search_facts`. High-weight facts are hard constraints.
2. **Record conclusions as facts**, citing the upstream fact-versions they came from:

   ```jsonc
   create_fact({
     "layer": "decisions",
     "content": { "choice": "use Postgres", "reason": "multi-writer" },
     "edges_to": ["<requirements fact-version UUID>"]
   })
   ```

3. **Revise by appending** — `update_fact` adds a version, `retract_fact` adds a tombstone; nothing is ever silently rewritten.

Later sessions — and other agents — read those facts back with full lineage. That is durable, auditable, *governed* memory.

See [`build.md`](./build.md) for server internals, per-client config paths, and manual wiring.

## Web dashboard

A local, read-only web UI for exploring the ledger: facts grouped into layers, connected by derivation edges, with per-version history and lineage in a side panel.

The `axiom-fabric-dashboard` package is part of this repo and currently runs **from source** (PyPI publication pending — requires Node 18+ to build the frontend once):

```bash
git clone <this-repo> && cd axiom-fabric-internal
uv sync --all-extras
npm --prefix axiom-fabric-dashboard/frontend install
npm --prefix axiom-fabric-dashboard/frontend run build
uv run af-dashboard                  # http://localhost:7373, same DB resolution as `af`
```

## Python API

```python
from axiom_fabric.db import session_scope
from axiom_fabric.layers import list_layers, seed_default_layers
from axiom_fabric.migrate import upgrade_to_head

upgrade_to_head()                       # apply migrations idempotently
with session_scope() as session:
    seed_default_layers(session)        # no-op if already seeded
    for layer in list_layers(session):
        print(layer.name, layer.weight)
```

## Features

### Shipped

- **Append-only truth store** — `layers` / `layer_versions` / `facts` / `fact_versions` / `fact_version_edges`, dialect-agnostic across SQLite (default, zero-setup) and Postgres (`AF_DATABASE_URL`).
- **Derivation DAG** — provenance edges between fact-versions in four kinds (`derived_from`, `evidence_of`, `refutes`, `supersedes`), cross-layer by design, acyclic by construction.
- **Full write CLI** — `af fact create / update / retract / list / show / version / edges`, `af layer create / list / history / version`, `af status` diagnostics, clean-start `af init`.
- **MCP server** (`af-mcp`) — read tools always on, write tools gated, agent guidance prompt + skills, one-command install for Claude / Claude Desktop / Gemini / Codex.
- **Read-only web dashboard** — FastAPI + React Flow graph explorer over the same repository functions.

### Planned

The roadmap, in order. The flagship governance mechanics — staleness, cost, gated promotion — are the next milestones.

1. **Context assembly + pinned generation.** A YAML DSL declaring layers and canonical facts; a query API ("the truth relevant to prompt X") with semantic retrieval (pgvector / sqlite-vec behind one seam); deterministic serialization so the same pinned fact-versions produce byte-identical context; `af generate` against Anthropic / OpenAI behind a provider seam.
2. **Write-back loop & gated promotion.** Every prompt/response logged as an episodic fact; LLM outputs become *candidate* facts carrying generation confidence; `af promote` commits a candidate into the next layer up, writing edges to every source fact-version — so any promoted fact traces back to the exact prompt and upstream versions that produced it.
3. **Cascade staleness + change cost.** The flagship: change a foundational fact and every downstream fact-version reachable through the derivation DAG is marked **stale** — never silently re-pinned — via a dialect-agnostic recursive CTE. Proposed changes are priced up-front: `Σ(weight × depth × temperature)` over the descendant subtree, so callers compare "rewrite a foundational rule" vs. "rewrite a leaf" before committing. `af cost`, `af stale`, `af reevaluate`.
4. **Sourced / dynamic facts.** First-class `FactSource` (SQL / HTTP / Python / MCP-tool) with snapshot-on-refresh: each refresh appends a fact-version with fetch provenance (`source`, `fetched_at`, `fresh_until`), and a refresh triggers downstream staleness through the same cascade as any other change. Live values, reproducible generations.
5. **Execution gateway + TMS backtracking.** An authorization plane that intercepts LLM-proposed truth changes before commit; when a proposal contradicts high-weight facts, dependency-directed backtracking over the derivation DAG returns the minimal-cost set of facts to rewrite — or a rejection — instead of letting the contradiction land.
6. **Cognitive blueprints.** Agent identity, allowed tools, and per-layer weight policies as declarative, version-controlled YAML artifacts. Switching projects = swapping a blueprint.
7. **Constraint manifolds.** Constrained decoding (Outlines / Guidance) masking tokens that would contradict canonical facts at generation time — for local-model paths that expose token-level control.
8. **Durable execution.** Promotion, gateway adjudication, and cascade re-evaluation as replayable workflows (Temporal.io) so a crash mid-operation never leaves the ledger inconsistent.
9. **Governance & HITL.** Cost-threshold human approval for expensive truth rewrites, and an evidence bundle (policy version, input context, decision record) for every truth-altering decision — so an auditor can reconstruct *why* any fact-version exists.

Anything not listed under **Shipped** is design-in-flight; the code is the authoritative source for what exists today.

## Documentation

- [`build.md`](./build.md) — architecture, data model, design principles, storage backends, building from source, MCP internals, development workflow.

## License

MIT — see [`LICENSE`](./LICENSE).
