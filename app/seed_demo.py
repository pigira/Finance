"""Create demo data:

    PRICE_PROVIDER=mock python -m app.seed_demo

Four assets covering every case the app handles: a USD stock with two lots,
an Amsterdam-listed EUR ETF, the same ETF bought in London where it is quoted
in pence (GBX), and an unlisted fund whose price is typed by hand. With PRICE_PROVIDER=mock the
resulting figures are exactly the reference values of the specification.
They are spread over three envelopes: two CTOs at two brokers and a PER.

The command refuses to run on a database that already contains purchases.
"""

import sys
from datetime import date

from sqlalchemy import func, select

from app.config import get_settings
from app.db import init_db, make_engine, make_session_factory
from app.models import Envelope, Transaction
from app.providers import build_fx_provider, build_price_provider
from app.schemas import EnvelopeForm, LotForm
from app.services import envelopes, lots, pricing

DEMO_FUND_ISIN = "ZZ000PECULE5"  # made-up ISIN ("ZZ" is not a country) with a valid check digit

MICROSOFT = {
    "isin": "US5949181045",
    "name": "Microsoft Corporation",
    "asset_type": "stock",
    "ticker": "MSFT",
    "exchange": "NMS",
    "currency": "USD",
}
MSCI_WORLD = {
    "isin": "IE00B4L5Y983",
    "name": "iShares Core MSCI World UCITS ETF USD (Acc)",
    "asset_type": "etf",
    "ticker": "IWDA.AS",
    "exchange": "AMS",
    "currency": "EUR",
}
MSCI_WORLD_LONDON = {
    # Same ISIN as above on another exchange: a distinct asset (other price, other currency).
    **MSCI_WORLD,
    "ticker": "SWDA.L",
    "exchange": "LSE",
    "currency": "GBX",  # quoted in pence: stored as GBP with price_scale 0.01
}
PENSION_FUND = {
    "isin": DEMO_FUND_ISIN,
    "name": "Fonds de pension employeur (démo)",
    "asset_type": "fund",
    "currency": "EUR",
    "manual_price": True,
}

DEMO_ENVELOPES: list[dict[str, object]] = [
    {"name": "CTO Courtier A", "envelope_type": "cto", "broker": "Courtier A"},
    {"name": "CTO Courtier B", "envelope_type": "cto", "broker": "Courtier B"},
    {"name": "PER employeur", "envelope_type": "per", "opening_date": date(2021, 9, 1)},
]

# Unit prices and fees are in the asset currency (pounds, not pence, for London).
DEMO_LOTS: list[dict[str, object]] = [
    {
        **MICROSOFT,
        "trade_date": date(2024, 3, 15),
        "quantity": 10,
        "unit_price": 400.00,
        "fees": 1.00,
        "fx_rate": 1.0900,
        "fx_source": "ecb",
        "envelope": "CTO Courtier A",
    },
    {
        **MICROSOFT,
        "trade_date": date(2025, 6, 2),
        "quantity": 5,
        "unit_price": 450.00,
        "fees": 1.00,
        "fx_rate": 1.1400,
        "fx_source": "ecb",
        "envelope": "CTO Courtier A",
    },
    {
        **MSCI_WORLD,
        "trade_date": date(2023, 9, 12),
        "quantity": 10,
        "unit_price": 80.00,
        "fees": 2.00,
        "envelope": "CTO Courtier B",
    },
    {
        **MSCI_WORLD_LONDON,
        "trade_date": date(2024, 11, 4),
        "quantity": 4,
        "unit_price": 78.40,
        "fees": 0.0,
        "fx_rate": 0.8400,
        "fx_source": "manual",
        "envelope": "CTO Courtier B",
    },
    {
        **PENSION_FUND,
        "trade_date": date(2022, 1, 31),
        "quantity": 120.5,
        "unit_price": 10.00,
        "fees": 0.0,
        "envelope": "PER employeur",
    },
]
PENSION_FUND_CURRENT_PRICE = 11.37


def main() -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)

    with make_session_factory(engine)() as session:
        existing = session.scalar(select(func.count()).select_from(Transaction))
        if existing:
            print(f"The database already contains {existing} purchase(s): nothing was changed.")
            print("Point DATABASE_URL at a new file to create a separate demo database.")
            return 1

        envelope_ids: dict[str, int] = {}
        for values in DEMO_ENVELOPES:
            envelope: Envelope = envelopes.create_envelope(
                session, EnvelopeForm.model_validate(values)
            )
            envelope_ids[envelope.name] = envelope.id

        for values in DEMO_LOTS:
            lot = {**values, "envelope_id": envelope_ids[str(values["envelope"])]}
            transaction = lots.create_lot(session, LotForm.model_validate(lot), settings)
            if transaction.asset.manual_price:
                pricing.set_manual_price(
                    session, transaction.asset, PENSION_FUND_CURRENT_PRICE, settings
                )

        report = pricing.refresh_all(
            session, build_price_provider(settings), build_fx_provider(settings), settings
        )

    print(f"Demo data created in {settings.database_url}: {len(DEMO_LOTS)} purchases, 4 assets.")
    print(
        f"Prices: {report.prices_updated} fetched, {report.prices_failed} failed "
        f"(provider: {settings.price_provider})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
