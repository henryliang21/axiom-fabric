from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Engine as _EngineType
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from axiom_fabric.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _engine_kwargs_for(url: str) -> dict[str, Any]:
    """SQLite needs special treatment; Postgres uses defaults."""
    if not url.startswith("sqlite"):
        return {}

    kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in url or url.endswith("sqlite://"):
        # Share one in-memory DB across all sessions in this process.
        kwargs["poolclass"] = StaticPool
    return kwargs


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            echo=settings.echo_sql,
            future=True,
            **_engine_kwargs_for(settings.database_url),
        )
    return _engine


@event.listens_for(_EngineType, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_for_tests() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
