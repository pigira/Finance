"""The portfolio view: Consolidated positions, and Positions grouped by Envelope."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.formatting import NBSP, NNBSP
from app.schemas import EnvelopeForm
from app.services import envelopes, pricing
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
    assert [p.asset.ticker for p in first.positions] == ["MSFT", "IWDA.AS"]
    assert first.positions[0].result.value_eur == pytest.approx(4545.45, abs=0.01)  # lot A only
    assert first.positions[1].result.value_eur == pytest.approx(950.00, abs=0.01)
    assert first.totals.value_eur == pytest.approx(5495.45, abs=0.01)
    assert first.totals.pl_eur == pytest.approx(874.81 + 148.00, abs=0.01)  # MSFT lot A + ETF

    assert [p.asset.ticker for p in second.positions] == ["MSFT"]
    assert second.totals.value_eur == pytest.approx(2272.73, abs=0.01)  # lot B only

    # The subtotals add up to the portfolio totals.
    assert view.totals.value_eur == pytest.approx(7768.18, abs=0.01)


def test_a_consolidated_position_sums_its_envelopes_and_names_them(
    two_ctos: None, session: Session, settings: Settings
) -> None:
    view = build_portfolio(session, settings)
    msft, etf = view.positions

    assert msft.result.value_eur == pytest.approx(6818.18, abs=0.01)
    assert msft.result.pl_eur == pytest.approx(1172.98, abs=0.01)
    assert [envelope.name for envelope in msft.envelopes] == ["CTO Courtier A", "CTO Courtier B"]
    assert [envelope.name for envelope in etf.envelopes] == ["CTO Courtier A"]


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
