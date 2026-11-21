"""SQLAlchemy ORM models (the whole database schema)."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utc_now

ASSET_TYPES = ("etf", "stock", "fund")
ENVELOPE_TYPES = ("cto", "pea", "pea_pme", "assurance_vie", "per")
# Every database holds at least this envelope until another one exists.
DEFAULT_ENVELOPE_NAME = "Compte par défaut"


class SchemaVersion(Base):
    """Single row holding the schema version, for future migrations."""

    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Envelope(Base):
    """A named tax wrapper ("compte": PEA, CTO...) opened at one broker. Holds lots."""

    __tablename__ = "envelopes"
    __table_args__ = (
        UniqueConstraint("name", name="uq_envelopes_name"),
        CheckConstraint(
            f"envelope_type in ({', '.join(repr(code) for code in ENVELOPE_TYPES)})",
            name="ck_envelopes_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    envelope_type: Mapped[str] = mapped_column(String(20))
    broker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # NULL: unknown, the date of the envelope's oldest lot stands in for it.
    opening_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="envelope")


class Asset(Base):
    """A security as listed on one exchange. Identity = (isin, exchange).

    The same ISIN bought on two exchanges (e.g. Amsterdam in EUR and London in
    GBP) is two assets, because price and currency differ.
    """

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("isin", "exchange", name="uq_assets_isin_exchange"),
        CheckConstraint("asset_type in ('etf', 'stock', 'fund')", name="ck_assets_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    isin: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(10))
    # ISO 4217 code of the *major* unit: "GBP" even when the exchange quotes in pence.
    currency: Mapped[str] = mapped_column(String(3))
    ticker: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Exchange code; empty string (not NULL) when there is no listing, so that
    # the unique constraint still applies.
    exchange: Mapped[str] = mapped_column(String(30), default="")
    # Multiplier from the quoted unit to `currency` (0.01 for a quote in pence).
    price_scale: Mapped[float] = mapped_column(Float, default=1.0)
    # True: no API quote; the owner types the current price by hand.
    manual_price: Mapped[bool] = mapped_column(Boolean, default=False)

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="Transaction.trade_date, Transaction.id",
    )


class Transaction(Base):
    """One purchase ("lot"). The 'sell' type exists in the schema but not in v1."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("type in ('buy', 'sell')", name="ck_transactions_type"),
        Index("ix_transactions_asset_date", "asset_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    envelope_id: Mapped[int] = mapped_column(ForeignKey("envelopes.id"))
    type: Mapped[str] = mapped_column(String(4), default="buy")
    trade_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[float] = mapped_column(Float)
    unit_price: Mapped[float] = mapped_column(Float)  # asset currency
    fees: Mapped[float] = mapped_column(Float, default=0.0)  # asset currency
    # Asset-currency units per 1 EUR at purchase. NULL for EUR assets.
    fx_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Where fx_rate came from: provider label ('ecb', ...) or 'manual'.
    fx_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    asset: Mapped[Asset] = relationship(back_populates="transactions")
    envelope: Mapped[Envelope] = relationship(back_populates="transactions")


class PriceHistory(Base):
    """Price cache: one row per asset and per day on which a price was obtained."""

    __tablename__ = "price_history"

    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    price_date: Mapped[date] = mapped_column(Date, primary_key=True)
    price: Mapped[float] = mapped_column(Float)  # raw quote, before price_scale
    source: Mapped[str] = mapped_column(String(20))
    fetched_at: Mapped[datetime] = mapped_column(DateTime)  # UTC


class FxRate(Base):
    """FX cache. rate = units of `currency` per 1 EUR, valid on `rate_date`.

    A row for a weekend or holiday holds the rate of the last business day before it.
    """

    __tablename__ = "fx_rates"

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)  # UTC
