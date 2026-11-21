"""Database plumbing: engine, session factory, table creation."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import (
    Connection,
    DateTime,
    Engine,
    bindparam,
    create_engine,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.timeutil import utc_now

SCHEMA_VERSION = 3


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
    """Create missing tables, migrate an older schema and record the version (idempotent)."""
    from app import models  # imported here so that every model is registered on Base

    with engine.connect() as connection:
        # pysqlite only opens a transaction before DML. Open it now, so that the
        # DDL of a migration is rolled back with the rest if anything fails.
        connection.exec_driver_sql("BEGIN")
        version = _stored_version(connection)
        if version == 1:
            _set_aside_v1_transactions(connection)
        Base.metadata.create_all(connection)
        if version == 1:
            _move_v1_brokers_to_envelopes(connection, models.DEFAULT_ENVELOPE_NAME)
            version = 2
        if version == 2:
            _add_cost_columns(connection)
        connection.commit()

    with Session(engine) as session:
        if session.scalar(select(models.SchemaVersion)) is None:
            session.add(models.SchemaVersion(version=SCHEMA_VERSION))
        if session.scalar(select(models.Envelope.id).limit(1)) is None:
            # A purchase needs an envelope: never leave a database without one.
            session.add(models.Envelope(name=models.DEFAULT_ENVELOPE_NAME, envelope_type="cto"))
        session.commit()


def _stored_version(connection: Connection) -> int | None:
    """Schema version of an existing database; None for a brand new one."""
    if not inspect(connection).has_table("schema_version"):
        return None
    return connection.scalar(text("SELECT max(version) FROM schema_version"))


# ---- version 1 -> 2: the free-text broker of a lot becomes an envelope --------


def _set_aside_v1_transactions(connection: Connection) -> None:
    """Rename the v1 table, so that create_all builds the v2 one (envelope_id NOT NULL)."""
    connection.execute(text("DROP INDEX ix_transactions_asset_date"))
    connection.execute(text("ALTER TABLE transactions RENAME TO transactions_v1"))


def _move_v1_brokers_to_envelopes(connection: Connection, default_name: str) -> None:
    """One CTO envelope per distinct broker, the default one for lots without broker."""
    now = utc_now()
    connection.execute(
        text(
            "INSERT INTO envelopes (name, envelope_type, broker, created_at) "
            "SELECT DISTINCT broker, 'cto', broker, :now FROM transactions_v1 "
            "WHERE broker IS NOT NULL"
        ).bindparams(bindparam("now", type_=DateTime)),
        {"now": now},
    )
    connection.execute(
        text(
            "INSERT INTO envelopes (name, envelope_type, created_at) "
            "SELECT :name, 'cto', :now WHERE EXISTS "
            "(SELECT 1 FROM transactions_v1 WHERE broker IS NULL) "
            "AND NOT EXISTS (SELECT 1 FROM envelopes WHERE name = :name)"
        ).bindparams(bindparam("now", type_=DateTime)),
        {"name": default_name, "now": now},
    )
    columns = "id, asset_id, type, trade_date, quantity, unit_price, fees, fx_rate, fx_source"
    selected = ", ".join(f"t.{column}" for column in columns.split(", "))
    connection.execute(
        text(
            f"INSERT INTO transactions ({columns}, envelope_id, created_at) "
            f"SELECT {selected}, e.id, t.created_at FROM transactions_v1 t "
            "JOIN envelopes e ON e.name = coalesce(t.broker, :name)"
        ),
        {"name": default_name},
    )
    # Never drop the old table unless every purchase made it across.
    before = connection.scalar(text("SELECT count(*) FROM transactions_v1"))
    after = connection.scalar(text("SELECT count(*) FROM transactions"))
    if after != before:
        raise RuntimeError(
            f"Migration to schema 2 copied {after} of {before} purchases; nothing was changed."
        )
    connection.execute(text("DROP TABLE transactions_v1"))
    connection.execute(text("UPDATE schema_version SET version = 2"))


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one database session per request."""
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session


# ---- version 2 -> 3: cost properties of envelopes, ongoing charges of assets --

V3_COLUMNS = {
    "envelopes": {
        "management_fee_pct": "FLOAT",
        "custody_fee_pct": "FLOAT",
        "custody_fee_per_position_eur": "FLOAT",
        "account_fee_eur": "FLOAT",
        "order_fee_pct": "FLOAT",
        "order_fee_min_eur": "FLOAT",
        "entry_fee_pct": "FLOAT",
        "exit_tax_rate_before_pct": "FLOAT",
        "exit_tax_threshold_years": "INTEGER",
        "exit_tax_rate_after_pct": "FLOAT",
    },
    "assets": {"ongoing_charges_pct": "FLOAT"},
}


def _add_cost_columns(connection: Connection) -> None:
    """Add the new nullable columns; a table create_all just built already has them."""
    for table, columns in V3_COLUMNS.items():
        existing = {column["name"] for column in inspect(connection).get_columns(table)}
        for name, sql_type in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
    connection.execute(text("UPDATE schema_version SET version = 3"))
