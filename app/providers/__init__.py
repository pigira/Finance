"""Provider selection from the settings."""

import logging
from datetime import date

from app.config import Settings
from app.providers.base import FxProvider, FxQuote, PriceProvider, ProviderError

logger = logging.getLogger(__name__)


class FallbackFxProvider(FxProvider):
    """Ask `primary`; when it fails or has no rate, ask `fallback`."""

    def __init__(self, primary: FxProvider, fallback: FxProvider) -> None:
        self._primary = primary
        self._fallback = fallback
        self.name = primary.name

    def get_rate(self, currency: str, on_date: date) -> FxQuote | None:
        try:
            quote = self._primary.get_rate(currency, on_date)
        except ProviderError as exc:
            logger.warning("FX provider %s failed (%s), using fallback", self._primary.name, exc)
            quote = None
        if quote is not None:
            return quote
        return self._fallback.get_rate(currency, on_date)


def build_price_provider(settings: Settings) -> PriceProvider:
    if settings.price_provider == "mock":
        from app.providers.mock import MockProvider

        return MockProvider()

    # Imported only when needed: yfinance pulls in pandas, which is slow to load.
    from app.providers.yahoo import YahooProvider

    return YahooProvider(timeout=settings.http_timeout_seconds)


def build_fx_provider(settings: Settings) -> FxProvider:
    kind = settings.effective_fx_provider
    if kind == "mock":
        from app.providers.mock import MockFxProvider

        return MockFxProvider()

    from app.providers.yahoo import YahooFxProvider, YahooProvider

    yahoo_fx = YahooFxProvider(YahooProvider(timeout=settings.http_timeout_seconds))
    if kind == "yahoo":
        return yahoo_fx

    from app.providers.frankfurter import FrankfurterProvider

    frankfurter = FrankfurterProvider(
        base_url=settings.frankfurter_base_url,
        timeout=settings.http_timeout_seconds,
    )
    return FallbackFxProvider(primary=frankfurter, fallback=yahoo_fx)
