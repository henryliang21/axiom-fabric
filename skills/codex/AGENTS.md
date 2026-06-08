# Axiom Fabric: versioned truth store

> Add this section to your project's `AGENTS.md` (Codex reads it automatically),
> and wire the server with `af-mcp install --client codex`. Canonical source:
> `axiom-fabric/src/axiom_fabric/mcp/agent_guide.md` (also the MCP prompt
> `axiom_fabric_usage`).

Axiom Fabric is a **versioned, append-only truth ledger** exposed via the
`af-mcp` MCP server. Use it as durable, auditable memory: read the facts that
constrain a task before acting, and record new conclusions as facts (with
provenance) as you go so later steps and sessions inherit them.

If the write tools (`create_layer`, `create_fact`, `update_fact`,
`retract_fact`) are not listed, the server is read-only — read and ground your
work, but do not attempt to mutate.

## Model

- **Layer** — a named bucket with `weight` (0–100 = change-cost gravity) and
  `ordinal` (lower = more foundational). Layers are *policy*.
- **Fact** — a stable identity in one layer; its content lives in **versions**.
- **Fact-version** — an immutable JSON-object snapshot. Append-only.
- **Edge** — `edges_to` cites the upstream fact-version UUIDs a new fact was
  derived from. `edge_kind` ∈ `derived_from` (default), `evidence_of`,
  `refutes`, `supersedes`.

## Workflow

1. **Read first** — `list_layers`, then `list_facts(layer=…)` or
   `search_facts(query)`. Treat high-weight facts as hard constraints.
2. **Ground** your work in what you read; note the fact-version UUIDs you used.
3. **Record outcomes** (when writes are enabled): `create_layer` for a new
   domain; `create_fact(layer, content, edges_to=[…])` for a new conclusion;
   `update_fact(fact_id, content)` to **append** a new version (never
   overwrites); `retract_fact(fact_id, note)` to tombstone.

## Rules

- **Never mutate or delete** — always append via `update_fact` / `retract_fact`.
- **`content` is always a JSON object** (a dict).
- **`weight`** defaults to the layer's weight on create and carries forward on
  update; set it only when this fact is more/less authoritative than its layer.
- **Edges point backward only** — cite only fact-version UUIDs that already
  exist (this keeps the graph acyclic).
- **Be conservative with high-weight layers** — prefer a low-weight layer for
  tentative or generated claims unless the user established otherwise.
