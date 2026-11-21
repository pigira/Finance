"""Shared fixtures. Every test runs offline, on its own temporary database."""

import os
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

# `app.main` builds a module-level application from the environment when it is
# imported. Point it at a throw-away database and at the mock providers
# *before* that import, so that importing it never touches real data or the network.
_IMPORT_DIR = tempfile.mkdtemp(prefix="pecule-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_IMPORT_DIR}/import-time.db"
os.environ["PRICE_PROVIDER"] = "mock"
os.environ["FX_PROVIDER"] = "auto"
os.environ["BACKGROUND_REFRESH_HOURS"] = "0"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas import LotForm  # noqa: E402
from app.services import lots, pricing  # noqa: E402

# The app only answers to its own host names (see app/security.py).
BASE_URL = "http://127.0.0.1:8000"

# Every new database starts with "Compte par défaut", whose id is therefore 1.
DEFAULT_ENVELOPE_ID = "1"

MSFT = {
    "isin": "US5949181045",
    "name": "Microsoft Corporation",
    "asset_type": "stock",
    "ticker": "MSFT",
    "exchange": "NMS",
    "currency": "USD",
}
MSFT_LOT_A = {
    **MSFT,
    "envelope_id": DEFAULT_ENVELOPE_ID,
    "trade_date": "15/03/2024",
    "quantity": "10",
    "unit_price": "400,00",
    "fees": "1,00",
    "fx_rate": "1,0900",
    "fx_source": "ecb",
}
MSFT_LOT_B = {
    **MSFT,
    "envelope_id": DEFAULT_ENVELOPE_ID,
    "trade_date": "02/06/2025",
    "quantity": "5",
    "unit_price": "450,00",
    "fees": "1,00",
    "fx_rate": "1,1400",
    "fx_source": "ecb",
}
EUR_ETF_LOT = {
    "envelope_id": DEFAULT_ENVELOPE_ID,
    "isin": "IE00B4L5Y983",
    "name": "iShares Core MSCI World UCITS ETF USD (Acc)",
    "asset_type": "etf",
    "ticker": "IWDA.AS",
    "exchange": "AMS",
    "currency": "EUR",
    "trade_date": "12/09/2023",
    "quantity": "10",
    "unit_price": "80,00",
    "fees": "2,00",
}
GBX_ETF_LOT = {
    "envelope_id": DEFAULT_ENVELOPE_ID,
    # The same ETF bought in London, where it is quoted in pence.
    "isin": "IE00B4L5Y983",
    "name": "iShares Core MSCI World UCITS ETF USD (Acc)",
    "asset_type": "etf",
    "ticker": "SWDA.L",
    "exchange": "LSE",
    "currency": "GBX",
    "trade_date": "04/11/2024",
    "quantity": "4",
    "unit_price": "78,40",
    "fees": "0",
    "fx_rate": "0,8400",
    "fx_source": "manual",
}
MANUAL_FUND_LOT = {
    "envelope_id": DEFAULT_ENVELOPE_ID,
    "isin": "ZZ000PECULE5",
    "name": "Fonds de pension employeur",
    "asset_type": "fund",
    "currency": "EUR",
    "manual_price": "on",
    "trade_date": "31/01/2022",
    "quantity": "120,5",
    "unit_price": "10,00",
}


@pytest.fixture(autouse=True)
def _clean_failure_registry() -> Iterator[None]:
    """The "last failure" registries are module-level: reset them around each test."""
    pricing.price_failures.clear()
    pricing.fx_failures.clear()
    yield
    pricing.price_failures.clear()
    pricing.fx_failures.clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # ignore a developer's local .env
        database_url=f"sqlite:///{tmp_path}/test.db",
        price_provider="mock",
        fx_provider="mock",
        background_refresh_hours=0,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE_URL) as test_client:
        yield test_client


@pytest.fixture
def session(app: FastAPI) -> Iterator[Session]:
    with app.state.session_factory() as db_session:
        yield db_session


def add_lot(session: Session, settings: Settings, form_values: dict[str, str]) -> int:
    """Create a lot straight through the service layer; returns its id."""
    form = LotForm.model_validate(form_values, context={"today": date(2026, 11, 16)})
    return lots.create_lot(session, form, settings).id


@pytest.fixture
def demo_portfolio(app: FastAPI, session: Session, settings: Settings) -> None:
    """MSFT (2 lots, USD), an EUR ETF, a GBX ETF and a manual fund, all priced."""
    for form_values in (MSFT_LOT_A, MSFT_LOT_B, EUR_ETF_LOT, GBX_ETF_LOT, MANUAL_FUND_LOT):
        add_lot(session, settings, form_values)
    pricing.refresh_all(session, app.state.price_provider, app.state.fx_provider, settings)
