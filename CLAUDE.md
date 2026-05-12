# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This repository is pre-implementation. There is no source code, build system, or test suite yet — only `README.md` and `brief.md`. Do not invent commands, file layouts, or architecture that don't exist. When the user asks you to start building, ask which piece of the brief they want to scaffold first rather than assuming.

## Project vision

`axiom-fabric` is the working name for the **Truth Glue Framework** — a versioned truth layer for agentic AI. The full design recommendation lives in `brief.md` and is the authoritative source for scope and intent. Key concepts a future Claude instance must understand before writing code:

- **Integration Paradox** — the problem being solved: stochastic LLM output vs. deterministic application "truth".
- **Versioned Truth Tree** — facts/rules carry `Layer Weight` (gravity, 0–100), `Justification` (parent/evidence link), and `Temperature Influence` (log-prob confidence acting as a change-cost multiplier).
- **Truth Hierarchies** — Canonical (immutable laws) → Episodic (interaction history) → Living (LLM-generated facts that may become truth).
- **Four integration priorities**, in the brief's own ordering: (1) MCP as the wire protocol for exposing the Truth Ledger, (2) Cognitive Blueprints — declarative YAML/JSON for agent laws and layer weights, (3) Execution Gateways — authorization plane that intercepts LLM proposals and triggers dependency-directed backtracking, (4) Constraint Manifolds — constrained decoding to mask "untruthful" tokens at generation time.
- **Change Cost** formula governs backtracking: `Σ (Layer Weight × Depth × Temperature Penalty)`. The TMS traces dependencies, the cost decides which layer to rewrite, Temporal-style durable execution performs the rollback.

## Intended stack (per brief, not yet chosen)

Python primary, Java SDKs for low-latency components, Temporal.io for durable workflow state, Outlines/Guidance for constrained decoding, MAEB (Minimum Action-Evidence Bundle) store for the evidence ledger, HITL toggle for high-cost rewrites. Treat these as proposed defaults — confirm with the user before committing the repo to any of them.

## Next steps named in the brief

1. Draft the Truth Glue DSL (YAML schema for canonical vs. living fact weights).
2. Build the MCP Mediator server (translates app databases ↔ LLM context).
3. Implement the backtracking loop wiring TMS reasoning into Temporal signals/queries.

If the user references "the DSL", "the mediator", or "the backtracking loop" without further context, these are what they mean.
