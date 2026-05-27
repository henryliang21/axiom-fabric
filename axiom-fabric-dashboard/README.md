# axiom-fabric-dashboard

A local web UI for **Axiom Fabric**. It reads the same database the `af` CLI
uses (resolved from `AF_DATABASE_URL` / `.env` in the current directory) and
renders the truth store as a graph: facts grouped into layers, connected by
their fact-version edges, with version history per node.

This package depends on [`axiom-fabric`](../axiom-fabric) and reuses its
repository functions — it adds no data-access logic of its own, only an HTTP/JSON
presentation layer and the frontend bundle. It is published separately, so
installing the core does **not** pull in the dashboard.

```bash
pip install axiom-fabric-dashboard   # also installs axiom-fabric
af-dashboard --port 7373             # then open http://localhost:7373
```

> Status: package scaffold only. The FastAPI app and React/React Flow frontend
> are not implemented yet — `af-dashboard` currently prints a placeholder.

See the [repository root](../README.md) for the full project overview.
