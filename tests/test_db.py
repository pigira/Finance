"""Schema creation and migrations, run against real SQLite files."""

from datetime import date
from pathlib import Path

from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from app.db import SCHEMA_VERSION, init_db, make_engine
from app.models import Asset, Envelope, SchemaVersion, Transaction
from app.services import envelopes

# The schema as version 1 of the application created it, verbatim.
V1_SCHEMA = """
CREATE TABLE schema_version (
    version INTEGER NOT NULL,
    applied_at DATETIME NOT NULL,
    PRIMARY KEY (version)
);
CREATE TABLE assets (
    id INTEGER NOT NULL,
    isin VARCHAR(12) NOT NULL,
    name VARCHAR(200) NOT NULL,
    asset_type VARCHAR(10) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    ticker VARCHAR(30),
    exchange VARCHAR(30) NOT NULL,
    price_scale FLOAT NOT NULL,
    manual_price BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_assets_isin_exchange UNIQUE (isin, exchange),
    CONSTRAINT ck_assets_type CHECK (asset_type in ('etf', 'stock', 'fund'))
);
CREATE TABLE fx_rates (
    currency VARCHAR(3) NOT NULL,
    rate_date DATE NOT NULL,
    rate FLOAT NOT NULL,
    source VARCHAR(20) NOT NULL,
    fetched_at DATETIME NOT NULL,
    PRIMARY KEY (currency, rate_date)
);
CREATE TABLE transactions (
    id INTEGER NOT NULL,
    asset_id INTEGER NOT NULL,
    type VARCHAR(4) NOT NULL,
    trade_date DATE NOT NULL,
    quantity FLOAT NOT NULL,
    unit_price FLOAT NOT NULL,
    fees FLOAT NOT NULL,
    fx_rate FLOAT,
    fx_source VARCHAR(20),
    broker VARCHAR(100),
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_transactions_type CHECK (type in ('buy', 'sell')),
    FOREIGN KEY(asset_id) REFERENCES assets (id)
);
CREATE INDEX ix_transactions_asset_date ON transactions (asset_id, trade_date);
CREATE TABLE price_history (
    asset_id INTEGER NOT NULL,
    price_date DATE NOT NULL,
    price FLOAT NOT NULL,
    source VARCHAR(20) NOT NULL,
    fetched_at DATETIME NOT NULL,
    PRIMARY KEY (asset_id, price_date),
    FOREIGN KEY(asset_id) REFERENCES assets (id)
);
INSERT INTO schema_version VALUES (1, '2026-01-05 09:00:00');
"""

# Four purchases: two at "Courtier A", one at "Courtier B", one without broker.
V1_PURCHASES = """
INSERT INTO assets VALUES
    (1, 'US5949181045', 'Microsoft Corporation', 'stock', 'USD', 'MSFT', 'NMS', 1.0, 0),
    (2, 'IE00B4L5Y983', 'iShares Core MSCI World', 'etf', 'EUR', 'IWDA.AS', 'AMS', 1.0, 0);
INSERT INTO transactions VALUES
    (1, 1, 'buy', '2024-03-15', 10.0, 400.0, 1.0, 1.09, 'ecb', 'Courtier A', '2026-01-05 09:01:00'),
    (2, 1, 'buy', '2025-06-02', 5.0, 450.0, 1.0, 1.14, 'ecb', 'Courtier A', '2026-01-05 09:02:00'),
    (3, 2, 'buy', '2023-09-12', 10.0, 80.0, 2.0, NULL, NULL, 'Courtier B', '2026-01-05 09:03:00'),
    (4, 2, 'buy', '2022-01-31', 3.0, 70.0, 0.0, NULL, NULL, NULL, '2026-01-05 09:04:00');
"""


# The schema as version 2 left it (envelopes, no cost properties), with one purchase.
V2_DATABASE = """
CREATE TABLE schema_version (
    version INTEGER NOT NULL,
    applied_at DATETIME NOT NULL,
    PRIMARY KEY (version)
);
CREATE TABLE envelopes (
    id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    envelope_type VARCHAR(20) NOT NULL,
    broker VARCHAR(100),
    opening_date DATE,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_envelopes_name UNIQUE (name),
    CONSTRAINT ck_envelopes_type
        CHECK (envelope_type in ('cto', 'pea', 'pea_pme', 'assurance_vie', 'per'))
);
CREATE TABLE assets (
    id INTEGER NOT NULL,
    isin VARCHAR(12) NOT NULL,
    name VARCHAR(200) NOT NULL,
    asset_type VARCHAR(10) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    ticker VARCHAR(30),
    exchange VARCHAR(30) NOT NULL,
    price_scale FLOAT NOT NULL,
    manual_price BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_assets_isin_exchange UNIQUE (isin, exchange),
    CONSTRAINT ck_assets_type CHECK (asset_type in ('etf', 'stock', 'fund'))
);
CREATE TABLE fx_rates (
    currency VARCHAR(3) NOT NULL,
    rate_date DATE NOT NULL,
    rate FLOAT NOT NULL,
    source VARCHAR(20) NOT NULL,
    fetched_at DATETIME NOT NULL,
    PRIMARY KEY (currency, rate_date)
);
CREATE TABLE transactions (
    id INTEGER NOT NULL,
    asset_id INTEGER NOT NULL,
    envelope_id INTEGER NOT NULL,
    type VARCHAR(4) NOT NULL,
    trade_date DATE NOT NULL,
    quantity FLOAT NOT NULL,
    unit_price FLOAT NOT NULL,
    fees FLOAT NOT NULL,
    fx_rate FLOAT,
    fx_source VARCHAR(20),
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_transactions_type CHECK (type in ('buy', 'sell')),
    FOREIGN KEY(asset_id) REFERENCES assets (id),
    FOREIGN KEY(envelope_id) REFERENCES envelopes (id)
);
CREATE INDEX ix_transactions_asset_date ON transactions (asset_id, trade_date);
CREATE TABLE price_history (
    asset_id INTEGER NOT NULL,
    price_date DATE NOT NULL,
    price FLOAT NOT NULL,
    source VARCHAR(20) NOT NULL,
    fetched_at DATETIME NOT NULL,
    PRIMARY KEY (asset_id, price_date),
    FOREIGN KEY(asset_id) REFERENCES assets (id)
);
INSERT INTO schema_version VALUES (2, '2026-11-20 09:00:00');
INSERT INTO envelopes VALUES (1, 'PEA Boursorama', 'pea', 'Boursorama', '2019-02-04',
    '2026-11-20 09:00:00');
INSERT INTO assets VALUES
    (1, 'IE00B4L5Y983', 'iShares Core MSCI World', 'etf', 'EUR', 'IWDA.AS', 'AMS', 1.0, 0);
INSERT INTO transactions VALUES
    (1, 1, 1, 'buy', '2023-09-12', 10.0, 80.0, 2.0, NULL, NULL, '2026-11-20 09:01:00');
"""


def fresh_engine(tmp_path: Path) -> Engine:
    return make_engine(f"sqlite:///{tmp_path}/fresh.db")


def v1_engine(tmp_path: Path, purchases: str = V1_PURCHASES) -> Engine:
    """A database file exactly as version 1 left it, holding `purchases` (SQL inserts)."""
    engine = make_engine(f"sqlite:///{tmp_path}/v1.db")
    with engine.connect() as connection:
        connection.connection.executescript(V1_SCHEMA + purchases)
    return engine


def test_new_database_starts_with_a_default_envelope(tmp_path: Path) -> None:
    engine = fresh_engine(tmp_path)
    init_db(engine)

    with Session(engine) as session:
        assert session.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        only = envelopes.all_envelopes(session)
        assert [(e.name, e.envelope_type, e.broker, e.opening_date) for e in only] == [
            ("Compte par défaut", "cto", None, None)
        ]


def test_v1_brokers_become_envelopes(tmp_path: Path) -> None:
    engine = v1_engine(tmp_path)
    init_db(engine)

    with Session(engine) as session:
        assert session.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        found = envelopes.all_envelopes(session)
        assert [(e.name, e.envelope_type, e.broker, e.opening_date) for e in found] == [
            ("Compte par défaut", "cto", None, None),
            ("Courtier A", "cto", "Courtier A", None),
            ("Courtier B", "cto", "Courtier B", None),
        ]

        lots = session.scalars(select(Transaction).order_by(Transaction.id)).all()
        assert [(lot.id, lot.envelope.name) for lot in lots] == [
            (1, "Courtier A"),
            (2, "Courtier A"),
            (3, "Courtier B"),
            (4, "Compte par défaut"),
        ]
        # Nothing else about a purchase changes.
        first = lots[0]
        assert (first.asset.isin, first.trade_date, first.quantity, first.fx_rate) == (
            "US5949181045",
            date(2024, 3, 15),
            10.0,
            1.09,
        )

    columns = {column["name"] for column in inspect(engine).get_columns("transactions")}
    assert "broker" not in columns


def test_starting_again_after_the_migration_changes_nothing(tmp_path: Path) -> None:
    engine = v1_engine(tmp_path)
    init_db(engine)
    init_db(engine)

    with Session(engine) as session:
        assert session.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert len(envelopes.all_envelopes(session)) == 3
        assert len(session.scalars(select(Transaction)).all()) == 4


def test_v1_database_without_purchases_gets_the_default_envelope(tmp_path: Path) -> None:
    engine = v1_engine(tmp_path, purchases="")
    init_db(engine)

    with Session(engine) as session:
        assert session.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        assert [e.name for e in envelopes.all_envelopes(session)] == ["Compte par défaut"]


def test_v1_purchases_all_with_a_broker_need_no_default_envelope(tmp_path: Path) -> None:
    every_lot_has_a_broker = V1_PURCHASES.replace("NULL, NULL, NULL,", "NULL, NULL, 'Courtier B',")
    engine = v1_engine(tmp_path, purchases=every_lot_has_a_broker)
    init_db(engine)

    with Session(engine) as session:
        assert [e.name for e in envelopes.all_envelopes(session)] == ["Courtier A", "Courtier B"]
        lots = session.scalars(select(Transaction).order_by(Transaction.id)).all()
        assert [lot.envelope.name for lot in lots] == [
            "Courtier A",
            "Courtier A",
            "Courtier B",
            "Courtier B",
        ]


def test_v2_envelopes_get_empty_cost_properties(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path}/v2.db")
    with engine.connect() as connection:
        connection.connection.executescript(V2_DATABASE)
    init_db(engine)
    init_db(engine)  # and starting again changes nothing

    with Session(engine) as session:
        assert session.scalar(select(SchemaVersion.version)) == SCHEMA_VERSION
        pea = session.get(Envelope, 1)
        assert (pea.name, pea.opening_date) == ("PEA Boursorama", date(2019, 2, 4))
        # Nothing was filled in yet: no fee, and no tax rate (unknown, not zero).
        assert (pea.management_fee_pct, pea.order_fee_min_eur, pea.exit_tax_rate_before_pct) == (
            None,
            None,
            None,
        )
        assert session.get(Asset, 1).ongoing_charges_pct is None
        lot = session.get(Transaction, 1)
        assert (lot.envelope.name, lot.quantity) == ("PEA Boursorama", 10.0)
