---
name: axiom-fabric
description: Use Axiom Fabric (the `af` MCP server) as a versioned, append-only truth store during a task — read the facts that constrain your work before acting, and record decisions/findings as facts as you go. Trigger when the axiom-fabric MCP tools (list_layers, list_facts, create_fact, ...) are available, when the user refers to the "truth store"/"fact store"/"truth ledger", or when work should be grounded in or persisted to Axiom Fabric.
---

# Axiom Fabric: versioned truth store

> Install: copy this file to `.claude/skills/axiom-fabric/SKILL.md` in your
> project (or `~/.claude/skills/axiom-fabric/SKILL.md` to make it global), and
> wire the server with `af-mcp install --client claude`. Canonical source:
> `axiom-fabric/src/axiom_fabric/mcp/agent_guide.md` (also the MCP prompt
> `axiom_fabric_usage`).

Axiom Fabric is a **versioned, append-only truth ledger** reached through MCP
tools. Use it as durable, auditable memory: read the constraining facts before
acting; record new conclusions as facts (with provenance) as you go so later
steps and sessions inherit them.

If the write tools (`create_layer`, `create_fact`, `update_fact`,
`retract_fact`) are not present, the server is read-only — read and ground your
work, but do not attempt to mutate. The server also exposes this guidance as the
MCP prompt **`axiom_fabric_usage`**.

## Model

- **Layer** — a named bucket with `weight` (0–100 = change-cost gravity) and
  `ordinal` (lower = more foundational). Layers are *policy*: how authoritative
  and how expensive to change the facts inside are.
- **Fact** — a stable identity in one layer; its content lives in **versions**.
- **Fact-version** — an immutable JSON-object snapshot. History is append-only.
- **Edge** — derivation link: `edges_to` cites the upstream fact-version UUIDs a
  new fact was derived from. `edge_kind` ∈ `derived_from` (default),
  `evidence_of`, `refutes`, `supersedes`.

## Workflow

1. **Read first** — `list_layers`, then `list_facts(layer=…)` or
   `search_facts(query)`. Treat high-weight facts as hard constraints.
2. **Ground** your work in what you read; note the fact-version UUIDs you used.
3. **Record outcomes** (when writes are enabled):
   - New domain → `create_layer(name, weight, ordinal)`.
   - New conclusion → `create_fact(layer, content, edges_to=[upstream UUIDs])`.
   - A fact changed → `update_fact(fact_id, content)` — **appends** a new
     version; it does not overwrite.
   - No longer true → `retract_fact(fact_id, note)` — appends a tombstone.

## Rules

- **Never mutate or delete.** Always append via `update_fact` / `retract_fact`.
- **`content` is always a JSON object** (a dict).
- **`weight`** defaults to the layer's weight on create and carries forward on
  update; set it only when this fact is more/less authoritative than its layer.
- **Edges point backward only** — you can only cite fact-version UUIDs that
  already exist (this keeps the graph acyclic).
- **Be conservative with high-weight layers** — recording there asserts
  authoritative truth. Prefer a low-weight layer for tentative/generated claims
  unless the user established otherwise.
