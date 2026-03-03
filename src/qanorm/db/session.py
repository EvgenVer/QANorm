"""Database session management."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from qanorm.settings import get_settings


def get_database_url() -> str:
    """Return the configured database URL."""

    return str(get_settings().env.db_url)


def create_database_engine(url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine."""

    database_url = url or get_database_url()
    return create_engine(database_url, future=True)


def create_session_factory(url: str | None = None) -> sessionmaker[Session]:
    """Create a configured sessionmaker."""

    return sessionmaker(
        bind=create_database_engine(url=url),
        autoflush=False,
        autocommit=False,
        future=True,
    )


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Provide a transactional session scope."""

    session = create_session_factory(url=url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
