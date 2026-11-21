"""Reference vectors of the specification and invariants of the calc layer."""

import random
from datetime import date

import pytest

from app import calc
from app.calc import Lot, Market

# MSFT: now 500.00 USD, EURUSD now 1.1000.
MSFT_NOW = Market(last_price=500.00, price_scale=1.0, fx_rate_now=1.1000)
LOT_A = Lot(quantity=10, unit_price=400.00, fees=1.00, fx_rate_buy=1.0900)
LOT_B = Lot(quantity=5, unit_price=450.00, fees=1.00, fx_rate_buy=1.1400)


def approx(value: float) -> object:
    return pytest.approx(value, abs=0.01)


def approx_pct(percent: float) -> object:
    """Compare a ratio with a percentage given with two decimals (±0.01 point)."""
    return pytest.approx(percent / 100, abs=0.0001)


def test_msft_lot_a() -> None:
    result = calc.compute_lot(LOT_A, MSFT_NOW)
    assert result.cost_eur == approx(3670.64)
    assert result.value_eur == approx(4545.45)
    assert result.pl_eur == approx(874.81)
    assert result.pl_pct == approx_pct(23.83)
    assert result.local_perf == approx_pct(24.97)
    assert result.fx_effect == approx_pct(-0.91)


def test_msft_lot_b() -> None:
    result = calc.compute_lot(LOT_B, MSFT_NOW)
    assert result.cost_eur == approx(1974.56)
    assert result.value_eur == approx(2272.73)
    assert result.pl_eur == approx(298.17)
    assert result.pl_pct == approx_pct(15.10)
    assert result.fx_effect == approx_pct(3.64)


def test_msft_asset() -> None:
    lots = [calc.compute_lot(LOT_A, MSFT_NOW), calc.compute_lot(LOT_B, MSFT_NOW)]
    asset = calc.compute_asset(lots)
    assert asset.quantity == 15
    assert asset.cost_eur == approx(5645.20)
    assert asset.value_eur == approx(6818.18)
    assert asset.pl_eur == approx(1172.98)
    assert asset.pl_pct == approx_pct(20.78)
    assert asset.pru_eur == approx(376.35)
    assert asset.local_perf == approx_pct(19.96)
    assert asset.fx_effect == approx_pct(0.68)


def test_asset_percentage_is_not_the_average_of_its_lots() -> None:
    lots = [calc.compute_lot(LOT_A, MSFT_NOW), calc.compute_lot(LOT_B, MSFT_NOW)]
    asset = calc.compute_asset(lots)
    naive_average = (lots[0].pl_pct + lots[1].pl_pct) / 2
    assert abs(asset.pl_pct - naive_average) > 0.01


def test_eur_asset() -> None:
    lot = Lot(quantity=10, unit_price=80.00, fees=2.00, fx_rate_buy=1.0)
    result = calc.compute_lot(lot, Market(last_price=95.00, price_scale=1.0, fx_rate_now=1.0))
    assert result.cost_eur == approx(802.00)
    assert result.value_eur == approx(950.00)
    assert result.pl_eur == approx(148.00)
    assert result.pl_pct == approx_pct(18.45)
    assert result.fx_effect == 0
    assert result.fx_effect_eur == 0


def test_gbx_quote_is_scaled_to_pounds() -> None:
    market = Market(last_price=8250, price_scale=0.01, fx_rate_now=0.8500)
    assert calc.scaled_price(market) == approx(82.50)

    lot = Lot(quantity=4, unit_price=78.40, fees=0.0, fx_rate_buy=0.8400)
    result = calc.compute_lot(lot, market)
    assert result.value_local == approx(330.00)
    assert result.value_eur == approx(388.24)


def test_sell_is_not_implemented() -> None:
    sell = Lot(quantity=1, unit_price=100.0, fees=0.0, fx_rate_buy=1.0, tx_type="sell")
    with pytest.raises(NotImplementedError):
        calc.compute_lot(sell, Market(last_price=100.0, price_scale=1.0, fx_rate_now=1.0))


def _random_lot_and_market(rng: random.Random) -> tuple[Lot, Market]:
    lot = Lot(
        quantity=rng.uniform(0.001, 5000),
        unit_price=rng.uniform(0.01, 5000),
        fees=rng.choice([0.0, rng.uniform(0, 50)]),
        fx_rate_buy=rng.uniform(0.2, 200),
    )
    market = Market(
        last_price=rng.uniform(0.01, 500000),
        price_scale=rng.choice([1.0, 0.01]),
        fx_rate_now=rng.uniform(0.2, 200),
    )
    return lot, market


def test_fx_decomposition_identity_holds_for_random_lots() -> None:
    """(1 + local_perf) * (1 + fx_effect) - 1 == pl_pct, whatever the inputs."""
    rng = random.Random(20240315)
    for _ in range(2000):
        lot, market = _random_lot_and_market(rng)
        result = calc.compute_lot(lot, market)

        recomposed = (1 + result.local_perf) * (1 + result.fx_effect) - 1
        assert recomposed == pytest.approx(result.pl_pct, rel=1e-9, abs=1e-9)
        # The EUR amounts of the split add up to the EUR gain.
        assert result.pl_local_eur + result.fx_effect_eur == pytest.approx(
            result.pl_eur, rel=1e-9, abs=1e-6
        )


def test_fx_decomposition_identity_holds_for_random_assets() -> None:
    rng = random.Random(7)
    for _ in range(300):
        # All lots of an asset share the same market data, not the same purchase rate.
        _, market = _random_lot_and_market(rng)
        lots = [
            calc.compute_lot(_random_lot_and_market(rng)[0], market)
            for _ in range(rng.randint(1, 6))
        ]
        asset = calc.compute_asset(lots)

        recomposed = (1 + asset.local_perf) * (1 + asset.fx_effect) - 1
        assert recomposed == pytest.approx(asset.pl_pct, rel=1e-9, abs=1e-9)
        assert asset.pl_local_eur + asset.fx_effect_eur == pytest.approx(
            asset.pl_eur, rel=1e-9, abs=1e-5
        )
        assert asset.pru_eur * asset.quantity == pytest.approx(asset.cost_eur)


def test_portfolio_kpis_are_sums_across_assets() -> None:
    msft = calc.compute_asset(
        [calc.compute_lot(LOT_A, MSFT_NOW), calc.compute_lot(LOT_B, MSFT_NOW)]
    )
    etf = calc.compute_asset(
        [
            calc.compute_lot(
                Lot(quantity=10, unit_price=80.00, fees=2.00, fx_rate_buy=1.0),
                Market(last_price=95.00, price_scale=1.0, fx_rate_now=1.0),
            )
        ]
    )
    portfolio = calc.compute_portfolio([msft, etf])

    assert portfolio.value_eur == approx(6818.18 + 950.00)
    assert portfolio.cost_eur == approx(5645.20 + 802.00)
    assert portfolio.pl_eur == approx(1172.98 + 148.00)
    assert portfolio.pl_pct == pytest.approx(portfolio.pl_eur / portfolio.cost_eur)
    # Only the USD asset contributes an FX effect.
    assert portfolio.fx_effect_eur == pytest.approx(msft.fx_effect_eur)


def test_empty_portfolio_does_not_divide_by_zero() -> None:
    portfolio = calc.compute_portfolio([])
    assert portfolio.value_eur == 0
    assert portfolio.pl_pct == 0
    assert portfolio.fx_effect_pct == 0


# ---- estimated costs of an Envelope --------------------------------------------

TODAY = date(2026, 11, 21)
OPENED = date(2020, 1, 15)


def test_annual_cost_adds_every_fee_shape() -> None:
    gaining = calc.Holding(value_eur=6000.0, cost_eur=5000.0, ongoing_charges_pct=0.20)
    losing = calc.Holding(value_eur=4000.0, cost_eur=4500.0)
    fees = calc.EnvelopeCosts(
        management_fee_pct=0.5,
        custody_fee_pct=0.1,
        custody_fee_per_position_eur=2.0,
        account_fee_eur=30.0,
    )
    first, second = calc.estimate_costs([gaining, losing], fees, OPENED, TODAY)

    # (0.5 + 0.1 + 0.20) % of 6 000 = 48, + 2 per line, + 30 x 6 000 / 10 000 = 18
    assert first.annual_cost_eur == approx(68.00)
    # (0.5 + 0.1) % of 4 000 = 24, + 2 per line, + 30 x 4 000 / 10 000 = 12
    assert second.annual_cost_eur == approx(38.00)


def test_selling_order_fee_has_a_minimum() -> None:
    large = calc.Holding(value_eur=6000.0, cost_eur=5000.0)
    small = calc.Holding(value_eur=400.0, cost_eur=380.0)
    fees = calc.EnvelopeCosts(order_fee_pct=0.5, order_fee_min_eur=5.0)
    first, second = calc.estimate_costs([large, small], fees, OPENED, TODAY)

    assert first.order_fee_eur == approx(30.00)  # 0.5 % of 6 000
    assert second.order_fee_eur == approx(5.00)  # 0.5 % of 400 = 2, below the minimum


def test_no_order_fee_when_the_envelope_has_none() -> None:
    (only,) = calc.estimate_costs(
        [calc.Holding(value_eur=6000.0, cost_eur=5000.0)], calc.EnvelopeCosts(), OPENED, TODAY
    )
    assert only.order_fee_eur == 0


def test_exit_tax_is_on_the_net_gain_and_carried_by_the_gains() -> None:
    gaining = calc.Holding(value_eur=6000.0, cost_eur=5000.0)
    losing = calc.Holding(value_eur=4000.0, cost_eur=4400.0)
    cto = calc.EnvelopeCosts(order_fee_pct=1.0, exit_tax=calc.ExitTax(rate_before_pct=31.4))
    first, second = calc.estimate_costs([gaining, losing], cto, OPENED, TODAY)

    # Taxable: 6 000 - 5 000 - 60 (order fee) = 940 and 4 000 - 4 400 - 40 = -440.
    # Net 500, taxed at 31.4 % = 157.00, all of it on the only Position with a gain.
    assert first.exit_tax_eur == approx(157.00)
    assert first.exit_cost_eur == approx(60.00 + 157.00)
    assert second.exit_tax_eur == 0
    assert second.exit_cost_eur == approx(40.00)


def test_exit_tax_rate_changes_on_the_anniversary_of_the_threshold() -> None:
    gain_of_2000 = [calc.Holding(value_eur=10000.0, cost_eur=8000.0)]
    pea = calc.EnvelopeCosts(
        exit_tax=calc.ExitTax(rate_before_pct=31.4, threshold_years=5, rate_after_pct=18.6)
    )
    opened = date(2021, 11, 22)

    (day_before,) = calc.estimate_costs(gain_of_2000, pea, opened, date(2026, 11, 21))
    (fifth_anniversary,) = calc.estimate_costs(gain_of_2000, pea, opened, date(2026, 11, 22))
    assert day_before.exit_tax_eur == approx(628.00)  # 31.4 % of 2 000
    assert fifth_anniversary.exit_tax_eur == approx(372.00)  # 18.6 % of 2 000


def test_an_envelope_opened_on_29_february_reaches_its_threshold_on_28_february() -> None:
    gain_of_2000 = [calc.Holding(value_eur=10000.0, cost_eur=8000.0)]
    pea = calc.EnvelopeCosts(
        exit_tax=calc.ExitTax(rate_before_pct=31.4, threshold_years=5, rate_after_pct=18.6)
    )
    (on_28_february,) = calc.estimate_costs(gain_of_2000, pea, date(2020, 2, 29), date(2025, 2, 28))
    assert on_28_february.exit_tax_eur == approx(372.00)


def test_exit_tax_is_split_in_proportion_to_the_gains() -> None:
    holdings = [
        calc.Holding(value_eur=1300.0, cost_eur=1000.0),  # +300
        calc.Holding(value_eur=600.0, cost_eur=500.0),  # +100
        calc.Holding(value_eur=400.0, cost_eur=500.0),  # -100
    ]
    cto = calc.EnvelopeCosts(exit_tax=calc.ExitTax(rate_before_pct=31.4))
    shares = [
        estimate.exit_tax_eur for estimate in calc.estimate_costs(holdings, cto, OPENED, TODAY)
    ]

    # Net gain 300, taxed 94.20: three quarters on the +300, one quarter on the +100.
    assert shares == [approx(70.65), approx(23.55), 0]


def test_no_exit_tax_when_the_envelope_is_at_a_net_loss() -> None:
    holdings = [
        calc.Holding(value_eur=1100.0, cost_eur=1000.0),  # +100
        calc.Holding(value_eur=300.0, cost_eur=500.0),  # -200
    ]
    cto = calc.EnvelopeCosts(exit_tax=calc.ExitTax(rate_before_pct=31.4))
    estimates = calc.estimate_costs(holdings, cto, OPENED, TODAY)
    assert [estimate.exit_tax_eur for estimate in estimates] == [0, 0]


def test_exit_tax_and_exit_cost_are_unknown_without_tax_rates() -> None:
    (only,) = calc.estimate_costs(
        [calc.Holding(value_eur=6000.0, cost_eur=5000.0)],
        calc.EnvelopeCosts(order_fee_min_eur=5.0),
        OPENED,
        TODAY,
    )
    assert only.order_fee_eur == 5.0
    assert only.exit_tax_eur is None
    assert only.exit_cost_eur is None
