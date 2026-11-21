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

Estimated costs
---------------
What holding a Position in its Envelope costs, at today's values (see
GLOSSARY.md). Percentages are given as typed (0.5 means 0.5 %), amounts in EUR.

    annual cost = (management % + custody % + ongoing charges %) x value
                  + custody fee per Position + account fee x value / Envelope value
    order fee   = max(order fee % x value, order fee minimum)   (selling it all at once)
    exit tax    = rate x max(0, sum over the Envelope of (value - cost - order fee)),
                  split over the Positions with a gain, in proportion to that gain
    exit cost   = order fee + its share of the exit tax
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

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


# ---- estimated costs ------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    """What the cost estimates need to know about one Position (an Asset in this Envelope)."""

    value_eur: float
    cost_eur: float
    ongoing_charges_pct: float = 0.0  # the Asset's own Ongoing charges


@dataclass(frozen=True)
class ExitTax:
    """The tax on the Envelope's gains if it were emptied today."""

    rate_before_pct: float
    # Both set, or neither (a single rate, as in a CTO).
    threshold_years: int | None = None
    rate_after_pct: float | None = None

    def rate_pct(self, opened_on: date, today: date) -> float:
        """The rate after the threshold applies from its anniversary on."""
        if self.threshold_years is None or self.rate_after_pct is None:
            return self.rate_before_pct
        anniversary = _add_years(opened_on, self.threshold_years)
        return self.rate_after_pct if today >= anniversary else self.rate_before_pct


def _add_years(day: date, years: int) -> date:
    """Same day `years` later; 29 February becomes 28 February in a common year."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


@dataclass(frozen=True)
class EnvelopeCosts:
    """The cost properties of an Envelope; a fee it does not charge is 0."""

    management_fee_pct: float = 0.0
    custody_fee_pct: float = 0.0
    custody_fee_per_position_eur: float = 0.0
    account_fee_eur: float = 0.0
    order_fee_pct: float = 0.0
    order_fee_min_eur: float = 0.0
    exit_tax: ExitTax | None = None  # None: not filled in, the tax is unknown


@dataclass(frozen=True)
class CostEstimate:
    annual_cost_eur: float
    order_fee_eur: float  # selling the whole Position in one order
    exit_tax_eur: float | None  # its share of the Envelope's Latent exit tax; None if unknown

    @property
    def exit_cost_eur(self) -> float | None:
        if self.exit_tax_eur is None:
            return None
        return self.order_fee_eur + self.exit_tax_eur


def estimate_costs(
    positions: Sequence[Position], costs: EnvelopeCosts, opened_on: date, today: date
) -> list[CostEstimate]:
    """Estimated costs of each Position of one Envelope, in the order given."""
    envelope_value = sum(position.value_eur for position in positions)
    annual_costs = [
        (costs.management_fee_pct + costs.custody_fee_pct + position.ongoing_charges_pct)
        / 100
        * position.value_eur
        + costs.custody_fee_per_position_eur
        + costs.account_fee_eur * safe_ratio(position.value_eur, envelope_value)
        for position in positions
    ]
    order_fees = [
        max(costs.order_fee_pct / 100 * position.value_eur, costs.order_fee_min_eur)
        for position in positions
    ]
    exit_taxes = _exit_tax_shares(positions, order_fees, costs.exit_tax, opened_on, today)
    return [
        CostEstimate(annual_cost_eur=annual, order_fee_eur=fee, exit_tax_eur=tax)
        for annual, fee, tax in zip(annual_costs, order_fees, exit_taxes, strict=True)
    ]


def _exit_tax_shares(
    positions: Sequence[Position],
    order_fees: Sequence[float],
    exit_tax: ExitTax | None,
    opened_on: date,
    today: date,
) -> list[float | None]:
    """The tax on the Envelope's net gain, split over the Positions with a gain."""
    if exit_tax is None:
        return [None] * len(positions)
    gains = [
        position.value_eur - position.cost_eur - fee
        for position, fee in zip(positions, order_fees, strict=True)
    ]
    total_tax = exit_tax.rate_pct(opened_on, today) / 100 * max(0.0, sum(gains))
    positive_gains = sum(gain for gain in gains if gain > 0)
    return [total_tax * safe_ratio(max(0.0, gain), positive_gains) for gain in gains]


def add_up_costs(estimates: Sequence[CostEstimate]) -> CostEstimate:
    """Costs of several Positions together; one unknown exit tax makes the total unknown."""
    taxes = [estimate.exit_tax_eur for estimate in estimates]
    return CostEstimate(
        annual_cost_eur=sum(estimate.annual_cost_eur for estimate in estimates),
        order_fee_eur=sum(estimate.order_fee_eur for estimate in estimates),
        exit_tax_eur=None if None in taxes else sum(tax for tax in taxes if tax is not None),
    )
