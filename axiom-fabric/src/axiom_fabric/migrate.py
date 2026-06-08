from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from axiom_fabric.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


def upgrade_to_head() -> None:
    command.upgrade(_alembic_config(), "head")


# Set once we've confirmed (or established) that this process's database is at
# head. Lets `ensure_schema` be called on every request for the cost of a bool
# check after the first time.
_schema_ready = False


def is_initialized() -> bool:
    """True if the database already carries the schema (an Alembic revision is stamped)."""
    return current_revision() is not None


def ensure_schema() -> bool:
    """Bring the schema to head, at most once per process. Returns True if the
    store was empty and has just been initialized, False if it was already there.

    Cheap and idempotent — the actual check runs only on the first call. SQLite
    auto-creates its file on connect, so a missing `af.db` is created and migrated
    here on first use rather than surfacing as an error.
    """
    global _schema_ready
    if _schema_ready:
        return False
    fresh = current_revision() is None
    upgrade_to_head()  # creates the schema when fresh; a no-op when already at head
    _schema_ready = True
    return fresh


def reset_schema_state() -> None:
    """Test hook: forget that the schema was already ensured this process."""
    global _schema_ready
    _schema_ready = False


def downgrade_to_base() -> None:
    command.downgrade(_alembic_config(), "base")


def current_revision() -> str | None:
    from alembic.runtime.migration import MigrationContext

    from axiom_fabric.db import get_engine

    with get_engine().connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()
