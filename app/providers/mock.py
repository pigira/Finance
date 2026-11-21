"""Deterministic, offline providers (PRICE_PROVIDER=mock).

Used by the tests, by the demo data and whenever you want to run the app
without network access. The numbers are made up but stable: the same input
always gives the same output.
"""

import math
from datetime import date, timedelta

from app.providers.base import (
    FxProvider,
    FxQuote,
    Listing,
    PricePoint,
    PriceProvider,
    ProviderError,
    Quote,
    normalize_currency,
)
from app.timeutil import utc_now

_MSCI_WORLD = "iShares Core MSCI World UCITS ETF USD (Acc)"
_FTSE_ALL_WORLD = "Vanguard FTSE All-World UCITS ETF USD Accumulation"

# ISIN -> listings. Currencies are spelled as an exchange would quote them.
_LISTINGS: dict[str, list[tuple[str, str, str, str, str, str]]] = {
    # (ticker, exchange code, exchange name, quoted currency, name, asset type)
    "US5949181045": [
        ("MSFT", "NMS", "NASDAQ", "USD", "Microsoft Corporation", "stock"),
        ("MSF.DE", "GER", "XETRA", "EUR", "Microsoft Corporation", "stock"),
    ],
    "IE00B4L5Y983": [
        ("IWDA.AS", "AMS", "Amsterdam", "EUR", _MSCI_WORLD, "etf"),
        ("SWDA.L", "LSE", "London", "GBp", _MSCI_WORLD, "etf"),
        ("EUNL.DE", "GER", "XETRA", "EUR", _MSCI_WORLD, "etf"),
    ],
    "IE00BK5BQT80": [
        ("VWCE.DE", "GER", "XETRA", "EUR", _FTSE_ALL_WORLD, "etf"),
        ("VWRA.L", "LSE", "London", "USD", _FTSE_ALL_WORLD, "etf"),
    ],
}

# ticker -> (raw price, quoted currency). The first three match the reference
# vectors of the specification (MSFT 500 USD, EUR ETF 95, pence quote 8250).
_QUOTES: dict[str, tuple[float, str]] = {
    "MSFT": (500.00, "USD"),
    "IWDA.AS": (95.00, "EUR"),
    "SWDA.L": (8250.0, "GBp"),
    "MSF.DE": (454.55, "EUR"),
    "EUNL.DE": (95.02, "EUR"),
    "VWCE.DE": (128.40, "EUR"),
    "VWRA.L": (141.20, "USD"),
}

# Units of currency per 1 EUR "now".
_FX_NOW: dict[str, float] = {
    "USD": 1.1000,
    "GBP": 0.8500,
    "CHF": 0.9500,
    "JPY": 160.00,
    "SEK": 11.000,
    "NOK": 11.500,
    "DKK": 7.4600,
    "CAD": 1.5000,
    "AUD": 1.6500,
}


def _is_business_day(day: date) -> bool:
    return day.weekday() < 5  # Monday=0 ... Friday=4


def _last_business_day(day: date) -> date:
    while not _is_business_day(day):
        day -= timedelta(days=1)
    return day


def _wiggle(day: date, amplitude: float) -> float:
    """A smooth, repeatable variation factor around 1.0 that depends only on the date."""
    return 1.0 + amplitude * math.sin(day.toordinal() / 58.0)


class MockProvider(PriceProvider):
    name = "mock"

    def search(self, isin: str) -> list[Listing]:
        rows = _LISTINGS.get(isin)
        if rows is None:
            # Unknown ISIN: invent one EUR listing so that the form flow still works offline.
            rows = [
                (f"MOCK-{isin[-4:]}", "MOCK", "Mock Exchange", "EUR", f"Mock asset {isin}", "etf")
            ]
        return [self._to_listing(row) for row in rows]

    def get_quote(self, ticker: str) -> Quote:
        if ticker.upper().startswith("FAIL"):
            # Convention for demos and tests: such a ticker always fails.
            raise ProviderError(f"mock: no quote for {ticker}")
        price, quote_currency = _QUOTES.get(ticker, (self._invented_price(ticker), "EUR"))
        currency, price_scale = normalize_currency(quote_currency)
        return Quote(ticker=ticker, price=price, currency=currency, price_scale=price_scale)

    def get_history(self, ticker: str, start: date, end: date) -> list[PricePoint]:
        latest = self.get_quote(ticker).price
        points = []
        day = start
        while day <= end:
            if _is_business_day(day):
                points.append(PricePoint(day=day, close=round(latest * _wiggle(day, 0.10), 4)))
            day += timedelta(days=1)
        return points

    @staticmethod
    def _to_listing(row: tuple[str, str, str, str, str, str]) -> Listing:
        ticker, exchange, exchange_name, quote_currency, name, asset_type = row
        currency, price_scale = normalize_currency(quote_currency)
        return Listing(
            ticker=ticker,
            exchange=exchange,
            exchange_name=exchange_name,
            currency=currency,
            price_scale=price_scale,
            name=name,
            asset_type=asset_type,
        )

    @staticmethod
    def _invented_price(ticker: str) -> float:
        # Not hash(): Python salts it per process, which would not be repeatable.
        checksum = sum(ord(char) * (index + 1) for index, char in enumerate(ticker))
        return 20.0 + (checksum % 18000) / 100.0


class MockFxProvider(FxProvider):
    """Fixed rates "now", a repeatable variation in the past, nothing on weekends.

    Like the ECB, it publishes no rate on Saturdays and Sundays: asking for
    such a date answers with the previous Friday (see `FxQuote.rate_date`).
    """

    name = "mock"

    def __init__(self, today: date | None = None) -> None:
        # The day whose rate is "current". None: the real date (demo, offline mode);
        # tests that freeze time give it, so that their rates never depend on the clock.
        self._today = today

    def get_rate(self, currency: str, on_date: date) -> FxQuote | None:
        if currency not in _FX_NOW:
            raise ProviderError(f"mock: unknown currency {currency}")

        today = self._today or utc_now().date()
        effective_day = _last_business_day(min(on_date, today))
        rate = _FX_NOW[currency]
        if (today - effective_day).days > 4:
            rate = round(rate * _wiggle(effective_day, 0.04), 4)
        return FxQuote(currency=currency, rate_date=effective_day, rate=rate, source=self.name)
