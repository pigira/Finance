"""Yahoo Finance providers (prices through yfinance, FX as a fallback source).

ISIN lookup, as observed against the live service (November 2026):

* `yfinance.Search("<ISIN>")` works (it is what `yfinance.Ticker("<ISIN>")`
  itself uses), but Yahoo usually answers with a single listing: IWDA.L (London,
  USD) for IE00B4L5Y983, although the same ETF also trades in Amsterdam,
  Frankfurt, Milan...
* Searching for that listing's long name returns the other exchanges. Those
  extra candidates are matched by name, not by ISIN, so they are flagged
  (`Listing.isin_match = False`) and the user confirms the right one.
* If yfinance's search fails, Yahoo's public search endpoint is called with httpx.

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
        # By default yfinance prints errors and returns empty data. Make it
        # raise instead, so that the real cause reaches the "stale" tooltip.
        yf.config.debug.hide_exceptions = False

    # ---- search -----------------------------------------------------------

    def search(self, isin: str) -> list[Listing]:
        by_isin = [raw for raw in self._search_raw(isin) if raw.get("symbol")]
        by_name = self._same_name_listings(by_isin)
        known_symbols = {raw["symbol"] for raw in by_isin}
        by_name = [raw for raw in by_name if raw["symbol"] not in known_symbols]

        candidates = [(raw, True) for raw in by_isin] + [(raw, False) for raw in by_name]
        candidates = candidates[:MAX_CANDIDATES]
        if not candidates:
            return []
        # One quote request per candidate, in parallel, to learn its currency.
        with ThreadPoolExecutor(max_workers=MAX_CANDIDATES) as pool:
            return list(pool.map(lambda item: self._to_listing(*item), candidates))

    def _search_raw(self, query: str) -> list[dict[str, Any]]:
        try:
            quotes = self._search_with_yfinance(query)
            if quotes:
                return quotes
        except Exception as exc:  # yfinance raises many unrelated exception types
            logger.warning("yfinance.Search failed for %r (%s), trying httpx", query, exc)
        return self._search_with_httpx(query)

    def _same_name_listings(self, by_isin: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Other exchanges of the security found by ISIN: same long name, same type.

        Best effort: any failure simply means "no extra candidate".
        """
        reference = next((raw for raw in by_isin if raw.get("longname")), None)
        if reference is None:
            return []
        try:
            results = self._search_raw(reference["longname"])
        except ProviderError as exc:
            logger.warning("name search failed for %r: %s", reference["longname"], exc)
            return []
        return [
            raw
            for raw in results
            if raw.get("symbol")
            and raw.get("longname") == reference["longname"]
            and raw.get("quoteType") == reference.get("quoteType")
        ]

    def _search_with_yfinance(self, query: str) -> list[dict[str, Any]]:
        search = yf.Search(
            query,
            max_results=MAX_CANDIDATES,
            news_count=0,
            lists_count=0,
            recommended=0,
            timeout=self._timeout,
        )
        return list(search.quotes)

    def _search_with_httpx(self, query: str) -> list[dict[str, Any]]:
        params = {"q": query, "quotesCount": MAX_CANDIDATES, "newsCount": 0, "listsCount": 0}
        try:
            response = self._client.get(SEARCH_URL, params=params)
            response.raise_for_status()
            return list(response.json().get("quotes", []))
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Yahoo search failed for {query!r}: {exc}") from exc

    def _to_listing(self, raw: dict[str, Any], isin_match: bool) -> Listing:
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
            isin_match=isin_match,
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
            )
            # Filled by the history() call above: no second request. Read single
            # keys only: this object is lazy, and copying it whole (dict(...))
            # would fetch its "tradingPeriods" key with an extra, slow request.
            metadata = yf_ticker.history_metadata
            price = metadata.get("regularMarketPrice")
            quote_currency = str(metadata.get("currency") or "")
        except Exception as exc:  # same reason as in _search_raw
            raise ProviderError(f"Yahoo quote failed for {ticker}: {exc}") from exc

        if price is None:
            closes = history["Close"].dropna() if "Close" in history else []
            if len(closes) == 0:
                raise ProviderError(f"Yahoo returned no price for {ticker}")
            price = closes.iloc[-1]

        price = float(price)
        if not price > 0:  # also rejects NaN
            raise ProviderError(f"Yahoo returned an invalid price for {ticker}: {price}")

        currency, price_scale = normalize_currency(quote_currency)
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
            )
        except Exception as exc:  # same reason as in _search_raw
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
