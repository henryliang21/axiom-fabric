# axiom-fabric-dashboard

A local web UI for **Axiom Fabric**. It reads the same database the `af` CLI
uses (resolved from `AF_DATABASE_URL` / `.env` in the current directory) and
renders the truth store as a graph: facts grouped into layers, connected by
their fact-version edges, with version history per node.

This package depends on [`axiom-fabric`](../axiom-fabric) and reuses its
repository functions — it adds no data-access logic of its own, only an HTTP/JSON
presentation layer and the frontend bundle. It is published separately, so
installing the core does **not** pull in the dashboard.

The wheel ships the frontend **prebuilt**, so an *installed* dashboard needs no
Node/npm. **PyPI publication is pending** — for now, run it from this repo
(needs Node 18+ to build the frontend once):

```bash
uv sync --all-extras                                  # from the workspace root
npm --prefix frontend install                         # one-time
npm --prefix frontend run build
uv run af init                                        # creates ./af.db here
uv run af-dashboard                                   # then open http://localhost:7373
```

Once published: `pipx install axiom-fabric-dashboard` (pulls in the
`axiom-fabric` core; add `--include-deps` to also expose `af`, or use plain
`pip install` into a virtualenv to get both).

## Read-only (for now)

This first version is **read-only**: it visualizes layers, facts, fact-versions,
and the edge graph. Create / edit / delete will follow the core engine's write
APIs. The architecture — FastAPI over the core's repository functions, a React
Flow canvas — is built so editing becomes an additive change.

## Developing the frontend

The UI is a Vite + React + React Flow app under `frontend/`, built into
`src/axiom_fabric_dashboard/static/` (served by FastAPI, shipped in the wheel,
not committed to git):

```bash
cd frontend
npm install
npm run build        # writes the bundle the backend serves
npm run dev          # hot-reload dev server, proxying /api to a running af-dashboard
```

`af-dashboard` serves the built bundle; if it hasn't been built, the page
explains how. See the [repository root](../README.md) for the full overview.
