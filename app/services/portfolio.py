"""Builds everything the portfolio page shows, from the database and calc.py.

This module only reads the cache tables: it never calls a provider, so
rendering the page stays fast and cannot fail because of the network.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import calc
from app.calc import EUR
from app.config import Settings
from app.formatting import format_datetime
from app.models import Asset, FxRate, PriceHistory, Transaction
from app.services import pricing
from app.timeutil import utc_now

ASSET_TYPE_LABELS = {"etf": "ETF", "stock": "Actions", "fund": "Fonds"}

# The donut shows at most this many named slices; the rest is folded into
# "Autres". Six is the largest count whose neighbouring colours all stay
# distinguishable, including for colour-blind readers (see DECISIONS.md).
MAX_NAMED_SLICES = 6
OTHER_SLICE_COLOR = 0  # colour slot of the neutral "Autres" slice


@dataclass(frozen=True)
class LotView:
    transaction: Transaction
    result: calc.LotResult


@dataclass
class PositionView:
    asset: Asset
    lots: list[LotView]
    result: calc.AssetResult
    last_price: float  # in the asset currency (already multiplied by price_scale)
    price_updated_at: datetime | None
    fx_rate_now: float
    # Reasons why the valuation may be out of date. Empty = all good.
    warnings: list[str] = field(default_factory=list)

    @property
    def is_foreign(self) -> bool:
        return self.asset.currency != EUR

    @property
    def is_stale(self) -> bool:
        return bool(self.warnings)

    @property
    def has_several_lots(self) -> bool:
        return len(self.lots) > 1


@dataclass(frozen=True)
class AllocationSlice:
    label: str
    value_eur: float
    share: float  # between 0 and 1
    color_slot: int  # 1..8 = categorical palette, 0 = neutral "Autres"


@dataclass
class PortfolioView:
    positions: list[PositionView]
    totals: calc.PortfolioResult
    allocations: dict[str, list[AllocationSlice]]
    last_refresh_at: datetime | None
    refresh_is_due: bool

    @property
    def is_empty(self) -> bool:
        return not self.positions

    @property
    def has_foreign(self) -> bool:
        return any(position.is_foreign for position in self.positions)

    @property
    def allocation_data(self) -> dict[str, list[dict[str, object]]]:
        """The allocations as plain dicts, embedded as JSON for the donut chart."""
        return {key: [asdict(item) for item in slices] for key, slices in self.allocations.items()}


def build_portfolio(
    session: Session, settings: Settings, now: datetime | None = None
) -> PortfolioView:
    now = now or utc_now()
    prices = pricing.latest_prices(session)
    rates = pricing.latest_fx_rates(session)

    positions = [
        _build_position(asset, prices.get(asset.id), rates.get(asset.currency), settings, now)
        for asset in pricing.held_assets(session)
    ]
    positions.sort(key=lambda position: position.result.value_eur, reverse=True)

    return PortfolioView(
        positions=positions,
        totals=calc.compute_portfolio([position.result for position in positions]),
        allocations=_build_allocations(positions),
        last_refresh_at=_last_refresh_at(prices, rates),
        refresh_is_due=pricing.refresh_is_due(session, settings, now),
    )


def last_refresh_at(session: Session) -> datetime | None:
    """Time of the most recent successful provider fetch (shown in the top bar)."""
    return _last_refresh_at(pricing.latest_prices(session), pricing.latest_fx_rates(session))


def _last_refresh_at(prices: dict[int, PriceHistory], rates: dict[str, FxRate]) -> datetime | None:
    fetched = [row.fetched_at for row in prices.values() if row.source != "manual"]
    fetched += [row.fetched_at for row in rates.values()]
    return max(fetched, default=None)


# ---- one position ----------------------------------------------------------


def _build_position(
    asset: Asset,
    price_row: PriceHistory | None,
    fx_row: FxRate | None,
    settings: Settings,
    now: datetime,
) -> PositionView:
    warnings: list[str] = []
    stale_after = timedelta(hours=settings.stale_after_hours)
    latest_lot = asset.transactions[-1]  # transactions are ordered by trade date

    raw_price = _current_raw_price(
        asset, price_row, latest_lot, settings, now, stale_after, warnings
    )
    fx_rate_now = _current_fx_rate(asset, fx_row, latest_lot, settings, now, stale_after, warnings)
    market = calc.Market(
        last_price=raw_price, price_scale=asset.price_scale, fx_rate_now=fx_rate_now
    )

    lots = [
        LotView(
            transaction, calc.compute_lot(_to_calc_lot(asset, transaction, fx_rate_now), market)
        )
        for transaction in asset.transactions
    ]
    return PositionView(
        asset=asset,
        lots=lots,
        result=calc.compute_asset([lot.result for lot in lots]),
        last_price=calc.scaled_price(market),
        price_updated_at=price_row.fetched_at if price_row else None,
        fx_rate_now=fx_rate_now,
        warnings=warnings,
    )


def _to_calc_lot(asset: Asset, transaction: Transaction, fx_rate_now: float) -> calc.Lot:
    # A foreign lot always has a rate (the form requires it). Should one be
    # missing anyway, using today's rate shows a neutral FX effect.
    foreign_rate_buy = transaction.fx_rate or fx_rate_now
    return calc.Lot(
        quantity=transaction.quantity,
        unit_price=transaction.unit_price,
        fees=transaction.fees,
        fx_rate_buy=1.0 if asset.currency == EUR else foreign_rate_buy,
        tx_type=transaction.type,
    )


def _current_raw_price(
    asset: Asset,
    price_row: PriceHistory | None,
    latest_lot: Transaction,
    settings: Settings,
    now: datetime,
    stale_after: timedelta,
    warnings: list[str],
) -> float:
    """Latest cached quote; when there is none at all, the last purchase price."""
    if price_row is None:
        if not asset.manual_price:
            warnings.append("Aucun cours disponible : valorisé au dernier prix d'achat")
        # unit_price is in the asset currency; a raw price is in the quoted unit.
        raw_price = latest_lot.unit_price / asset.price_scale
    else:
        raw_price = price_row.price
        # A manual price is as fresh as its owner wants it to be: never flagged.
        if not asset.manual_price and now - price_row.fetched_at > stale_after:
            when = format_datetime(price_row.fetched_at, settings.display_timezone)
            warnings.append(f"Cours du {when} (plus de {settings.stale_after_hours} h)")

    failure = pricing.price_failures.get(asset.id)
    if failure is not None and not asset.manual_price:
        warnings.append(f"Échec de la dernière mise à jour : {failure.message}")
    return raw_price


def _current_fx_rate(
    asset: Asset,
    fx_row: FxRate | None,
    latest_lot: Transaction,
    settings: Settings,
    now: datetime,
    stale_after: timedelta,
    warnings: list[str],
) -> float:
    """Latest cached EUR rate of the asset currency (1.0 for EUR assets)."""
    if asset.currency == EUR:
        return 1.0

    pair = f"EUR/{asset.currency}"
    if fx_row is None:
        warnings.append(f"Taux {pair} indisponible : taux du dernier achat utilisé")
        rate = latest_lot.fx_rate or 1.0
    else:
        rate = fx_row.rate
        if now - fx_row.fetched_at > stale_after:
            when = format_datetime(fx_row.fetched_at, settings.display_timezone)
            warnings.append(f"Taux {pair} du {when} (plus de {settings.stale_after_hours} h)")

    failure = pricing.fx_failures.get(asset.currency)
    if failure is not None:
        warnings.append(f"Échec de la mise à jour du taux {pair} : {failure.message}")
    return rate


# ---- allocation (donut data) -----------------------------------------------


def _build_allocations(positions: list[PositionView]) -> dict[str, list[AllocationSlice]]:
    total = sum(position.result.value_eur for position in positions)
    return {
        "asset": _slices_by_asset(positions, total),
        "type": _slices_by_type(positions, total),
        "currency": _slices_by_currency(positions, total),
    }


def _slices_by_asset(positions: list[PositionView], total: float) -> list[AllocationSlice]:
    """Largest holdings first; everything after the sixth is folded into "Autres"."""
    named = positions[:MAX_NAMED_SLICES]  # positions are already sorted by value
    others = positions[MAX_NAMED_SLICES:]
    slices = [
        _slice(position.asset.name, position.result.value_eur, total, slot)
        for slot, position in enumerate(named, start=1)
    ]
    if others:
        others_value = sum(position.result.value_eur for position in others)
        slices.append(_slice(f"Autres ({len(others)})", others_value, total, OTHER_SLICE_COLOR))
    return slices


def _slices_by_type(positions: list[PositionView], total: float) -> list[AllocationSlice]:
    """Fixed order and colours: an asset type keeps its colour whatever its weight."""
    slices = []
    for slot, (asset_type, label) in enumerate(ASSET_TYPE_LABELS.items(), start=1):
        value = sum(p.result.value_eur for p in positions if p.asset.asset_type == asset_type)
        if value > 0:
            slices.append(_slice(label, value, total, slot))
    return slices


def _slices_by_currency(positions: list[PositionView], total: float) -> list[AllocationSlice]:
    """EUR first, then the other currencies by decreasing weight."""
    by_currency: dict[str, float] = {}
    for position in positions:
        currency = position.asset.currency
        by_currency[currency] = by_currency.get(currency, 0.0) + position.result.value_eur

    ordered = sorted(by_currency.items(), key=lambda item: (item[0] != EUR, -item[1]))
    named, others = ordered[:MAX_NAMED_SLICES], ordered[MAX_NAMED_SLICES:]
    slices = [
        _slice(currency, value, total, slot)
        for slot, (currency, value) in enumerate(named, start=1)
    ]
    if others:
        others_value = sum(value for _currency, value in others)
        slices.append(_slice(f"Autres ({len(others)})", others_value, total, OTHER_SLICE_COLOR))
    return slices


def _slice(label: str, value_eur: float, total: float, color_slot: int) -> AllocationSlice:
    return AllocationSlice(
        label=label,
        value_eur=value_eur,
        share=calc.safe_ratio(value_eur, total),
        color_slot=color_slot,
    )
