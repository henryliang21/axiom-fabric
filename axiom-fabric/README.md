# axiom-fabric

The core of **Axiom Fabric** — the Versioned Truth Layer for Agentic AI: the
SQLAlchemy data model (`layers`, `layer_versions`, `facts`, `fact_versions`,
`fact_version_edges`), the repository functions all frontends share, Alembic
migrations, and the `af` command-line interface.

This package has no UI. The web dashboard lives in the separate
[`axiom-fabric-dashboard`](../axiom-fabric-dashboard) package, which depends on
this one. Installing `axiom-fabric` gives you the core + CLI (+ MCP) only.

```bash
pip install axiom-fabric          # core + cli
pip install "axiom-fabric[llm]"   # + Anthropic / OpenAI extras
pip install "axiom-fabric[mcp]"   # + the af-mcp MCP server for agents

af init          # apply migrations -> clean (empty) store; `af init --demo` seeds examples
af layer list    # (empty after a clean init)
```

See the [repository root](../README.md) for the full project overview,
[`brief.md`](../brief.md) for the design vision, [`plan.md`](../plan.md) for the
roadmap, and [`build.md`](../build.md) for local setup.
