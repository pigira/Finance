"""Price and FX refresh: calls the providers and fills the cache tables.

Rules
-----
* Every refresh upserts *today's* row in price_history (one row per asset and
  per day) and in fx_rates (one row per currency and per day).
* An on-demand refresh skips whatever was fetched less than PRICE_TTL_MINUTES
  ago. The background task and the CLI pass force=True.
* A provider failure never raises out of `refresh_all`: it is recorded in
  `price_failures` / `fx_failures` and the previous price stays in use.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.calc import EUR
from app.config import Settings
from app.models import Asset, FxRate, PriceHistory
from app.providers.base import FxProvider, PriceProvider, ProviderError, Quote
from app.services.fx import fetch_rate_with_lookback, is_fresh, store_rate
from app.timeutil import local_today, utc_now

logger = logging.getLogger(__name__)

MAX_PARALLEL_REQUESTS = 8


@dataclass(frozen=True)
class FetchFailure:
    message: str
    attempted_at: datetime


# Last failed attempt per asset id and per currency. Kept in memory only: it
# feeds the "stale" badge tooltip, and a restart simply retries everything.
price_failures: dict[int, FetchFailure] = {}
fx_failures: dict[str, FetchFailure] = {}

# Two refreshes at once (page load + background task) would insert the same rows.
_refresh_lock = threading.Lock()


@dataclass
class RefreshReport:
    prices_updated: int = 0
    prices_failed: int = 0
    prices_skipped: int = 0
    fx_updated: int = 0
    fx_failed: int = 0
    fx_skipped: int = 0


# ---- queries ---------------------------------------------------------------


def held_assets(session: Session) -> list[Asset]:
    """Assets with at least one lot (an asset whose lots were all deleted is hidden)."""
    statement = (
        select(Asset)
        .where(Asset.transactions.any())
        .options(selectinload(Asset.transactions))
        .order_by(Asset.id)
    )
    return list(session.scalars(statement))


def latest_prices(session: Session) -> dict[int, PriceHistory]:
    """Most recent price_history row of every asset, keyed by asset id."""
    newest = (
        select(PriceHistory.asset_id, func.max(PriceHistory.price_date).label("price_date"))
        .group_by(PriceHistory.asset_id)
        .subquery()
    )
    statement = select(PriceHistory).join(
        newest,
        and_(
            PriceHistory.asset_id == newest.c.asset_id,
            PriceHistory.price_date == newest.c.price_date,
        ),
    )
    return {row.asset_id: row for row in session.scalars(statement)}


def latest_fx_rates(session: Session) -> dict[str, FxRate]:
    """Most recent fx_rates row of every currency, keyed by currency code."""
    newest = (
        select(FxRate.currency, func.max(FxRate.rate_date).label("rate_date"))
        .group_by(FxRate.currency)
        .subquery()
    )
    statement = select(FxRate).join(
        newest,
        and_(FxRate.currency == newest.c.currency, FxRate.rate_date == newest.c.rate_date),
    )
    return {row.currency: row for row in session.scalars(statement)}


def foreign_currencies(assets: list[Asset]) -> list[str]:
    return sorted({asset.currency for asset in assets if asset.currency != EUR})


def has_api_price(asset: Asset) -> bool:
    return not asset.manual_price and bool(asset.ticker)


# ---- what needs refreshing -------------------------------------------------


def _is_due(
    fetched_at: datetime | None,
    failure: FetchFailure | None,
    now: datetime,
    settings: Settings,
    force: bool,
    retry_failed: bool,
) -> bool:
    """Decide whether one price (or one rate) must be fetched again."""
    ttl = settings.price_ttl_minutes
    if force:
        return True
    if fetched_at is not None and is_fresh(fetched_at, now, ttl):
        return False
    # A recent failure is not retried by automatic refreshes (page load),
    # otherwise a dead ticker would cost a timeout on every page view.
    failed_recently = failure is not None and is_fresh(failure.attempted_at, now, ttl)
    return retry_failed or not failed_recently


def refresh_is_due(session: Session, settings: Settings, now: datetime | None = None) -> bool:
    """True when the page should trigger an automatic refresh after loading."""
    now = now or utc_now()
    assets = held_assets(session)
    prices = latest_prices(session)
    rates = latest_fx_rates(session)

    for asset in assets:
        if not has_api_price(asset):
            continue
        row = prices.get(asset.id)
        fetched_at = row.fetched_at if row else None
        if _is_due(fetched_at, price_failures.get(asset.id), now, settings, False, False):
            return True

    for currency in foreign_currencies(assets):
        row = rates.get(currency)
        fetched_at = row.fetched_at if row else None
        if _is_due(fetched_at, fx_failures.get(currency), now, settings, False, False):
            return True
    return False


# ---- refresh ---------------------------------------------------------------


def refresh_all(
    session: Session,
    price_provider: PriceProvider,
    fx_provider: FxProvider,
    settings: Settings,
    *,
    force: bool = False,
    retry_failed: bool = True,
    now: datetime | None = None,
) -> RefreshReport:
    """Refresh prices and FX rates of everything currently held. Never raises
    because of a provider: failures are counted in the report."""
    with _refresh_lock:
        now = now or utc_now()
        today = local_today(settings.display_timezone, now)
        report = RefreshReport()
        assets = held_assets(session)

        _refresh_prices(
            session, price_provider, assets, settings, now, today, force, retry_failed, report
        )
        _refresh_fx(session, fx_provider, assets, settings, now, today, force, retry_failed, report)
        session.commit()
        return report


def run_refresh(
    session_factory: sessionmaker[Session],
    price_provider: PriceProvider,
    fx_provider: FxProvider,
    settings: Settings,
) -> RefreshReport:
    """Forced refresh in its own database session (background task and CLI)."""
    with session_factory() as session:
        return refresh_all(session, price_provider, fx_provider, settings, force=True)


def _refresh_prices(
    session: Session,
    provider: PriceProvider,
    assets: list[Asset],
    settings: Settings,
    now: datetime,
    today: date,
    force: bool,
    retry_failed: bool,
    report: RefreshReport,
) -> None:
    prices = latest_prices(session)
    due: list[Asset] = []
    for asset in assets:
        if not has_api_price(asset):
            continue
        row = prices.get(asset.id)
        fetched_at = row.fetched_at if row else None
        failure = price_failures.get(asset.id)
        if _is_due(fetched_at, failure, now, settings, force, retry_failed):
            due.append(asset)
        else:
            report.prices_skipped += 1

    results = _fetch_quotes(provider, [asset.ticker for asset in due if asset.ticker])
    for asset in due:
        result = results[asset.ticker]
        if isinstance(result, Quote):
            error = _store_quote(session, asset, result, provider.name, now, today)
        else:
            error = str(result)

        if error is None:
            price_failures.pop(asset.id, None)
            report.prices_updated += 1
        else:
            logger.warning("price refresh failed for %s (%s): %s", asset.ticker, asset.isin, error)
            price_failures[asset.id] = FetchFailure(error, now)
            report.prices_failed += 1


def _fetch_quotes(provider: PriceProvider, tickers: list[str]) -> dict[str, Quote | ProviderError]:
    """Fetch several quotes in parallel. Errors are returned, not raised."""

    def fetch_one(ticker: str) -> Quote | ProviderError:
        try:
            return provider.get_quote(ticker)
        except ProviderError as exc:
            return exc
        except Exception as exc:  # noqa: BLE001 - a buggy provider must not break the page
            logger.exception("unexpected provider error for %s", ticker)
            return ProviderError(f"unexpected error: {exc}")

    unique_tickers = list(dict.fromkeys(tickers))
    if not unique_tickers:
        return {}
    workers = min(MAX_PARALLEL_REQUESTS, len(unique_tickers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(unique_tickers, pool.map(fetch_one, unique_tickers), strict=True))


def _store_quote(
    session: Session, asset: Asset, quote: Quote, source: str, now: datetime, today: date
) -> str | None:
    """Save a quote as today's price. Returns an error message when it cannot be used."""
    if quote.currency and quote.currency != asset.currency:
        # Wrong listing or wrong currency on the asset: storing this price
        # would silently produce a wrong valuation.
        return f"la cotation est en {quote.currency}, l'actif est enregistré en {asset.currency}"
    if quote.currency and quote.price_scale != asset.price_scale:
        # The provider knows the quoted unit (pence vs pounds) better than a
        # manually typed listing does: trust it.
        asset.price_scale = quote.price_scale

    store_price(session, asset.id, today, quote.price, source, now)
    return None


def store_price(
    session: Session, asset_id: int, price_date: date, price: float, source: str, now: datetime
) -> None:
    """Insert or update the price_history row of (asset, day)."""
    session.merge(
        PriceHistory(
            asset_id=asset_id,
            price_date=price_date,
            price=price,
            source=source,
            fetched_at=now,
        )
    )


def _refresh_fx(
    session: Session,
    provider: FxProvider,
    assets: list[Asset],
    settings: Settings,
    now: datetime,
    today: date,
    force: bool,
    retry_failed: bool,
    report: RefreshReport,
) -> None:
    rates = latest_fx_rates(session)
    for currency in foreign_currencies(assets):
        row = rates.get(currency)
        fetched_at = row.fetched_at if row else None
        if not _is_due(fetched_at, fx_failures.get(currency), now, settings, force, retry_failed):
            report.fx_skipped += 1
            continue

        error: str | None = None
        try:
            quote = fetch_rate_with_lookback(provider, currency, today)
            if quote is None:
                error = "aucun taux publié récemment"
            else:
                store_rate(session, quote, rate_date=today, fetched_at=now)
        except ProviderError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - same reason as in _fetch_quotes
            logger.exception("unexpected FX provider error for %s", currency)
            error = f"unexpected error: {exc}"

        if error is None:
            fx_failures.pop(currency, None)
            report.fx_updated += 1
        else:
            logger.warning("FX refresh failed for %s: %s", currency, error)
            fx_failures[currency] = FetchFailure(error, now)
            report.fx_failed += 1


def set_manual_price(
    session: Session, asset: Asset, price: float, settings: Settings, now: datetime | None = None
) -> None:
    """Record a price typed by the owner (in the asset currency) as today's price."""
    now = now or utc_now()
    today = local_today(settings.display_timezone, now)
    # The stored price is "raw": undo the scale so that price * price_scale gives it back.
    store_price(session, asset.id, today, price / asset.price_scale, "manual", now)
    session.commit()
