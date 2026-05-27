# Build & Run

How to get **Axiom Fabric** (`af`) running locally on **macOS** and **Windows**.

Axiom Fabric is a Python 3.12+ package (`src/axiom_fabric/`) built with `hatchling`, dependency-managed by `uv`, persisted via SQLAlchemy + Alembic, and exposed as a Typer CLI named `af`. There is no compile step — "build" here means: set up the toolchain, install dependencies, point at a database, run migrations, and invoke the CLI.

See [`README.md`](./README.md) for what the project does and [`pyproject.toml`](./pyproject.toml) for the authoritative dependency list.

---

## Prerequisites (both platforms)

| Requirement | Why                                                                                          |
| ----------- | -------------------------------------------------------------------------------------------- |
| Python ≥ 3.12 | Declared in `pyproject.toml` (`requires-python = ">=3.12"`).                               |
| `uv`        | Project uses `uv.lock`; `uv sync` is the canonical install path.                             |
| Git         | To clone the repo.                                                                           |
| PostgreSQL ≥ 14 (optional) | Only needed if you want the Postgres backend. SQLite works out of the box.    |

`uv` is the recommended dependency manager — it reads `pyproject.toml` + `uv.lock` and creates `.venv/` automatically. Plain `pip` works too; both paths are shown below.

---

## macOS

### 1. Install the toolchain

```bash
# Homebrew is the easiest path
brew install python@3.12 uv git

# Postgres (skip if you only want the SQLite backend)
brew install postgresql@16
brew services start postgresql@16
```

### 2. Clone and install

```bash
git clone <your-fork-or-remote-url> axiom-fabric
cd axiom-fabric

# Editable install with dev + optional LLM extras, into .venv/
uv sync --all-extras

# Equivalent without uv:
# python3.12 -m venv .venv && source .venv/bin/activate
# pip install -e '.[dev,llm]'
```

### 3. Pick a backend

**SQLite (the default — zero setup):**

Nothing to do. With `AF_DATABASE_URL` unset, the default is `sqlite:///./af.db`, a file created in the working directory on the first `af init`. To set it explicitly (or use an in-memory DB for throwaway work):

```bash
export AF_DATABASE_URL='sqlite:///./af.db'
```

**Postgres (opt-in):**

Create a database and point `AF_DATABASE_URL` at it:

```bash
createdb axiom_fabric
export AF_DATABASE_URL='postgresql+psycopg://<user>@localhost:5432/axiom_fabric'
```

`psycopg[binary]` is pulled in as a dependency — no extra `libpq` install is required on macOS.

### 4. Run migrations + seed and verify

```bash
uv run af init           # apply migrations + seed default layers
uv run af layer list     # should print: canonical, episodic, living
```

---

## Windows

PowerShell is assumed (`pwsh` or Windows PowerShell 5.1). All commands below work in both.

### 1. Install the toolchain

Easiest path is `winget`:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id astral-sh.uv -e
winget install --id Git.Git -e

# Postgres (skip if you only want the SQLite backend)
winget install --id PostgreSQL.PostgreSQL.16 -e
```

After installing, **open a new PowerShell window** so the updated `PATH` is picked up. Verify:

```powershell
python --version    # 3.12.x
uv --version
git --version
```

### 2. Clone and install

```powershell
git clone <your-fork-or-remote-url> axiom-fabric
Set-Location axiom-fabric

# Editable install with dev + optional LLM extras, into .venv\
uv sync --all-extras

# Equivalent without uv:
# py -3.12 -m venv .venv
# .\.venv\Scripts\Activate.ps1
# pip install -e ".[dev,llm]"
```

If `Activate.ps1` is blocked by execution policy, run once per shell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3. Pick a backend

**SQLite (the default — zero setup):**

Nothing to do — with `AF_DATABASE_URL` unset, the default is `sqlite:///./af.db`, created on the first `af init`. To set it explicitly:

```powershell
$env:AF_DATABASE_URL = 'sqlite:///./af.db'
```

**Postgres (opt-in):** the Windows Postgres installer prompts for a password during install, so set the URL with your credentials:

```powershell
# Replace <password> and <dbname> with your values
$env:AF_DATABASE_URL = 'postgresql+psycopg://postgres:<password>@localhost:5432/<dbname>'
```

Create the database first if it doesn't exist (from a shell where `psql` is on `PATH` — typically `C:\Program Files\PostgreSQL\16\bin`):

```powershell
createdb -U postgres <dbname>
```

`psycopg[binary]` ships prebuilt wheels for Windows, so no `libpq` / Visual C++ build tools are required.

### 4. Run migrations + seed and verify

```powershell
uv run af init           # apply migrations + seed default layers
uv run af layer list     # should print: canonical, episodic, living
```

---

## Viewing the data structure

The schema is five tables (defined in `src/axiom_fabric/models.py`):

| Table                | Purpose                                                  | Key columns                                                                      |
| -------------------- | -------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `layers`             | Truth layer = policy (write authority, default weight)   | `name`, `weight`, `ordinal`                                                      |
| `layer_versions`     | Immutable snapshots of a layer                           | `layer_id`, `version`, `weight`, `notes`                                         |
| `facts`              | Fact identity (stable across versions)                   | `layer_id`, `schema_ref`                                                         |
| `fact_versions`      | Versioned fact payloads                                  | `fact_id`, `layer_version_id`, `content` (JSON), `weight`, `justification`, `temperature` |
| `fact_version_edges` | Derivation DAG between fact-versions                     | `source_fv_id`, `target_fv_id`, `edge_kind`                                      |

Inspect it live with the built-in CLI (dialect-agnostic — works on either backend):

```powershell
uv run af layer list                       # all layers
uv run af layer history canonical          # version snapshots of a layer
uv run af layer version canonical 1        # one layer-version + its pinned fact-versions
uv run af fact edges <fact-version-uuid>   # derivation edges in/out of a fact-version
```

Or dump the raw schema. The dialect-agnostic way uses SQLAlchemy's inspector:

```powershell
uv run python -c "from axiom_fabric.db import engine; from sqlalchemy import inspect; i=inspect(engine); [print(t, '->', [c['name'] for c in i.get_columns(t)]) for t in i.get_table_names()]"
```

Backend-specific tools work too:

- **SQLite:** `sqlite3 .\af.db ".schema"` (or `.tables` to list, `SELECT * FROM layers;` for rows)
- **Postgres:** `psql -U postgres -d axiom_fabric -c "\dt"` (or `\d+ fact_versions` for one table, `TABLE layers;` for rows)

---

## Persisting the database URL (both platforms)

Setting `AF_DATABASE_URL` in the shell only lasts for that session. Two durable options:

- **`.env` file in the repo root** — already supported by `pydantic-settings` (see `src/axiom_fabric/config.py`). `.env` is gitignored.

  ```
  AF_DATABASE_URL=sqlite:///./af.db
  ```

- **Shell profile** — append the `export` (macOS, `~/.zshrc`) or `[Environment]::SetEnvironmentVariable(...)` (Windows, user-scope) so it survives reboots.

Other settings honored via the `AF_` prefix: `AF_ECHO_SQL=true` to log every SQL statement.

---

## Optional: vector search extensions (Phase 2+)

Phase 2 of `plan.md` introduces semantic retrieval behind a small `VectorIndex` seam. The extensions are *not* required for Phase 1 — `af init` and `af layer list` work without them — but installing now means no extra setup when Phase 2 lands.

- **Postgres — pgvector.** macOS: `brew install pgvector`. Windows: install via Stack Builder, or `git clone https://github.com/pgvector/pgvector && nmake /F Makefile.win` from a Visual Studio Developer Prompt. Then, against your axiom-fabric database: `CREATE EXTENSION vector;`.
- **SQLite — sqlite-vec.** `uv pip install sqlite-vec` (loadable extension; the Python package ships the binary).

Application code stays dialect-agnostic — the `VectorIndex` seam dispatches to whichever backend `AF_DATABASE_URL` points at.

---

## Development workflow

Run from the project root; `uv run` auto-activates `.venv/`.

```bash
# Tests
uv run pytest

# Lint + format
uv run ruff check .
uv run ruff format .

# Alembic (raw, if you need to author a migration)
uv run alembic -c alembic.ini revision --autogenerate -m "msg"
uv run alembic -c alembic.ini upgrade head
```

The CLI entry point is registered in `pyproject.toml` (`af = "axiom_fabric.cli:app"`); after `uv sync` you can also call `uv run af --help` for the full command surface.

---

## Troubleshooting

| Symptom                                                              | Likely cause / fix                                                                                                                |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `uv: command not found` (macOS) or not recognized (Windows)          | Install step skipped or PATH not refreshed. Reopen the shell after install.                                                       |
| `connection to server at "localhost" ... failed`                     | Postgres not running, wrong port, or wrong credentials. Either start the service or switch to `sqlite:///./af.db`.                |
| `database "<name>" does not exist` after setting a Postgres URL      | The target DB hasn't been created yet. Run `createdb <name>` (or `CREATE DATABASE`), and check the user / password / port in `AF_DATABASE_URL`. |
| `ModuleNotFoundError: axiom_fabric`                                  | Editable install didn't take. Re-run `uv sync` from the repo root.                                                                |
| Windows: `Activate.ps1 cannot be loaded because running scripts is disabled` | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the shell, or just use `uv run` instead of activating. |
| Alembic complains about missing tables on first run                  | You skipped `af init`. That command runs migrations *and* seeds the three default layers.                                         |
