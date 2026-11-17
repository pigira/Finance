"""Portfolio maths: pure functions, no I/O, no FastAPI, no database.

Conventions
-----------
* "local" means the asset's listing currency (USD for MSFT, GBP for an LSE ETF).
* An FX rate is always "units of foreign currency per 1 EUR" (EURUSD = 1.0900
  means 1 EUR buys 1.09 USD). Converting a local amount to EUR therefore
  *divides* by the rate. For an EUR asset the rate is 1.0.
* Everything is a float; rounding happens only when values are displayed.
* Only purchases exist in v1. A 'sell' transaction raises NotImplementedError
  rather than silently producing wrong numbers.

FX decomposition
----------------
The EUR return of a foreign asset is split into two factors:

    1 + pl_pct = (1 + local_perf) * (1 + fx_effect)

    local_perf = value_local / cost_local - 1    (what the asset did in its currency)
    fx_effect  = r_buy / r_now - 1               (what the currency did against EUR)

fx_effect is positive when the foreign currency got stronger: fewer units of
it are now needed per EUR (r_now < r_buy), so the holding is worth more EUR.

The same split exists as EUR amounts that add up exactly to pl_eur:

    pl_local_eur  = (value_local - cost_local) / r_buy   (local gain at the purchase rate)
    fx_effect_eur = value_local / r_now - value_local / r_buy
"""

from collections.abc import Sequence
from dataclasses import dataclass

EUR = "EUR"


@dataclass(frozen=True)
class Lot:
    """One purchase, as entered by the user."""

    quantity: float
    unit_price: float  # in the asset currency
    fees: float  # in the asset currency
    fx_rate_buy: float  # asset-currency units per 1 EUR at purchase; 1.0 for EUR
    tx_type: str = "buy"


@dataclass(frozen=True)
class Market:
    """Current market data for one asset."""

    last_price: float  # raw quote, as published (8250 for a quote in pence)
    price_scale: float  # quote unit -> asset currency (0.01 for GBX, else 1.0)
    fx_rate_now: float  # asset-currency units per 1 EUR now; 1.0 for EUR


@dataclass(frozen=True)
class LotResult:
    quantity: float
    cost_local: float
    cost_eur: float
    value_local: float
    value_eur: float
    pl_eur: float
    pl_pct: float
    local_perf: float
    fx_effect: float
    pl_local_eur: float
    fx_effect_eur: float


@dataclass(frozen=True)
class AssetResult:
    quantity: float
    cost_local: float
    cost_eur: float
    value_local: float
    value_eur: float
    pl_eur: float
    pl_pct: float
    pru_eur: float  # "prix de revient unitaire": average EUR cost per unit, fees included
    local_perf: float
    fx_effect: float
    pl_local_eur: float
    fx_effect_eur: float


@dataclass(frozen=True)
class PortfolioResult:
    value_eur: float
    cost_eur: float  # total invested
    pl_eur: float
    pl_pct: float
    fx_effect_eur: float
    fx_effect_pct: float


def safe_ratio(numerator: float, denominator: float) -> float:
    """Divide, returning 0.0 instead of failing when the denominator is 0."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def scaled_price(market: Market) -> float:
    """Last price in the asset currency (8250 GBp * 0.01 = 82.50 GBP)."""
    return market.last_price * market.price_scale


def compute_lot(lot: Lot, market: Market) -> LotResult:
    """Value one purchase at the current market price and FX rate."""
    if lot.tx_type != "buy":
        raise NotImplementedError(f"transaction type {lot.tx_type!r} is not supported in v1")

    r_buy = lot.fx_rate_buy
    r_now = market.fx_rate_now

    # Fees are part of the cost: they lower the P/L from day one.
    cost_local = lot.quantity * lot.unit_price + lot.fees
    cost_eur = cost_local / r_buy
    value_local = lot.quantity * scaled_price(market)
    value_eur = value_local / r_now
    pl_eur = value_eur - cost_eur

    # What the holding would be worth in EUR had the FX rate not moved since purchase.
    value_eur_at_buy_rate = value_local / r_buy

    return LotResult(
        quantity=lot.quantity,
        cost_local=cost_local,
        cost_eur=cost_eur,
        value_local=value_local,
        value_eur=value_eur,
        pl_eur=pl_eur,
        pl_pct=safe_ratio(pl_eur, cost_eur),
        local_perf=safe_ratio(value_local, cost_local) - 1 if cost_local else 0.0,
        fx_effect=r_buy / r_now - 1,
        pl_local_eur=value_eur_at_buy_rate - cost_eur,
        fx_effect_eur=value_eur - value_eur_at_buy_rate,
    )


def compute_asset(lots: Sequence[LotResult]) -> AssetResult:
    """Aggregate the lots of one asset.

    Percentages are recomputed from the sums. Averaging the lots' percentages
    would be wrong because the lots do not have the same size.
    """
    quantity = sum(lot.quantity for lot in lots)
    cost_local = sum(lot.cost_local for lot in lots)
    cost_eur = sum(lot.cost_eur for lot in lots)
    value_local = sum(lot.value_local for lot in lots)
    value_eur = sum(lot.value_eur for lot in lots)
    pl_eur = sum(lot.pl_eur for lot in lots)

    pl_pct = safe_ratio(pl_eur, cost_eur)
    local_perf = safe_ratio(value_local, cost_local) - 1 if cost_local else 0.0
    # Chosen so that (1 + local_perf) * (1 + fx_effect) - 1 == pl_pct also holds per asset.
    fx_effect = safe_ratio(1 + pl_pct, 1 + local_perf) - 1 if local_perf != -1 else 0.0

    return AssetResult(
        quantity=quantity,
        cost_local=cost_local,
        cost_eur=cost_eur,
        value_local=value_local,
        value_eur=value_eur,
        pl_eur=pl_eur,
        pl_pct=pl_pct,
        pru_eur=safe_ratio(cost_eur, quantity),
        local_perf=local_perf,
        fx_effect=fx_effect,
        pl_local_eur=sum(lot.pl_local_eur for lot in lots),
        fx_effect_eur=sum(lot.fx_effect_eur for lot in lots),
    )


def compute_portfolio(assets: Sequence[AssetResult]) -> PortfolioResult:
    """Portfolio KPIs: plain sums across assets (every amount is already in EUR)."""
    value_eur = sum(asset.value_eur for asset in assets)
    cost_eur = sum(asset.cost_eur for asset in assets)
    pl_eur = sum(asset.pl_eur for asset in assets)
    fx_effect_eur = sum(asset.fx_effect_eur for asset in assets)

    # Mixed currencies cannot be summed in "local" terms, so the portfolio FX
    # effect is expressed against the value the portfolio would have today at
    # the purchase FX rates.
    value_eur_at_buy_rates = value_eur - fx_effect_eur

    return PortfolioResult(
        value_eur=value_eur,
        cost_eur=cost_eur,
        pl_eur=pl_eur,
        pl_pct=safe_ratio(pl_eur, cost_eur),
        fx_effect_eur=fx_effect_eur,
        fx_effect_pct=safe_ratio(fx_effect_eur, value_eur_at_buy_rates),
    )
