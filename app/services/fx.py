"""FX rate lookup with a database cache.

All rates are EUR-based: units of foreign currency per 1 EUR.
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import FxRate
from app.providers.base import FxProvider, FxQuote, ProviderError
from app.timeutil import local_today, utc_now

# Longest closure we need to bridge: Easter (Good Friday to Easter Monday) is
# four days without ECB publication; a week leaves a comfortable margin.
MAX_LOOKBACK_DAYS = 7


def fetch_rate_with_lookback(provider: FxProvider, currency: str, on_date: date) -> FxQuote | None:
    """Rate applicable on `on_date`: that day's, else the last one published before it.

    Frankfurter does this by itself (a Saturday is answered with Friday's
    rate). The loop makes the rule hold for any provider: when one answers
    "nothing published that day", we ask for the day before, and so on.
    """
    for days_back in range(MAX_LOOKBACK_DAYS + 1):
        quote = provider.get_rate(currency, on_date - timedelta(days=days_back))
        if quote is not None:
            return quote
    return None


def is_fresh(fetched_at: datetime, now: datetime, ttl_minutes: int) -> bool:
    return now - fetched_at < timedelta(minutes=ttl_minutes)


def store_rate(session: Session, quote: FxQuote, rate_date: date, fetched_at: datetime) -> None:
    """Insert or update the cache row of (currency, rate_date)."""
    session.merge(
        FxRate(
            currency=quote.currency,
            rate_date=rate_date,
            rate=quote.rate,
            source=quote.source,
            fetched_at=fetched_at,
        )
    )


def get_rate_for_date(
    session: Session,
    provider: FxProvider,
    settings: Settings,
    currency: str,
    on_date: date,
    now: datetime | None = None,
) -> FxQuote:
    """Rate to prefill for a purchase made on `on_date` (cached in fx_rates).

    A past date never changes, so its cached row is final. Today's row follows
    the usual TTL because the ECB publishes during the afternoon.
    Raises ProviderError when no rate can be obtained.
    """
    now = now or utc_now()
    today = local_today(settings.display_timezone, now)

    cached = session.get(FxRate, (currency, on_date))
    if cached is not None:
        is_final = on_date < today
        if is_final or is_fresh(cached.fetched_at, now, settings.price_ttl_minutes):
            return FxQuote(currency, on_date, cached.rate, cached.source)

    quote = fetch_rate_with_lookback(provider, currency, on_date)
    if quote is None:
        raise ProviderError(f"no {currency} rate published on or before {on_date.isoformat()}")

    # Stored under the requested date: "the rate that applies on that day".
    store_rate(session, quote, rate_date=on_date, fetched_at=now)
    session.commit()
    return quote
