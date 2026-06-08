# Using Axiom Fabric as your truth store

Axiom Fabric (`af`) is a **versioned, append-only truth ledger**. Use it as durable,
auditable memory across a task: read the facts that constrain your work *before*
acting, and record new conclusions as facts *as you go* so later steps (and later
sessions) inherit them with full provenance.

You reach it through MCP tools (names below). Write tools are only present when the
server was started with writes enabled — if you don't see `create_fact`, the store
is read-only and you should not attempt to mutate it.

## Mental model

- **Layer** — a named bucket with a `weight` (0–100 = "gravity"/change-cost) and an
  `ordinal` (foundational layers first). Layers are *policy*: how authoritative and
  how expensive-to-change the facts inside are. Examples a user might create:
  `requirements` (high weight, ~90), `decisions` (~60), `scratch` (~10).
- **Fact** — a stable identity living in one layer.
- **Fact-version** — an immutable snapshot of a fact's `content` (a JSON object).
  History is **append-only**: you never edit or delete a version. "Updating" a fact
  appends a *new* version; "retracting" appends a tombstone version. Older versions
  remain for audit.
- **Edge** — a derivation link between fact-versions (`edges_to` cites the upstream
  fact-version UUIDs a new fact was derived from). Edges record *why* a fact exists.
  `edge_kind` ∈ `derived_from` (default), `evidence_of`, `refutes`, `supersedes`.

## Workflow

1. **Read first.** Call `list_layers` to see the structure, then `list_facts`
   (optionally filtered by `layer`) or `search_facts(query)` to load the facts
   relevant to the task. Treat high-weight facts as hard constraints.
2. **Ground your work** in what you read. Cite the fact-version UUIDs you relied on.
3. **Record outcomes** (when writes are enabled):
   - New domain with no suitable layer? `create_layer(name, weight, ordinal)`.
   - New conclusion/decision/finding? `create_fact(layer, content, edges_to=[…])`,
     where `content` is a JSON object and `edges_to` lists the upstream
     fact-version UUIDs that informed it.
   - A fact changed? `update_fact(fact_id, content)` — this **appends a new
     version**, it does not overwrite. Carry an `edges_to` if the change was driven
     by other facts.
   - A fact is no longer true? `retract_fact(fact_id, note)` — appends a tombstone;
     the history stays intact.

## Rules of the road

- **Never mutate or delete.** Always append via `update_fact` / `retract_fact`.
- **`content` is always a JSON object** (a dict), e.g.
  `{"claim": "API base URL is https://x", "source": "config.yaml"}`.
- **`weight`** defaults to the layer's weight on create and carries forward on
  update. Only set it when this fact is more/less authoritative than its layer.
- **Edges only point backward.** You can only cite fact-version UUIDs that already
  exist; this is what keeps the derivation graph acyclic. Get UUIDs from
  `list_facts` / `get_fact` / `create_fact` return values.
- **Be conservative with high-weight layers.** Recording a fact into a foundational
  layer asserts it is authoritative truth; prefer a low-weight layer for tentative
  or generated claims unless the user established otherwise.
