# axiom-fabric

**The governance ledger for agentic AI.**

Agent memory systems answer *"what do I remember that's relevant?"* Axiom Fabric
answers a different question: **"what is true, how do I know, what breaks if it
changes, and who authorized the change?"**

It is a versioned, append-only truth ledger for the facts your agents and
applications act on: every fact carries explicit provenance (derivation edges to
the exact upstream fact-versions it was built from), an explicit trust weight
(0–100, by layer policy), and a full version history. Updates append, retractions
append tombstones — nothing is ever silently rewritten, so the history *is* the
audit trail.

This package is the core: the SQLAlchemy data model (`layers`, `layer_versions`,
`facts`, `fact_versions`, `fact_version_edges`), the repository functions all
frontends share, Alembic migrations, the `af` command-line interface, and the
`af-mcp` MCP server that lets agents (Claude Code, Claude Desktop, Gemini CLI,
Codex CLI, …) read and write the ledger during execution.

```bash
pip install axiom-fabric          # core + `af` CLI — SQLite built in, zero infrastructure
pip install "axiom-fabric[mcp]"   # + the af-mcp MCP server for agents
pip install "axiom-fabric[llm]"   # + Anthropic / OpenAI extras

af init                           # apply migrations -> clean store (`--demo` seeds example layers)
af fact create --layer canonical --content '{"rule": "max_refund_usd", "value": 500}'
af fact list
```

To wire it into an MCP-capable agent (read tools always on; write tools only
with `--allow-writes`):

```bash
af-mcp install --client claude --allow-writes --with-skill
```

Storage is SQLite by default (`./af.db`); set `AF_DATABASE_URL` to a
`postgresql+psycopg://` URL for production. The web dashboard lives in the
separate `axiom-fabric-dashboard` package.

Full documentation, the design model, and the roadmap (cascade staleness,
change-cost pricing, gated promotion of LLM output into truth) live in the
project repository's `README.md` and `build.md`.
