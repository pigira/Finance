"""Database plumbing: engine, session factory, table creation."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

SCHEMA_VERSION = 1


class Base(DeclarativeBase):
    """Base class of every ORM model."""


def make_engine(database_url: str) -> Engine:
    """Create the engine; for SQLite, also create the parent folder of the file."""
    url = make_url(database_url)
    connect_args: dict[str, object] = {}

    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        # FastAPI runs sync endpoints in a thread pool: a connection may be used
        # from a thread other than the one that opened it.
        connect_args["check_same_thread"] = False

    engine = create_engine(database_url, connect_args=connect_args)

    if url.get_backend_name() == "sqlite":
        # SQLite ignores foreign keys unless asked, on every new connection.
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False keeps loaded objects readable after a commit,
    # which is what templates rendered after a write expect.
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create missing tables and record the schema version (idempotent)."""
    from app import models  # imported here so that every model is registered on Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        if session.scalar(select(models.SchemaVersion)) is None:
            session.add(models.SchemaVersion(version=SCHEMA_VERSION))
            session.commit()


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one database session per request."""
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session
