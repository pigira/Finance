"""CSV exports of the transactions and the envelopes (a plain-text backup of what the
owner typed)."""

import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Envelope, Transaction
from app.services.envelopes import COST_FIELDS

CSV_COLUMNS = [
    "transaction_id",
    "isin",
    "name",
    "asset_type",
    "ticker",
    "exchange",
    "currency",
    "type",
    "trade_date",
    "quantity",
    "unit_price",
    "fees",
    "fx_rate",
    "fx_source",
    "envelope",
    "created_at",
    "ongoing_charges_pct",  # added later: last, so older exports keep their columns
]


def transactions_csv(session: Session) -> str:
    """Machine-readable CSV: comma separated, ISO dates, "." as decimal separator."""
    statement = (
        select(Transaction)
        .options(joinedload(Transaction.asset), joinedload(Transaction.envelope))
        .order_by(Transaction.trade_date, Transaction.id)
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for transaction in session.scalars(statement):
        asset = transaction.asset
        writer.writerow(
            [
                transaction.id,
                asset.isin,
                asset.name,
                asset.asset_type,
                asset.ticker or "",
                asset.exchange,
                asset.currency,
                transaction.type,
                transaction.trade_date.isoformat(),
                _number(transaction.quantity),
                _number(transaction.unit_price),
                _number(transaction.fees),
                _number(transaction.fx_rate),
                transaction.fx_source or "",
                transaction.envelope.name,
                transaction.created_at.isoformat(timespec="seconds"),
                _number(asset.ongoing_charges_pct),
            ]
        )
    return buffer.getvalue()


ENVELOPE_COLUMNS = [
    "envelope_id",
    "name",
    "envelope_type",
    "broker",
    "opening_date",
    *COST_FIELDS,
    "created_at",
]


def envelopes_csv(session: Session) -> str:
    """Every envelope with all its properties, same conventions as transactions_csv."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(ENVELOPE_COLUMNS)
    for envelope in session.scalars(select(Envelope).order_by(Envelope.id)):
        writer.writerow(
            [
                envelope.id,
                envelope.name,
                envelope.envelope_type,
                envelope.broker or "",
                envelope.opening_date.isoformat() if envelope.opening_date else "",
                *(_number(getattr(envelope, name)) for name in COST_FIELDS),
                envelope.created_at.isoformat(timespec="seconds"),
            ]
        )
    return buffer.getvalue()


def _number(value: float | None) -> str:
    """Every digit of a number (repr() keeps them all), empty when it was left empty."""
    return "" if value is None else repr(value)
