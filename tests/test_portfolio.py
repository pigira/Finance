"""The portfolio view: Consolidated positions, and Positions grouped by Envelope."""

from datetime import date, datetime
from html import unescape

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.formatting import NBSP, NNBSP
from app.schemas import EnvelopeForm
from app.services import envelopes, lots, pricing
from app.services.portfolio import build_portfolio
from tests.conftest import EUR_ETF_LOT, MSFT_LOT_A, MSFT_LOT_B, add_lot


@pytest.fixture
def two_ctos(app: FastAPI, session: Session, settings: Settings) -> None:
    """MSFT split over two CTOs, the EUR ETF in the first one; "Compte par défaut" stays empty."""
    first = envelopes.create_envelope(
        session, EnvelopeForm(name="CTO Courtier A", envelope_type="cto")
    )
    second = envelopes.create_envelope(
        session, EnvelopeForm(name="CTO Courtier B", envelope_type="cto")
    )
    add_lot(session, settings, {**MSFT_LOT_A, "envelope_id": str(first.id)})
    add_lot(session, settings, {**MSFT_LOT_B, "envelope_id": str(second.id)})
    add_lot(session, settings, {**EUR_ETF_LOT, "envelope_id": str(first.id)})
    pricing.refresh_all(session, app.state.price_provider, app.state.fx_provider, settings)


def test_positions_are_grouped_by_envelope_with_subtotals(
    two_ctos: None, session: Session, settings: Settings
) -> None:
    view = build_portfolio(session, settings)

    # Largest envelope first; an envelope without open Position is left out.
    groups = view.envelope_groups
    assert [group.envelope.name for group in groups] == ["CTO Courtier A", "CTO Courtier B"]

    first, second = groups
    assert [position.asset.ticker for position in first.positions] == ["MSFT", "IWDA.AS"]
    assert first.positions[0].result.value_eur == pytest.approx(4545.45, abs=0.01)  # lot A only
    assert first.positions[1].result.value_eur == pytest.approx(950.00, abs=0.01)
    assert first.totals.value_eur == pytest.approx(5495.45, abs=0.01)
    assert first.totals.pl_eur == pytest.approx(874.81 + 148.00, abs=0.01)  # MSFT lot A + ETF

    assert [position.asset.ticker for position in second.positions] == ["MSFT"]
    assert second.totals.value_eur == pytest.approx(2272.73, abs=0.01)  # lot B only

    assert view.totals.value_eur == pytest.approx(7768.18, abs=0.01)


def test_envelope_subtotals_add_up_to_the_portfolio_totals(
    two_ctos: None, session: Session, settings: Settings
) -> None:
    view = build_portfolio(session, settings)
    for figure in ("value_eur", "cost_eur", "pl_eur"):
        subtotals = sum(getattr(group.totals, figure) for group in view.envelope_groups)
        assert subtotals == pytest.approx(getattr(view.totals, figure), abs=0.005), figure


def test_a_consolidated_position_sums_its_envelopes_and_names_them(
    two_ctos: None, session: Session, settings: Settings
) -> None:
    view = build_portfolio(session, settings)
    msft, etf = view.consolidated_positions

    assert msft.result.value_eur == pytest.approx(6818.18, abs=0.01)
    assert msft.result.pl_eur == pytest.approx(1172.98, abs=0.01)
    assert [envelope.name for envelope in msft.envelopes] == ["CTO Courtier A", "CTO Courtier B"]
    assert [envelope.name for envelope in etf.envelopes] == ["CTO Courtier A"]


@pytest.fixture
def cto_a_with_costs(two_ctos: None, session: Session) -> None:
    """CTO Courtier A charges an order fee and custody, and its gains are taxed at 31.4 %.
    CTO Courtier B has no cost property at all; the ETF has 0.20 % ongoing charges."""
    cto_a = next(e for e in envelopes.all_envelopes(session) if e.name == "CTO Courtier A")
    cto_a.order_fee_pct, cto_a.order_fee_min_eur = 0.5, 2.0
    cto_a.custody_fee_per_position_eur = 3.0
    cto_a.exit_tax_rate_before_pct = 31.4
    etf = next(a for a in lots.all_assets(session) if a.ticker == "IWDA.AS")
    etf.ongoing_charges_pct = 0.20
    session.commit()


def test_estimated_costs_of_positions_envelopes_and_portfolio(
    cto_a_with_costs: None, session: Session, settings: Settings
) -> None:
    view = build_portfolio(session, settings)
    cto_a, cto_b = view.envelope_groups
    msft_in_a, etf = cto_a.positions

    # MSFT lot A: value 4 545.45, cost 3 670.64. Order fee 0.5 % = 22.73, custody 3 per line.
    # ETF: value 950.00, cost 802.00. Order fee 4.75; custody 3 + 0.20 % of 950 = 4.90.
    # Taxable 852.09 + 143.25 = 995.33, x 31.4 % = 312.54, split 267.55 / 44.98.
    assert msft_in_a.costs.annual_cost_eur == pytest.approx(3.00, abs=0.01)
    assert msft_in_a.costs.order_fee_eur == pytest.approx(22.73, abs=0.01)
    assert msft_in_a.costs.exit_tax_eur == pytest.approx(267.55, abs=0.01)
    assert msft_in_a.costs.exit_cost_eur == pytest.approx(22.73 + 267.55, abs=0.01)
    assert etf.costs.annual_cost_eur == pytest.approx(4.90, abs=0.01)
    assert etf.costs.exit_tax_eur == pytest.approx(44.98, abs=0.01)
    assert cto_a.costs.annual_cost_eur == pytest.approx(7.90, abs=0.01)
    assert cto_a.costs.exit_cost_eur == pytest.approx(340.01, abs=0.01)

    # CTO Courtier B: no fee, and its tax is unknown, not zero.
    assert cto_b.costs.annual_cost_eur == 0
    assert cto_b.costs.exit_tax_eur is None

    # A Consolidated position, and the portfolio, add up their parts; with CTO Courtier B's
    # tax unknown, their exit cost is the known part, a lower bound.
    msft = view.consolidated_positions[0]
    assert msft.costs.annual_cost_eur == pytest.approx(3.00, abs=0.01)
    assert msft.costs.exit_cost_eur == pytest.approx(22.73 + 267.55, abs=0.01)
    assert not msft.costs.exit_tax_complete
    assert view.costs.annual_cost_eur == pytest.approx(7.90, abs=0.01)
    assert view.costs.exit_cost_eur == pytest.approx(340.01, abs=0.01)
    assert not view.costs.exit_tax_complete


def test_the_oldest_purchase_dates_an_envelope_without_opening_date(
    app: FastAPI, session: Session, settings: Settings
) -> None:
    pea = envelopes.create_envelope(session, EnvelopeForm(name="PEA", envelope_type="pea"))
    pea.exit_tax_rate_before_pct, pea.exit_tax_threshold_years = 31.4, 5
    pea.exit_tax_rate_after_pct = 18.6
    session.commit()
    add_lot(
        session, settings, {**EUR_ETF_LOT, "envelope_id": str(pea.id), "trade_date": "10/01/2021"}
    )
    pricing.refresh_all(session, app.state.price_provider, app.state.fx_provider, settings)
    today = datetime(2026, 11, 21, 11, 0)

    # Bought on 10/01/2021: more than 5 years ago, so 18.6 % of the 148.00 gain.
    (group,) = build_portfolio(session, settings, now=today).envelope_groups
    assert group.costs.exit_tax_eur == pytest.approx(27.53, abs=0.01)

    # An opening date typed in wins: opened 01/06/2022, not 5 years yet, 31.4 %.
    pea.opening_date = date(2022, 6, 1)
    session.commit()
    (group,) = build_portfolio(session, settings, now=today).envelope_groups
    assert group.costs.exit_tax_eur == pytest.approx(46.47, abs=0.01)


def test_allocation_by_envelope_and_envelopes_without_exit_tax(
    cto_a_with_costs: None, session: Session, settings: Settings
) -> None:
    view = build_portfolio(session, settings)

    by_envelope = [
        (s.label, round(s.value_eur, 2), s.color_slot) for s in view.allocations["envelope"]
    ]
    assert by_envelope == [("CTO Courtier A", 5495.45, 1), ("CTO Courtier B", 2272.73, 2)]

    # Named where a total is only a lower bound because of them.
    assert view.envelopes_without_exit_tax == ["CTO Courtier B"]
    msft, etf = view.consolidated_positions
    assert msft.envelopes_without_exit_tax == ["CTO Courtier B"]
    assert etf.envelopes_without_exit_tax == []


# ---- the dashboard ----------------------------------------------------------------


def test_the_dashboard_offers_both_groupings(client: TestClient, two_ctos: None) -> None:
    html = client.get("/").text
    assert "setPositionsView('asset')" in html
    assert "setPositionsView('envelope')" in html
    by_asset, by_envelope = html.split('id="positions-by-envelope"')

    # "Par actif": each row names its envelope, or says how many it spans.
    assert '<span class="asset-envelope">2 comptes</span>' in by_asset
    assert '<span class="asset-envelope">CTO Courtier A</span>' in by_asset

    # "Par compte": one group per envelope holding something, largest first, with a subtotal.
    assert "Compte par défaut" not in by_envelope
    assert by_envelope.index("CTO Courtier A") < by_envelope.index("CTO Courtier B")
    assert f"Sous-total{NBSP}: CTO Courtier A" in by_envelope
    assert f"5{NNBSP}495,45{NBSP}€" in by_envelope
    assert f"+1{NNBSP}022,81{NBSP}€" in by_envelope  # its P/L
    assert f"2{NNBSP}272,73{NBSP}€" in by_envelope
    # The subtotal also says what was invested: MSFT lot A 3 670,64 € + ETF 802,00 €.
    assert f"investi 4{NNBSP}472,64{NBSP}€" in by_envelope


def test_each_row_of_an_envelope_names_it_for_screen_readers(
    client: TestClient, two_ctos: None
) -> None:
    by_envelope = client.get("/").text.split('id="positions-by-envelope"')[1]
    # The visible envelope name is the group header; each row repeats it, unseen.
    assert by_envelope.count('<span class="sr-only">CTO Courtier A : </span>') == 2
    assert by_envelope.count('<span class="sr-only">CTO Courtier B : </span>') == 1


def test_the_allocation_card_has_a_tab_per_envelope(client: TestClient, two_ctos: None) -> None:
    html = client.get("/").text
    assert "show('envelope')" in html
    assert ">Par compte</button>" in html.split('id="allocation-title"')[1].split("</header>")[0]
    assert "x-show=\"tab === 'envelope'\"" in html


def test_positions_show_their_annual_and_exit_costs(
    client: TestClient, cto_a_with_costs: None
) -> None:
    html = unescape(client.get("/").text)
    by_asset, by_envelope = html.split('id="positions-by-envelope"')
    for table in (by_asset, by_envelope):
        assert 'data-sort="annualCost"' in table and 'data-sort="exitCost"' in table

    # "Par compte": MSFT in CTO Courtier A, its exit cost fully known, and its breakdown.
    assert f"290,28{NBSP}€" in by_envelope  # 22.73 order fee + 267.55 tax
    assert f"Courtage à la vente : 22,73{NBSP}€" in by_envelope
    assert f"Impôt latent : 267,55{NBSP}€" in by_envelope
    assert f"Frais du compte : 4,90{NBSP}€" not in by_envelope  # the ETF's 4.90 is split:
    assert f"Frais du compte : 3,00{NBSP}€ · Frais courants : 1,90{NBSP}€" in by_envelope
    # MSFT in CTO Courtier B: its tax is unknown.
    assert "Fiscalité non renseignée pour ce compte" in by_envelope

    # "Par actif": MSFT spans both, so its exit cost is a lower bound that says why.
    assert f"≥{NBSP}290,28{NBSP}€" in by_asset
    assert "hors fiscalité de : CTO Courtier B" in by_asset


def test_a_kpi_card_sums_up_the_estimated_costs(client: TestClient, cto_a_with_costs: None) -> None:
    kpis = unescape(client.get("/").text).split('class="kpis"')[1].split("</section>")[0]
    assert "Coûts estimés" in kpis
    assert f"7,90{NBSP}€ / an" in kpis
    # CTO Courtier B's tax is unknown: the exit cost is a lower bound, and says why.
    assert f"sortie ≥{NBSP}340,01{NBSP}€" in kpis
    assert "hors fiscalité de : CTO Courtier B" in kpis
