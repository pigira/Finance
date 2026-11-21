"""CSV export of all transactions (a plain-text backup of what the owner typed)."""

import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Transaction

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
                repr(transaction.quantity),  # repr() keeps every digit of a float
                repr(transaction.unit_price),
                repr(transaction.fees),
                "" if transaction.fx_rate is None else repr(transaction.fx_rate),
                transaction.fx_source or "",
                transaction.envelope.name,
                transaction.created_at.isoformat(timespec="seconds"),
            ]
        )
    return buffer.getvalue()
