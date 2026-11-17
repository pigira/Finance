"""Provider interfaces and the plain data objects they exchange.

A provider is the only place that talks to the outside world. Everything
else in the app depends on these two abstract classes, never on Yahoo or
Frankfurter directly, which is what makes the app testable offline.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

# Some exchanges quote in a minor unit (London quotes many ETFs in pence).
# Map the quoted unit to (ISO currency, multiplier to reach that currency).
MINOR_UNIT_CURRENCIES: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 0.01),  # pence sterling, as spelled by Yahoo
    "GBX": ("GBP", 0.01),  # pence sterling, as spelled by most brokers
    "ZAc": ("ZAR", 0.01),  # South African cents
    "ILA": ("ILS", 0.01),  # Israeli agorot
}


# How a minor-unit listing is spelled in the purchase form (one code per currency).
_FORM_CODE_OF_MINOR_UNIT = {"GBP": "GBX", "ZAR": "ZAc", "ILS": "ILA"}


class ProviderError(Exception):
    """A provider could not answer (network failure, unknown ticker, bad payload)."""


def normalize_currency(quote_currency: str) -> tuple[str, float]:
    """Return (ISO currency, price_scale) for a currency as quoted by an exchange.

    "GBp" and "GBX" give ("GBP", 0.01). "usd" gives ("USD", 1.0). The minor
    unit lookup is case-sensitive on purpose: "GBP" is pounds, "GBp" is pence.
    """
    code = quote_currency.strip()
    if code in MINOR_UNIT_CURRENCIES:
        return MINOR_UNIT_CURRENCIES[code]
    return code.upper(), 1.0


def parse_currency_input(text: str) -> tuple[str, float]:
    """`normalize_currency` for a code typed by a person: "gbx" and " usd " work too."""
    code = text.strip()
    if code not in MINOR_UNIT_CURRENCIES:
        code = code.upper()  # "gbx" -> "GBX"; an exact "GBp" (pence) is left alone
    return normalize_currency(code)


def is_iso_currency(code: str) -> bool:
    return len(code) == 3 and code.isascii() and code.isalpha()


def quoted_currency_code(currency: str, price_scale: float) -> str:
    """Inverse of `normalize_currency`: ("GBP", 0.01) -> "GBX", ("USD", 1.0) -> "USD"."""
    if price_scale == 1.0:
        return currency
    return _FORM_CODE_OF_MINOR_UNIT.get(currency, currency)


@dataclass(frozen=True)
class Listing:
    """One place where an ISIN trades: the answer to "Find listing"."""

    ticker: str
    exchange: str  # exchange code, e.g. "AMS"
    exchange_name: str  # human-readable, e.g. "Amsterdam"
    currency: str  # ISO code of the major unit; "" when unknown
    price_scale: float
    name: str
    asset_type: str  # 'etf', 'stock' or 'fund'
    # False when the listing was found through the asset's name rather than
    # its ISIN: probably the same security on another exchange, to be checked.
    isin_match: bool = True


@dataclass(frozen=True)
class Quote:
    """Latest known price of a ticker."""

    ticker: str
    price: float  # raw, in the quoted unit (pence for a GBX listing)
    currency: str  # ISO code of the major unit; "" when unknown
    price_scale: float


@dataclass(frozen=True)
class PricePoint:
    """One daily close, raw (in the quoted unit)."""

    day: date
    close: float


@dataclass(frozen=True)
class FxQuote:
    """An EUR-based FX rate: `rate` units of `currency` per 1 EUR."""

    currency: str
    rate_date: date  # the day the rate was actually published for
    rate: float
    source: str  # 'ecb', 'yahoo', 'mock'


class PriceProvider(ABC):
    """Source of listings and prices."""

    name: str

    @abstractmethod
    def search(self, isin: str) -> list[Listing]:
        """Return the candidate listings of an ISIN (possibly empty)."""

    @abstractmethod
    def get_quote(self, ticker: str) -> Quote:
        """Return the latest price. Raises ProviderError when unavailable."""

    @abstractmethod
    def get_history(self, ticker: str, start: date, end: date) -> list[PricePoint]:
        """Return daily closes from `start` to `end`, both included, oldest first."""


class FxProvider(ABC):
    """Source of EUR-based FX rates."""

    name: str

    @abstractmethod
    def get_rate(self, currency: str, on_date: date) -> FxQuote | None:
        """Return the rate applicable on `on_date`.

        The returned quote may carry an earlier `rate_date` (last business day)
        when nothing was published on `on_date`. Return None when the provider
        has no rate on or just before that date. Raise ProviderError when the
        provider cannot be reached or does not know the currency.
        """
