This project development recommendation for **"Truth Glue"** outlines a structured path toward building an open-source framework that manages versioned truth layers for AI-enabled applications. It synthesizes principles from **Reason Maintenance Systems (TMS)**, **Durable Execution**, and **Declarative Agent Architectures** to ensure LLM-generated content remains consistent with an evolving "canonical truth" substrate.

# Project Development Recommendation: Truth Glue Framework

## 1. Executive Summary
The Truth Glue Framework addresses the **"Integration Paradox"**: the mismatch between the stochastic, unstructured outputs of LLMs and the deterministic, schema-bound requirements of application "truth". This project will provide a **Governed Reference Architecture** that separates cognitive reasoning from execution, treating "truth" as a versioned, hierarchical ledger rather than a static prompt.

## 2. Core Architectural Principles
*   **Separation of Cognition and Execution:** The LLM reasoning core is isolated from the control, memory, and tool execution layers.
*   **Truth Hierarchies:** Data is structured into **Canonical Layers** (immutable laws), **Episodic Layers** (interaction history), and **Living Layers** (LLM-generated facts that become future truth).
*   **Durable Execution:** Every truth-altering decision is backed by an event history to allow for **failure-oblivious recovery** and precise version rollbacks.

## 3. The Truth Layer Model
The framework should implement a **Versioned Truth DAG** where each node is a fact-version with the following metadata:
*   **Layer Weight:** A numerical value (e.g., 0–100) defining the "gravity" of the fact. High weights represent foundational truths (laws of physics), while low weights represent mutable flavor text.
*   **Justification:** A structured set of links to the upstream fact-versions (and optionally MAEB evidence) that led to this fact's creation. Stored both as a JSONB blob (for audit) and projected into a normalized `fact_version_edges` adjacency table (for traversal). Edges are always between specific fact-versions, never between fact identities, and may cross layers freely.
*   **Temperature Influence:** The confidence level (log-probs) of the LLM during generation, which acts as a multiplier for the "cost" of changing that specific fact later.

**Architectural note: layers vs. edges are orthogonal.** Layer assignment governs *policy* — write authority, default weight, promotion target, whether the LLM may mutate the fact. `fact_version_edges` govern *derivation* — which upstream fact-versions a given fact-version was built from. The two never need to align: a Living fact-version directly justified by a Canonical fact-version (skipping Episodic) is the normal case, not the exception.

**The fact-version edge graph is acyclic by construction.** Because writes are append-only and a new fact-version can only cite IDs that already exist at insert time, edges only ever point backward in time. Cycles at the version level are physically impossible — no cycle-detection pass is needed. Apparent cycles at the *fact-identity* level (A.v2 cites B.v1 which cites A.v1) represent mutual refinement across versions, not pathology.

## 4. Integration Strategy (The 4 Priorities)

### Priority 4: Constraint Manifolds (The Generation Shield)
Implement **Constrained Decoding** to project application rules directly into the LLM’s token generation process. 
*   **Action:** Before the LLM generates a token, apply a masking function to set the probability of "untruthful" tokens to zero, preventing hallucinations by construction rather than post-hoc filtering.

### Priority 1: Model Context Protocol (MCP) (Standardized Interface)
Use **MCP** as the wire protocol for tool and resource discovery.
*   **Action:** Traditional applications expose their "Truth Ledger" as an MCP resource, allowing the LLM to query the current state (e.g., "What are the physical exits of this room?") via a standardized JSON schema.

### Priority 2: Cognitive Blueprints (The Living Constitution)
Define agent identities and truth-layer weights in a **Declarative AgenticFormat** (YAML/JSON).
*   **Action:** Allow developers to version control the "laws" of their game or application as data artifacts, making the agent’s boundaries auditable and portable across different LLM backends.

### Priority 3: Execution Gateways (Backtracking & Audit)
Establish an **Authorization Plane** that intercepts LLM proposals before they commit to the world state.
*   **Action:** If a proposal leads to an "impossible" state, the gateway triggers **Dependency-Directed Backtracking** to identify which truth layer must be rewritten based on the calculated **Change Cost**.

## 5. Conflict Resolution & Backtracking Logic
When future task solving becomes impossible, the framework must:
1.  **Trace Dependencies:** Walk the `fact_version_edges` DAG (append-only, acyclic by construction) downstream from the impasse via a recursive CTE — same query text on Postgres and SQLite. The TMS surfaces the affected fact-version set. Layer-version staleness is a derived view: a layer-version is stale iff any fact-version it pins has been invalidated.
2.  **Calculate Change Cost:** 
    $$\text{Cost} = \sum (\text{Layer Weight} \times \text{Depth} \times \text{Temperature Penalty})$$
    summed over the descendant subtree in `fact_version_edges`.
3.  **Evaluate Branching:** Compare the cost of rewriting a high-level "flavor" fact vs. a mid-level "structural" fact.
4.  **Execute Rollback:** Use **Temporal.io** to revert the workflow state to the chosen version and re-prompt the LLM with the revised truth.

## 6. Suggested Implementation Stack
*   **Language:** **Python** (for rich AI ecosystem) with **Java** SDKs for low-latency backend components.
*   **Workflow Engine:** **Temporal.io** for persistent state management and durable execution.
*   **Constraint Engine:** **Outlines** or **Guidance** for implementating the priority 4 masking functions.
*   **Governance Switch:** A configuration toggle for **Human-in-the-loop (HITL)** approval for high-cost truth rewrites (>X cost threshold).
*   **Evidence Ledger:** A **Minimum Action-Evidence Bundle (MAEB)** store to save policy versions, state snapshots, and decision records for every truth change.

## 7. Next Steps for Development
1.  **Draft the Truth Glue DSL:** Create a YAML schema to define weights for "living" vs. "canonical" facts.
2.  **Develop the MCP Mediator:** Build the server that translates between application databases and LLM context.
3.  **Implement the Backtracking Loop:** Integrate **Reason Maintenance** logic with **Temporal**'s signal/query mechanism to handle the "impossible task" scenario.