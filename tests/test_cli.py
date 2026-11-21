"""Command-line entry points: demo data and cron refresh."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import refresh, seed_demo
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.isin import is_valid_isin
from app.models import Asset, Envelope, Transaction
from app.services.portfolio import build_portfolio


@pytest.fixture
def cli_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the CLI modules (which read the environment) at a temporary database."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setenv("PRICE_PROVIDER", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_seed_demo_then_refresh(cli_database: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert seed_demo.main() == 0

    settings = get_settings()
    with make_session_factory(make_engine(settings.database_url))() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 4
        assert session.scalar(select(func.count()).select_from(Transaction)) == 5

        view = build_portfolio(session, settings)
        by_type = {position.asset.asset_type for position in view.consolidated_positions}
        assert by_type == {"stock", "etf", "fund"}
        currencies = {position.asset.currency for position in view.consolidated_positions}
        assert currencies == {"EUR", "USD", "GBP"}
        assert any(position.asset.manual_price for position in view.consolidated_positions)
        by_ticker = {position.asset.ticker: position for position in view.consolidated_positions}
        msft = by_ticker["MSFT"]
        assert msft.result.pl_eur == pytest.approx(1172.98, abs=0.01)
        assert not any(position.is_stale for position in view.consolidated_positions)

    # Running it again must not duplicate anything.
    assert seed_demo.main() == 1
    assert "nothing was changed" in capsys.readouterr().out

    assert refresh.main() == 0
    assert "prices: 3 updated, 0 failed" in capsys.readouterr().out


def test_demo_fund_isin_is_valid() -> None:
    assert is_valid_isin(seed_demo.DEMO_FUND_ISIN)


def test_demo_envelopes_get_the_exit_tax_of_their_type(cli_database: None) -> None:
    assert seed_demo.main() == 0
    settings = get_settings()
    with make_session_factory(make_engine(settings.database_url))() as session:
        taxes = {
            envelope.name: envelope.exit_tax_rate_before_pct
            for envelope in session.scalars(select(Envelope))
        }
    assert taxes["CTO Courtier A"] == 31.4
    assert taxes["CTO Courtier B"] == 31.4
    assert taxes["PER employeur"] is None  # no default for a PER
