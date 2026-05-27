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


def downgrade_to_base() -> None:
    command.downgrade(_alembic_config(), "base")


def current_revision() -> str | None:
    from alembic.runtime.migration import MigrationContext

    from axiom_fabric.db import get_engine

    with get_engine().connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()
