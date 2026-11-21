"""Refresh logic: cache rows, TTL, and resilience to provider failures."""

from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Asset, FxRate, PriceHistory
from app.providers.base import Listing, PricePoint, PriceProvider, ProviderError, Quote
from app.providers.mock import MockFxProvider, MockProvider
from app.services import pricing
from app.services.fx import get_rate_for_date
from app.services.portfolio import build_portfolio
from tests.conftest import EUR_ETF_LOT, GBX_ETF_LOT, MSFT_LOT_A, MSFT_LOT_B, add_lot

NOON = datetime(2026, 11, 16, 11, 0)  # 12:00 in Amsterdam


class ScriptedProvider(PriceProvider):
    """Returns the quotes it was given; raises for the tickers listed in `failing`."""

    name = "scripted"

    def __init__(self, quotes: dict[str, Quote], failing: dict[str, Exception] | None = None):
        self.quotes = quotes
        self.failing = failing or {}
        self.asked: list[str] = []

    def search(self, isin: str) -> list[Listing]:
        return []

    def get_quote(self, ticker: str) -> Quote:
        self.asked.append(ticker)
        if ticker in self.failing:
            raise self.failing[ticker]
        return self.quotes[ticker]

    def get_history(self, ticker: str, start: date, end: date) -> list[PricePoint]:
        return []


def usd_quote(price: float) -> Quote:
    return Quote(ticker="MSFT", price=price, currency="USD", price_scale=1.0)


def eur_quote(price: float) -> Quote:
    return Quote(ticker="IWDA.AS", price=price, currency="EUR", price_scale=1.0)


def price_rows(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(PriceHistory))


@pytest.fixture
def two_assets(session: Session, settings: Settings) -> None:
    add_lot(session, settings, MSFT_LOT_A)
    add_lot(session, settings, EUR_ETF_LOT)


def test_refresh_upserts_one_row_per_asset_and_per_day(
    two_assets: None, session: Session, settings: Settings
) -> None:
    provider = ScriptedProvider({"MSFT": usd_quote(500.0), "IWDA.AS": eur_quote(95.0)})
    fx = MockFxProvider()

    report = pricing.refresh_all(session, provider, fx, settings, force=True, now=NOON)
    assert (report.prices_updated, report.prices_failed, report.fx_updated) == (2, 0, 1)
    assert price_rows(session) == 2

    # Same day, new price: the row is updated, not duplicated.
    provider.quotes["MSFT"] = usd_quote(510.0)
    later = NOON + timedelta(hours=2)
    pricing.refresh_all(session, provider, fx, settings, force=True, now=later)
    assert price_rows(session) == 2
    msft = pricing.latest_prices(session)[1]
    assert (msft.price, msft.price_date, msft.fetched_at) == (510.0, date(2026, 11, 16), later)
    assert msft.source == "scripted"

    # Next day: a new row; the latest one wins.
    provider.quotes["MSFT"] = usd_quote(520.0)
    pricing.refresh_all(session, provider, fx, settings, force=True, now=NOON + timedelta(days=1))
    assert price_rows(session) == 4
    assert pricing.latest_prices(session)[1].price == 520.0

    usd_rate = session.get(FxRate, ("USD", date(2026, 11, 16)))
    assert usd_rate is not None
    assert usd_rate.rate == pytest.approx(1.10)


def test_on_demand_refresh_respects_the_ttl(
    two_assets: None, session: Session, settings: Settings
) -> None:
    provider = ScriptedProvider({"MSFT": usd_quote(500.0), "IWDA.AS": eur_quote(95.0)})
    fx = MockFxProvider()

    pricing.refresh_all(session, provider, fx, settings, now=NOON)
    assert sorted(provider.asked) == ["IWDA.AS", "MSFT"]
    assert not pricing.refresh_is_due(session, settings, now=NOON + timedelta(minutes=5))

    provider.asked.clear()
    report = pricing.refresh_all(session, provider, fx, settings, now=NOON + timedelta(minutes=5))
    assert provider.asked == []  # fetched 5 minutes ago: still fresh
    assert (report.prices_skipped, report.fx_skipped) == (2, 1)

    assert pricing.refresh_is_due(session, settings, now=NOON + timedelta(minutes=16))
    pricing.refresh_all(session, provider, fx, settings, now=NOON + timedelta(minutes=16))
    assert sorted(provider.asked) == ["IWDA.AS", "MSFT"]

    provider.asked.clear()
    pricing.refresh_all(
        session, provider, fx, settings, force=True, now=NOON + timedelta(minutes=17)
    )
    assert sorted(provider.asked) == ["IWDA.AS", "MSFT"]  # force ignores the TTL


def test_one_failing_ticker_does_not_affect_the_others(
    two_assets: None, session: Session, settings: Settings
) -> None:
    provider = ScriptedProvider({"MSFT": usd_quote(500.0), "IWDA.AS": eur_quote(95.0)})
    fx = MockFxProvider()
    pricing.refresh_all(session, provider, fx, settings, force=True, now=NOON)

    # Next refresh: MSFT times out, the ETF moves.
    provider.failing = {"MSFT": ProviderError("timed out after 5s")}
    provider.quotes["IWDA.AS"] = eur_quote(96.0)
    later = NOON + timedelta(hours=1)
    report = pricing.refresh_all(session, provider, fx, settings, force=True, now=later)

    assert (report.prices_updated, report.prices_failed) == (1, 1)
    prices = pricing.latest_prices(session)
    assert prices[1].price == 500.0  # last known MSFT price is kept
    assert prices[2].price == 96.0
    assert "timed out" in pricing.price_failures[1].message

    view = build_portfolio(session, settings, now=later)
    by_ticker = {position.asset.ticker: position for position in view.consolidated_positions}
    msft, etf = by_ticker["MSFT"], by_ticker["IWDA.AS"]
    assert msft.last_price == 500.0
    assert msft.is_stale
    assert any("timed out" in warning for warning in msft.warnings)
    assert not etf.is_stale

    # A later success clears the failure.
    provider.failing = {}
    pricing.refresh_all(session, provider, fx, settings, force=True, now=later)
    assert pricing.price_failures == {}
    assert not build_portfolio(session, settings, now=later).consolidated_positions[0].is_stale


def test_unexpected_provider_exception_is_contained(
    two_assets: None, session: Session, settings: Settings
) -> None:
    provider = ScriptedProvider(
        {"IWDA.AS": eur_quote(95.0)}, failing={"MSFT": RuntimeError("bug in provider")}
    )
    report = pricing.refresh_all(session, provider, MockFxProvider(), settings, now=NOON)
    assert (report.prices_updated, report.prices_failed) == (1, 1)
    assert "bug in provider" in pricing.price_failures[1].message


def test_failed_tickers_are_not_retried_by_automatic_refreshes(
    two_assets: None, session: Session, settings: Settings
) -> None:
    provider = ScriptedProvider(
        {"IWDA.AS": eur_quote(95.0)}, failing={"MSFT": ProviderError("unknown ticker")}
    )
    fx = MockFxProvider()
    pricing.refresh_all(session, provider, fx, settings, now=NOON)

    soon = NOON + timedelta(minutes=2)
    assert not pricing.refresh_is_due(session, settings, now=soon)
    provider.asked.clear()
    pricing.refresh_all(session, provider, fx, settings, retry_failed=False, now=soon)
    assert provider.asked == []

    # The "Actualiser" button (retry_failed=True) does try again.
    pricing.refresh_all(session, provider, fx, settings, retry_failed=True, now=soon)
    assert provider.asked == ["MSFT"]

    # After the TTL the automatic refresh retries too.
    assert pricing.refresh_is_due(session, settings, now=NOON + timedelta(minutes=16))


def test_no_price_at_all_values_the_position_at_purchase_price(
    session: Session, settings: Settings
) -> None:
    add_lot(session, settings, MSFT_LOT_A)
    provider = ScriptedProvider({}, failing={"MSFT": ProviderError("down")})
    pricing.refresh_all(session, provider, MockFxProvider(), settings, now=NOON)

    position = build_portfolio(session, settings, now=NOON).consolidated_positions[0]
    assert position.last_price == 400.0
    assert position.is_stale
    assert any("Aucun cours disponible" in warning for warning in position.warnings)


def test_fx_failure_keeps_the_page_working(session: Session, settings: Settings) -> None:
    class BrokenFx(MockFxProvider):
        def get_rate(self, currency, on_date):
            raise ProviderError("FX API down")

    add_lot(session, settings, MSFT_LOT_A)
    provider = ScriptedProvider({"MSFT": usd_quote(500.0)})
    report = pricing.refresh_all(session, provider, BrokenFx(), settings, now=NOON)
    assert (report.fx_updated, report.fx_failed) == (0, 1)

    position = build_portfolio(session, settings, now=NOON).consolidated_positions[0]
    # No current rate: the purchase rate is used, so the FX effect is neutral.
    assert position.fx_rate_now == pytest.approx(1.09)
    assert position.result.fx_effect == pytest.approx(0.0)
    assert any("EUR/USD" in warning for warning in position.warnings)


def test_cached_purchase_date_rate_is_not_mistaken_for_the_current_rate(
    session: Session, settings: Settings
) -> None:
    """Regression: prefilling the form caches an old rate with a recent fetch time."""
    fx = MockFxProvider()
    purchase_day = date(2024, 3, 15)
    old_rate = get_rate_for_date(session, fx, settings, "USD", purchase_day, now=NOON).rate
    assert old_rate != pytest.approx(1.10)

    add_lot(session, settings, MSFT_LOT_A)
    provider = ScriptedProvider({"MSFT": usd_quote(500.0)})

    # Only that historical row exists: a refresh is due, and it fetches today's rate.
    assert pricing.refresh_is_due(session, settings, now=NOON)
    report = pricing.refresh_all(session, provider, fx, settings, now=NOON)
    assert report.fx_updated == 1
    position = build_portfolio(session, settings, now=NOON).consolidated_positions[0]
    assert position.fx_rate_now == pytest.approx(1.10)
    assert position.result.value_eur == pytest.approx(4545.45, abs=0.01)
    assert not position.is_stale


def test_old_cached_rate_used_as_a_last_resort_is_flagged(
    session: Session, settings: Settings
) -> None:
    class BrokenFx(MockFxProvider):
        def get_rate(self, currency, on_date):
            raise ProviderError("FX API down")

    get_rate_for_date(session, MockFxProvider(), settings, "USD", date(2024, 3, 15), now=NOON)
    add_lot(session, settings, MSFT_LOT_A)
    pricing.refresh_all(
        session, ScriptedProvider({"MSFT": usd_quote(500.0)}), BrokenFx(), settings, now=NOON
    )

    position = build_portfolio(session, settings, now=NOON).consolidated_positions[0]
    assert position.is_stale
    assert any("Taux EUR/USD du 15/03/2024" in warning for warning in position.warnings)


def test_quote_in_another_currency_is_rejected(session: Session, settings: Settings) -> None:
    add_lot(session, settings, EUR_ETF_LOT)
    wrong = Quote(ticker="IWDA.AS", price=101.0, currency="USD", price_scale=1.0)
    report = pricing.refresh_all(
        session, ScriptedProvider({"IWDA.AS": wrong}), MockFxProvider(), settings, now=NOON
    )
    assert report.prices_failed == 1
    assert price_rows(session) == 0
    assert "USD" in pricing.price_failures[1].message


def test_quote_unit_corrects_a_wrong_price_scale(session: Session, settings: Settings) -> None:
    """The user typed GBP for a listing that is in fact quoted in pence."""
    add_lot(session, settings, {**GBX_ETF_LOT, "currency": "GBP"})
    asset = session.get(Asset, 1)
    assert asset.price_scale == 1.0

    pricing.refresh_all(session, MockProvider(), MockFxProvider(), settings, now=NOON)

    assert asset.price_scale == 0.01
    position = build_portfolio(session, settings, now=NOON).consolidated_positions[0]
    assert position.last_price == pytest.approx(82.50)
    assert position.result.value_eur == pytest.approx(388.24, abs=0.01)


def test_price_older_than_a_day_is_flagged_stale(session: Session, settings: Settings) -> None:
    add_lot(session, settings, EUR_ETF_LOT)
    pricing.refresh_all(session, MockProvider(), MockFxProvider(), settings, now=NOON)

    fresh_view = build_portfolio(session, settings, now=NOON + timedelta(hours=23))
    fresh = fresh_view.consolidated_positions[0]
    assert not fresh.is_stale

    stale_view = build_portfolio(session, settings, now=NOON + timedelta(hours=25))
    stale = stale_view.consolidated_positions[0]
    assert stale.is_stale
    assert "plus de 24 h" in stale.warnings[0]
    assert stale.last_price == 95.0  # the last known price is still shown


def test_portfolio_matches_the_reference_figures(
    demo_portfolio: None, session: Session, settings: Settings
) -> None:
    view = build_portfolio(session, settings)
    by_ticker = {position.asset.ticker: position for position in view.consolidated_positions}

    msft = by_ticker["MSFT"].result
    assert msft.quantity == 15
    assert msft.cost_eur == pytest.approx(5645.20, abs=0.01)
    assert msft.value_eur == pytest.approx(6818.18, abs=0.01)
    assert msft.pl_eur == pytest.approx(1172.98, abs=0.01)
    assert msft.pru_eur == pytest.approx(376.35, abs=0.01)

    assert by_ticker["IWDA.AS"].result.pl_eur == pytest.approx(148.00, abs=0.01)
    assert by_ticker["SWDA.L"].last_price == pytest.approx(82.50)
    assert by_ticker["SWDA.L"].result.value_eur == pytest.approx(388.24, abs=0.01)

    # Default order: by value, largest first.
    values = [position.result.value_eur for position in view.consolidated_positions]
    assert values == sorted(values, reverse=True)

    assert view.has_foreign
    assert view.totals.value_eur == pytest.approx(sum(values))
    shares = [item.share for item in view.allocations["currency"]]
    assert sum(shares) == pytest.approx(1.0)
    assert [item.label for item in view.allocations["currency"]][0] == "EUR"
    assert {item.label for item in view.allocations["type"]} == {"ETF", "Actions", "Fonds"}

    # Two holdings share a name (same ETF on two exchanges): the legend tells them apart.
    asset_labels = [item.label for item in view.allocations["asset"]]
    assert "iShares Core MSCI World UCITS ETF USD (Acc) · IWDA.AS" in asset_labels
    assert "iShares Core MSCI World UCITS ETF USD (Acc) · SWDA.L" in asset_labels
    assert "Microsoft Corporation" in asset_labels


def test_allocation_folds_small_holdings_into_others(
    app: FastAPI, session: Session, settings: Settings
) -> None:
    # Eight EUR assets on eight exchanges of the same ISIN.
    for index in range(8):
        lot = {
            **EUR_ETF_LOT,
            "exchange": f"X{index}",
            "ticker": f"T{index}",
            "quantity": str(index + 1),
        }
        add_lot(session, settings, lot)
    pricing.refresh_all(session, app.state.price_provider, app.state.fx_provider, settings)

    slices = build_portfolio(session, settings).allocations["asset"]
    assert len(slices) == 7
    assert [item.color_slot for item in slices] == [1, 2, 3, 4, 5, 6, 0]
    assert slices[-1].label == "Autres (2)"
    assert sum(item.share for item in slices) == pytest.approx(1.0)


def test_second_lot_reuses_the_asset(session: Session, settings: Settings) -> None:
    add_lot(session, settings, MSFT_LOT_A)
    add_lot(session, settings, MSFT_LOT_B)
    assert session.scalar(select(func.count()).select_from(Asset)) == 1
    assert len(session.get(Asset, 1).transactions) == 2
