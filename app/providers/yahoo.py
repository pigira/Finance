"""Yahoo Finance providers (prices through yfinance, FX as a fallback source).

ISIN lookup: yfinance resolves an ISIN with `yfinance.Search` (that is what
`yfinance.Ticker("<ISIN>")` itself does internally, keeping only the first
match). We call `Search` directly to get *every* listing. If that fails or
returns nothing, we call Yahoo's public search endpoint with httpx.

The search answer has no currency, so each candidate is completed with one
quote request. Yahoo spells a quote in pence "GBp": `normalize_currency`
turns it into currency GBP with price_scale 0.01.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import httpx
import yfinance as yf

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

logger = logging.getLogger(__name__)

SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
MAX_CANDIDATES = 8

# Yahoo rejects requests that do not look like a browser.
_BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

# Yahoo quoteType -> our asset type. Anything else is treated as a stock.
_ASSET_TYPES = {"ETF": "etf", "EQUITY": "stock", "MUTUALFUND": "fund"}


class YahooProvider(PriceProvider):
    name = "yahoo"

    def __init__(self, timeout: float = 5.0, http_client: httpx.Client | None = None) -> None:
        self._timeout = timeout
        self._client = http_client or httpx.Client(timeout=timeout, headers=_BROWSER_HEADERS)

    # ---- search -----------------------------------------------------------

    def search(self, isin: str) -> list[Listing]:
        raw_quotes = [raw for raw in self._search_raw(isin) if raw.get("symbol")][:MAX_CANDIDATES]
        if not raw_quotes:
            return []
        # One quote request per candidate, in parallel, to learn its currency.
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(self._listing_from_search_result, raw_quotes))

    def _search_raw(self, isin: str) -> list[dict[str, Any]]:
        try:
            quotes = self._search_with_yfinance(isin)
            if quotes:
                return quotes
        except Exception as exc:  # noqa: BLE001 - yfinance raises many unrelated types
            logger.warning("yfinance.Search failed for %s (%s), trying httpx", isin, exc)
        return self._search_with_httpx(isin)

    def _search_with_yfinance(self, isin: str) -> list[dict[str, Any]]:
        search = yf.Search(
            isin,
            max_results=MAX_CANDIDATES,
            news_count=0,
            lists_count=0,
            recommended=0,
            timeout=self._timeout,
        )
        return list(search.quotes)

    def _search_with_httpx(self, isin: str) -> list[dict[str, Any]]:
        params = {"q": isin, "quotesCount": MAX_CANDIDATES, "newsCount": 0, "listsCount": 0}
        try:
            response = self._client.get(SEARCH_URL, params=params)
            response.raise_for_status()
            return list(response.json().get("quotes", []))
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Yahoo search failed for {isin}: {exc}") from exc

    def _listing_from_search_result(self, raw: dict[str, Any]) -> Listing:
        ticker = str(raw["symbol"])
        try:
            quote = self.get_quote(ticker)
            currency, price_scale = quote.currency, quote.price_scale
        except ProviderError as exc:
            # Still a useful candidate: the user fills in the currency by hand.
            logger.warning("no currency for search candidate %s: %s", ticker, exc)
            currency, price_scale = "", 1.0
        return Listing(
            ticker=ticker,
            exchange=str(raw.get("exchange") or ""),
            exchange_name=str(raw.get("exchDisp") or raw.get("exchange") or ""),
            currency=currency,
            price_scale=price_scale,
            name=str(raw.get("longname") or raw.get("shortname") or ticker),
            asset_type=_ASSET_TYPES.get(str(raw.get("quoteType", "")).upper(), "stock"),
        )

    # ---- quotes -----------------------------------------------------------

    def get_quote(self, ticker: str) -> Quote:
        try:
            yf_ticker = yf.Ticker(ticker)
            history = yf_ticker.history(
                period="5d",
                interval="1d",
                auto_adjust=False,
                actions=False,
                timeout=self._timeout,
                raise_errors=True,
            )
            # Filled by the history() call above: no second request.
            metadata = dict(yf_ticker.history_metadata or {})
        except Exception as exc:  # noqa: BLE001 - see _search_raw
            raise ProviderError(f"Yahoo quote failed for {ticker}: {exc}") from exc

        price = metadata.get("regularMarketPrice")
        if price is None:
            closes = history["Close"].dropna() if "Close" in history else []
            if len(closes) == 0:
                raise ProviderError(f"Yahoo returned no price for {ticker}")
            price = closes.iloc[-1]

        price = float(price)
        if not price > 0:  # also rejects NaN
            raise ProviderError(f"Yahoo returned an invalid price for {ticker}: {price}")

        currency, price_scale = normalize_currency(str(metadata.get("currency") or ""))
        return Quote(ticker=ticker, price=price, currency=currency, price_scale=price_scale)

    def get_history(self, ticker: str, start: date, end: date) -> list[PricePoint]:
        try:
            history = yf.Ticker(ticker).history(
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),  # Yahoo's end is exclusive
                interval="1d",
                auto_adjust=False,
                actions=False,
                timeout=self._timeout,
                raise_errors=True,
            )
        except Exception as exc:  # noqa: BLE001 - see _search_raw
            raise ProviderError(f"Yahoo history failed for {ticker}: {exc}") from exc

        if "Close" not in history:
            return []
        closes = history["Close"].dropna()
        return [
            PricePoint(day=timestamp.date(), close=float(close))
            for timestamp, close in closes.items()
        ]


class YahooFxProvider(FxProvider):
    """EUR-based FX rates from Yahoo tickers such as "EURUSD=X".

    The quote of EURUSD=X is the number of USD per 1 EUR, which is exactly the
    convention used everywhere in this app.
    """

    name = "yahoo"

    def __init__(self, price_provider: PriceProvider) -> None:
        self._prices = price_provider

    def get_rate(self, currency: str, on_date: date) -> FxQuote | None:
        ticker = f"EUR{currency}=X"
        today = utc_now().date()

        if on_date >= today:
            quote = self._prices.get_quote(ticker)
            return FxQuote(currency=currency, rate_date=today, rate=quote.price, source=self.name)

        # A one-week window always contains the last business day before `on_date`.
        points = self._prices.get_history(ticker, on_date - timedelta(days=7), on_date)
        if not points:
            return None
        last = points[-1]
        return FxQuote(currency=currency, rate_date=last.day, rate=last.close, source=self.name)
