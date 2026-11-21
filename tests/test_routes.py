"""HTTP layer: pages, CRUD of lots, HTMX fragments, failure resilience."""

from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.formatting import MINUS, NBSP, NNBSP
from app.models import Asset, PriceHistory, Transaction
from app.providers.base import ProviderError
from app.providers.mock import MockFxProvider, MockProvider
from app.services import pricing
from tests.conftest import (
    BASE_URL,
    EUR_ETF_LOT,
    GBX_ETF_LOT,
    MANUAL_FUND_LOT,
    MSFT_LOT_A,
    MSFT_LOT_B,
    add_lot,
)


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model))


def by_asset_view(html: str) -> str:
    """The page up to the "Par compte" table, which repeats every row per envelope."""
    return html.split('id="positions-by-envelope"')[0]


# ---- pages --------------------------------------------------------------------


def test_empty_portfolio_shows_a_call_to_action(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Votre portefeuille est vide" in response.text
    assert 'href="/lots/new"' in response.text
    assert "positions-table" not in response.text


def test_every_page_answers_200(client: TestClient, demo_portfolio: None) -> None:
    expected = {
        "/": "Portefeuille",
        "/lots/new": "Ajouter un achat",
        "/lots/1/edit": "Modifier un achat",
        "/settings": "Paramètres",
    }
    for path, title in expected.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert f"<title>{title} · Pécule</title>" in response.text
        assert 'lang="fr"' in response.text
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/lots/999/edit").status_code == 404


def test_positions_page_rendering(client: TestClient, demo_portfolio: None) -> None:
    html = client.get("/").text

    # KPI cards and labels.
    for label in ("Valeur totale", "Total investi", "Effet de change total"):
        assert label in html
    assert "Plus-value latente" in html and "(hors dividendes)" in html

    # Reference figures, in French format.
    assert f"6{NNBSP}818,18{NBSP}€" in html  # MSFT value
    assert f"+1{NNBSP}172,98{NBSP}€" in html  # MSFT P/L in EUR...
    assert f"+20,78{NBSP}%" in html  # ...and in percent (both are rendered)
    assert f"376,35{NBSP}€" in html  # MSFT PRU
    assert f"500,00{NBSP}$" in html  # last price in local currency
    assert f"82,50{NBSP}£" in html  # GBX quote shown in pounds
    assert f"388,24{NBSP}€" in html
    assert f"+148,00{NBSP}€" in html and f"+18,45{NBSP}%" in html

    # Gains and losses carry a sign and an arrow, not only a colour.
    assert "▲" in html and "▼" in html
    assert f"{MINUS}0,91{NBSP}%" in html  # FX effect of MSFT lot A

    # Lots: dates dd/mm/yyyy, purchase FX rate, one sub-table for the 2-lot asset only.
    assert "15/03/2024" in html and "02/06/2025" in html
    assert "1,0900" in html and "1,1400" in html
    assert by_asset_view(html).count('class="lots-row"') == 1
    assert by_asset_view(html).count('class="chevron"') == 1
    assert 'id="lots-1"' in html

    # Toggles.
    assert "Effet de change" in html
    assert "setPlMode('pct')" in html
    assert "Perf. devise locale" in html

    # Manual asset: inline price form and badge.
    assert 'hx-post="/assets/4/price"' in html
    assert "manuel" in html
    assert "périmé" not in html


def test_fx_toggle_is_hidden_without_foreign_assets(
    client: TestClient, session: Session, settings: Settings
) -> None:
    add_lot(session, settings, EUR_ETF_LOT)
    html = client.get("/").text
    assert "Effet de change" not in html
    assert "setPlMode" in html


def test_settings_page_and_csv_export(client: TestClient, demo_portfolio: None) -> None:
    html = client.get("/settings").text
    assert "15 min" in html and "24 h" in html
    assert "Exporter CSV" in html
    assert "(mock)" in html

    response = client.get("/export/transactions.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.strip().split("\n")
    assert lines[0].startswith("transaction_id,isin,name,asset_type,ticker,exchange,currency")
    assert len(lines) == 1 + 5
    # Rows are ordered by purchase date; MSFT lot A is transaction 1.
    msft_row = "1,US5949181045,Microsoft Corporation,stock,MSFT,NMS,USD,buy,2024-03-15,"
    assert any(line.startswith(msft_row + "10.0,400.0,1.0,1.09,ecb,") for line in lines)
    assert lines[1].startswith("5,ZZ000PECULE5,")  # the oldest purchase comes first


# ---- create / update / delete ---------------------------------------------------


def test_create_lot(client: TestClient, session: Session) -> None:
    response = client.post("/lots", data=MSFT_LOT_A, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    asset = session.get(Asset, 1)
    assert (asset.isin, asset.exchange, asset.currency, asset.ticker) == (
        "US5949181045",
        "NMS",
        "USD",
        "MSFT",
    )
    lot = session.get(Transaction, 1)
    assert (lot.type, lot.quantity, lot.unit_price, lot.fees) == ("buy", 10.0, 400.0, 1.0)
    assert (lot.fx_rate, lot.fx_source) == (1.09, "ecb")
    # The price of the new asset was fetched right away.
    assert pricing.latest_prices(session)[1].price == 500.0

    assert f"4{NNBSP}545,45{NBSP}€" in client.get("/").text


def test_second_lot_reuses_the_asset_and_shows_sub_rows(
    client: TestClient, session: Session
) -> None:
    client.post("/lots", data=MSFT_LOT_A)
    client.post("/lots", data={**MSFT_LOT_B, "isin": "us5949181045"})  # lower case on purpose
    assert count(session, Asset) == 1
    assert count(session, Transaction) == 2

    html = by_asset_view(client.get("/").text)
    assert html.count('class="chevron"') == 1
    assert "2 achats" in html


def test_same_isin_on_another_exchange_is_a_distinct_asset(
    client: TestClient, session: Session
) -> None:
    client.post("/lots", data=MSFT_LOT_A)
    german_listing = {**MSFT_LOT_A, "ticker": "MSF.DE", "exchange": "GER", "currency": "EUR"}
    assert client.post("/lots", data=german_listing, follow_redirects=False).status_code == 303
    assert count(session, Asset) == 2


def test_currency_conflict_on_an_existing_asset_is_refused(
    client: TestClient, session: Session
) -> None:
    client.post("/lots", data=MSFT_LOT_A)
    response = client.post("/lots", data={**MSFT_LOT_A, "currency": "EUR"})
    assert response.status_code == 422
    assert "existe déjà" in response.text
    assert count(session, Transaction) == 1


def test_invalid_form_is_shown_again_with_messages(client: TestClient, session: Session) -> None:
    invalid = {**EUR_ETF_LOT, "isin": "IE00B4L5Y984", "quantity": "0", "name": "Mon ETF"}
    response = client.post("/lots", data=invalid)
    assert response.status_code == 422
    assert "ISIN invalide" in response.text
    assert "Doit être supérieur à 0" in response.text
    assert "Mon ETF" in response.text  # what was typed is kept
    assert count(session, Asset) == 0
    assert count(session, Transaction) == 0


def test_gbx_lot_is_stored_in_pounds(client: TestClient, session: Session) -> None:
    client.post("/lots", data=GBX_ETF_LOT)
    asset = session.get(Asset, 1)
    assert (asset.currency, asset.price_scale) == ("GBP", 0.01)
    assert (
        session.get(PriceHistory, (1, pricing.latest_prices(session)[1].price_date)).price == 8250
    )
    assert f"82,50{NBSP}£" in client.get("/").text


def test_update_lot(client: TestClient, session: Session) -> None:
    client.post("/lots", data=MSFT_LOT_A)
    client.post("/settings/envelopes", data={"name": "CTO Courtier A", "envelope_type": "cto"})

    form = client.get("/lots/1/edit").text
    assert 'value="US5949181045"' not in form or "readonly" in form
    assert "15/03/2024" in form

    changed = {
        **MSFT_LOT_A,
        "isin": "IE00B4L5Y983",  # identity fields are ignored on update
        "currency": "EUR",
        "name": "Microsoft Corp.",
        "quantity": "12",
        "fx_rate": "1,0850",
        "fx_source": "manual",
        "envelope_id": "2",  # moved to another envelope
    }
    response = client.post("/lots/1", data=changed, follow_redirects=False)
    assert response.status_code == 303

    session.expire_all()
    lot = session.get(Transaction, 1)
    assert (lot.quantity, lot.fx_rate, lot.fx_source, lot.envelope.name) == (
        12.0,
        1.085,
        "manual",
        "CTO Courtier A",
    )
    # The edit form shows the envelope the lot is in now.
    assert '<option value="2" selected>CTO Courtier A</option>' in client.get("/lots/1/edit").text
    assert (lot.asset.isin, lot.asset.currency, lot.asset.name) == (
        "US5949181045",
        "USD",
        "Microsoft Corp.",
    )

    invalid = client.post("/lots/1", data={**MSFT_LOT_A, "quantity": "-3"})
    assert invalid.status_code == 422
    assert client.post("/lots/999", data=MSFT_LOT_A).status_code == 404


def test_delete_lot_returns_the_refreshed_fragment(client: TestClient, session: Session) -> None:
    client.post("/lots", data=MSFT_LOT_A)
    client.post("/lots", data=MSFT_LOT_B)

    response = client.delete("/lots/1")
    assert response.status_code == 200
    assert response.text.lstrip().startswith("<div") or "portfolio-content" in response.text
    assert "<html" not in response.text  # a fragment, not a page
    assert 'id="portfolio-content"' in response.text
    assert "15/03/2024" not in response.text
    assert count(session, Transaction) == 1
    assert client.delete("/lots/1").status_code == 404


def test_deleting_the_last_lot_keeps_the_asset_but_hides_it(
    client: TestClient, session: Session
) -> None:
    client.post("/lots", data=MSFT_LOT_A)
    response = client.delete("/lots/1")

    assert "Votre portefeuille est vide" in response.text
    assert count(session, Transaction) == 0
    assert count(session, Asset) == 1  # kept for autocomplete
    assert "Microsoft Corporation" not in client.get("/").text
    # The ISIN is still offered when adding a purchase.
    assert "US5949181045" in client.get("/lots/new").text


def test_new_lot_form_can_be_prefilled_from_an_asset(client: TestClient) -> None:
    client.post("/lots", data=GBX_ETF_LOT)
    html = client.get("/lots/new?asset_id=1").text
    assert "IE00B4L5Y983" in html
    assert "SWDA.L" in html
    assert "GBX" in html  # a pence listing is shown as GBX in the form


# ---- HTMX fragments --------------------------------------------------------------


def test_listing_search_fragment(client: TestClient) -> None:
    response = client.get("/fragments/listings", params={"isin": "ie00b4l5y983"})
    assert response.status_code == 200
    assert "<html" not in response.text
    assert "3 cotations trouvées" in response.text
    for expected in ("IWDA.AS", "Amsterdam", "SWDA.L", "GBX", "EUNL.DE"):
        assert expected in response.text

    invalid = client.get("/fragments/listings", params={"isin": "IE00B4L5Y984"})
    assert "ISIN valide" in invalid.text


def test_listings_found_by_name_only_are_flagged(client: TestClient, app: FastAPI) -> None:
    class NameMatches(MockProvider):
        def search(self, isin):
            first, second, *_ = super().search(isin)
            return [first, replace(second, isin_match=False)]

    app.state.price_provider = NameMatches()
    html = client.get("/fragments/listings", params={"isin": "IE00B4L5Y983"}).text
    assert html.count("ISIN à vérifier") == 1


def test_listing_search_survives_a_provider_failure(client: TestClient, app: FastAPI) -> None:
    class BrokenSearch(MockProvider):
        def search(self, isin):
            raise ProviderError("Yahoo is down")

    app.state.price_provider = BrokenSearch()
    response = client.get("/fragments/listings", params={"isin": "IE00B4L5Y983"})
    assert response.status_code == 200
    assert "Recherche indisponible" in response.text


def test_fx_fragment_prefills_the_rate_of_the_previous_business_day(client: TestClient) -> None:
    saturday = client.get(
        "/fragments/fx-rate", params={"currency": "USD", "trade_date": "16/03/2024"}
    )
    friday = client.get(
        "/fragments/fx-rate", params={"currency": "USD", "trade_date": "15/03/2024"}
    )
    assert saturday.status_code == 200
    assert 'name="fx_rate"' in saturday.text
    assert "publié le 15/03/2024" in saturday.text
    assert "publié le" not in friday.text

    def rate_of(html: str) -> str:
        return html.split("fxRate = ")[1].split(";")[0]

    assert rate_of(saturday.text) == rate_of(friday.text)

    pence = client.get("/fragments/fx-rate", params={"currency": "GBX", "trade_date": "15/03/2024"})
    assert "1 EUR = ? GBP" in pence.text
    assert client.get("/fragments/fx-rate", params={"currency": "EUR"}).text == ""


def test_fx_fragment_survives_a_provider_failure(client: TestClient, app: FastAPI) -> None:
    class BrokenFx(MockFxProvider):
        def get_rate(self, currency, on_date):
            raise ProviderError("down")

    app.state.fx_provider = BrokenFx()
    response = client.get(
        "/fragments/fx-rate", params={"currency": "USD", "trade_date": "15/03/2024"}
    )
    assert response.status_code == 200
    assert "Taux indisponible" in response.text


def test_manual_price_inline_edit(client: TestClient, session: Session) -> None:
    client.post("/lots", data=MANUAL_FUND_LOT)
    assert f"1{NNBSP}205,00{NBSP}€" in client.get("/").text  # valued at purchase price so far

    response = client.post("/assets/1/price", data={"price": "11,37"})
    assert response.status_code == 200
    assert "<html" not in response.text
    assert f"1{NNBSP}370,09{NBSP}€" in response.text or f"1{NNBSP}370,08{NBSP}€" in response.text
    assert pricing.latest_prices(session)[1].source == "manual"

    invalid = client.post("/assets/1/price", data={"price": "abc"})
    assert invalid.status_code == 200
    assert "Prix invalide" in invalid.text

    client.post("/lots", data=EUR_ETF_LOT)
    assert client.post("/assets/2/price", data={"price": "1"}).status_code == 404  # not manual


def test_refresh_returns_fragments(client: TestClient, demo_portfolio: None) -> None:
    from_portfolio = client.post("/refresh", headers={"HX-Current-URL": f"{BASE_URL}/"})
    assert from_portfolio.status_code == 200
    assert "<html" not in from_portfolio.text
    assert 'id="last-refresh"' in from_portfolio.text
    assert 'id="portfolio-content" class="portfolio" hx-swap-oob="true"' in from_portfolio.text

    from_settings = client.post("/refresh", headers={"HX-Current-URL": f"{BASE_URL}/settings"})
    assert 'id="last-refresh"' in from_settings.text
    assert "portfolio-content" not in from_settings.text


def test_stale_cache_triggers_an_automatic_refresh_on_load(
    client: TestClient, session: Session, settings: Settings
) -> None:
    add_lot(session, settings, EUR_ETF_LOT)  # no price fetched yet
    html = client.get("/").text
    assert 'hx-post="/refresh?auto=1" hx-trigger="load"' in html

    client.post("/refresh?auto=1")
    assert 'hx-trigger="load"' not in client.get("/").text


# ---- resilience ---------------------------------------------------------------------


def test_page_survives_a_failing_ticker(
    client: TestClient, app: FastAPI, demo_portfolio: None
) -> None:
    class MsftIsDown(MockProvider):
        def get_quote(self, ticker):
            if ticker == "MSFT":
                raise ProviderError("timed out after 5s")
            return super().get_quote(ticker)

    app.state.price_provider = MsftIsDown()
    pricing.run_refresh(
        app.state.session_factory,
        app.state.price_provider,
        app.state.fx_provider,
        app.state.settings,
    )

    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert f"500,00{NBSP}$" in html  # last known price
    assert html.count("périmé") >= 1
    assert "Échec de la dernière mise à jour : timed out after 5s" in html
    assert f"950,00{NBSP}€" in html  # the other assets are unaffected


def test_page_survives_every_provider_being_down(
    client: TestClient, app: FastAPI, demo_portfolio: None
) -> None:
    class EverythingIsDown(MockProvider):
        def get_quote(self, ticker):
            raise RuntimeError("no network")

    class FxIsDown(MockFxProvider):
        def get_rate(self, currency, on_date):
            raise ProviderError("no network")

    app.state.price_provider = EverythingIsDown()
    app.state.fx_provider = FxIsDown()

    # A forced refresh (as the background task does) fails for every ticker and rate...
    report = pricing.run_refresh(
        app.state.session_factory,
        app.state.price_provider,
        app.state.fx_provider,
        app.state.settings,
    )
    assert (report.prices_updated, report.prices_failed, report.fx_failed) == (0, 3, 2)
    # ...and so does the "Actualiser" button, without breaking anything.
    refresh = client.post("/refresh", headers={"HX-Current-URL": f"{BASE_URL}/"})
    assert refresh.status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert f"6{NNBSP}818,18{NBSP}€" in page.text  # still valued with the cached data
    assert "périmé" in page.text

    # Adding a purchase while offline works too.
    lot = {**EUR_ETF_LOT, "exchange": "GER", "ticker": "EUNL.DE"}
    assert client.post("/lots", data=lot, follow_redirects=False).status_code == 303
    assert "Aucun cours disponible" in client.get("/").text


# ---- security guards ---------------------------------------------------------------


def test_cross_site_writes_are_refused(client: TestClient, session: Session) -> None:
    response = client.post("/lots", data=MSFT_LOT_A, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert count(session, Transaction) == 0

    same_origin = client.post(
        "/lots", data=MSFT_LOT_A, headers={"Origin": BASE_URL}, follow_redirects=False
    )
    assert same_origin.status_code == 303


def test_unknown_host_header_is_refused(app: FastAPI) -> None:
    with TestClient(app, base_url="http://evil.example") as rebinding_client:
        assert rebinding_client.get("/").status_code == 400
