"""Price providers: mock determinism and the Yahoo adapter (yfinance is faked)."""

from datetime import date

import httpx
import pandas as pd
import pytest

from app.providers import yahoo
from app.providers.base import (
    PricePoint,
    ProviderError,
    normalize_currency,
    parse_currency_input,
    quoted_currency_code,
)
from app.providers.mock import MockProvider
from app.providers.yahoo import YahooFxProvider, YahooProvider

# ---- currency units -----------------------------------------------------------


def test_pence_quotes_are_normalized_to_pounds() -> None:
    assert normalize_currency("GBp") == ("GBP", 0.01)
    assert normalize_currency("GBX") == ("GBP", 0.01)
    assert normalize_currency("GBP") == ("GBP", 1.0)
    assert normalize_currency("usd") == ("USD", 1.0)
    assert parse_currency_input(" gbx ") == ("GBP", 0.01)
    assert quoted_currency_code("GBP", 0.01) == "GBX"
    assert quoted_currency_code("GBP", 1.0) == "GBP"
    assert quoted_currency_code("USD", 1.0) == "USD"


# ---- mock ---------------------------------------------------------------------


def test_mock_provider_is_deterministic() -> None:
    first, second = MockProvider(), MockProvider()
    assert first.get_quote("MSFT") == second.get_quote("MSFT")
    assert first.get_quote("UNKNOWN.XX") == second.get_quote("UNKNOWN.XX")
    assert first.search("IE00B4L5Y983") == second.search("IE00B4L5Y983")
    start, end = date(2024, 3, 11), date(2024, 3, 17)
    assert first.get_history("MSFT", start, end) == second.get_history("MSFT", start, end)


def test_mock_quotes_match_the_reference_values() -> None:
    provider = MockProvider()
    assert provider.get_quote("MSFT").price == 500.00
    gbx_quote = provider.get_quote("VWRP.L")
    assert (gbx_quote.price, gbx_quote.currency, gbx_quote.price_scale) == (8250.0, "GBP", 0.01)


def test_mock_search_returns_listings_with_normalized_currency() -> None:
    listings = MockProvider().search("IE00B4L5Y983")
    by_ticker = {listing.ticker: listing for listing in listings}
    assert by_ticker["IWDA.AS"].currency == "EUR"
    assert by_ticker["IWDA.AS"].exchange == "AMS"
    assert (by_ticker["SWDA.L"].currency, by_ticker["SWDA.L"].price_scale) == ("GBP", 0.01)


def test_mock_history_has_business_days_only() -> None:
    points = MockProvider().get_history("MSFT", date(2024, 3, 11), date(2024, 3, 17))
    assert [point.day.weekday() for point in points] == [0, 1, 2, 3, 4]


def test_mock_fail_tickers_raise() -> None:
    with pytest.raises(ProviderError):
        MockProvider().get_quote("FAIL.XX")


# ---- Yahoo (yfinance replaced by fakes: no network) ----------------------------

YAHOO_SEARCH_QUOTES = [
    {
        "symbol": "IWDA.AS",
        "longname": "iShares Core MSCI World UCITS ETF USD (Acc)",
        "exchange": "AMS",
        "exchDisp": "Amsterdam",
        "quoteType": "ETF",
    },
    {
        "symbol": "SWDA.L",
        "shortname": "ISHARES CORE MSCI WORLD",
        "exchange": "LSE",
        "exchDisp": "London",
        "quoteType": "ETF",
    },
    {"shortname": "an entry without symbol is ignored"},
]

YAHOO_METADATA = {
    "IWDA.AS": {"currency": "EUR", "regularMarketPrice": 95.4},
    "SWDA.L": {"currency": "GBp", "regularMarketPrice": 8075.0},
    "EURUSD=X": {"currency": "USD", "regularMarketPrice": 1.0875},
    "NOMETA.L": {"currency": "GBp"},
}


class FakeTicker:
    """Stands in for yfinance.Ticker: history() then history_metadata."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, **kwargs) -> pd.DataFrame:
        if self.symbol not in YAHOO_METADATA:
            raise RuntimeError(f"{self.symbol}: possibly delisted")
        assert kwargs["timeout"] == 5.0  # the 5 s limit is passed down to yfinance
        index = pd.to_datetime(["2024-03-13", "2024-03-14", "2024-03-15"])
        return pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=index)

    @property
    def history_metadata(self) -> dict:
        return YAHOO_METADATA[self.symbol]


class FakeSearch:
    def __init__(self, query: str, **kwargs) -> None:
        self.quotes = YAHOO_SEARCH_QUOTES


class FailingSearch:
    def __init__(self, query: str, **kwargs) -> None:
        raise RuntimeError("yfinance search is broken")


@pytest.fixture
def fake_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yahoo.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(yahoo.yf, "Search", FakeSearch)


def unreachable_http_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the httpx fallback should not have been used")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_yahoo_search_lists_every_listing_with_its_currency(fake_yfinance: None) -> None:
    listings = YahooProvider(http_client=unreachable_http_client()).search("IE00B4L5Y983")

    assert [listing.ticker for listing in listings] == ["IWDA.AS", "SWDA.L"]
    amsterdam, london = listings
    assert (amsterdam.exchange, amsterdam.exchange_name) == ("AMS", "Amsterdam")
    assert (amsterdam.currency, amsterdam.price_scale) == ("EUR", 1.0)
    assert amsterdam.asset_type == "etf"
    assert amsterdam.name == "iShares Core MSCI World UCITS ETF USD (Acc)"
    # Quoted in pence by Yahoo ("GBp"): stored as GBP with a 0.01 scale.
    assert (london.currency, london.price_scale) == ("GBP", 0.01)
    assert london.name == "ISHARES CORE MSCI WORLD"


def test_yahoo_search_falls_back_to_the_http_endpoint(
    fake_yfinance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(yahoo.yf, "Search", FailingSearch)
    requested: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url)
        return httpx.Response(200, json={"quotes": YAHOO_SEARCH_QUOTES[:1]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    listings = YahooProvider(http_client=client).search("IE00B4L5Y983")

    assert [listing.ticker for listing in listings] == ["IWDA.AS"]
    assert requested[0].host == "query2.finance.yahoo.com"
    assert requested[0].params["q"] == "IE00B4L5Y983"


def test_yahoo_search_failure_raises_provider_error(
    fake_yfinance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(yahoo.yf, "Search", FailingSearch)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    with pytest.raises(ProviderError):
        YahooProvider(http_client=client).search("IE00B4L5Y983")


def test_yahoo_search_keeps_a_candidate_whose_quote_fails(
    fake_yfinance: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SearchWithUnknownTicker:
        def __init__(self, query: str, **kwargs) -> None:
            self.quotes = [{"symbol": "GONE.PA", "exchange": "PAR", "quoteType": "EQUITY"}]

    monkeypatch.setattr(yahoo.yf, "Search", SearchWithUnknownTicker)
    (listing,) = YahooProvider(http_client=unreachable_http_client()).search("FR0000000000")
    assert (listing.ticker, listing.currency, listing.asset_type) == ("GONE.PA", "", "stock")


def test_yahoo_quote_in_pence(fake_yfinance: None) -> None:
    quote = YahooProvider().get_quote("SWDA.L")
    assert (quote.price, quote.currency, quote.price_scale) == (8075.0, "GBP", 0.01)


def test_yahoo_quote_uses_the_last_close_without_market_price(fake_yfinance: None) -> None:
    quote = YahooProvider().get_quote("NOMETA.L")
    assert quote.price == 3.0


def test_yahoo_quote_failure_raises_provider_error(fake_yfinance: None) -> None:
    with pytest.raises(ProviderError):
        YahooProvider().get_quote("DELISTED")


def test_yahoo_history_returns_daily_closes(fake_yfinance: None) -> None:
    points = YahooProvider().get_history("IWDA.AS", date(2024, 3, 13), date(2024, 3, 15))
    assert points == [
        PricePoint(date(2024, 3, 13), 1.0),
        PricePoint(date(2024, 3, 14), 2.0),
        PricePoint(date(2024, 3, 15), 3.0),
    ]


def test_yahoo_fx_uses_eur_pair_tickers(fake_yfinance: None) -> None:
    provider = YahooFxProvider(YahooProvider())

    latest = provider.get_rate("USD", date(2999, 1, 1))  # a date in the future = "now"
    assert latest is not None
    assert (latest.rate, latest.source) == (1.0875, "yahoo")

    # A past Saturday: the last close on or before it (Friday) is used.
    historical = provider.get_rate("USD", date(2024, 3, 16))
    assert historical is not None
    assert (historical.rate_date, historical.rate) == (date(2024, 3, 15), 3.0)
