"""Envelopes ("comptes"): managed in Paramètres, chosen on every purchase."""

from html import unescape

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Transaction
from tests.conftest import EUR_ETF_LOT

PEA = {
    "name": "PEA Boursorama",
    "envelope_type": "pea",
    "broker": "Boursorama",
    "opening_date": "04/02/2019",
}


# ---- Paramètres > Comptes ---------------------------------------------------------


def test_create_an_envelope(client: TestClient) -> None:
    response = client.post("/settings/envelopes", data=PEA, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"

    html = client.get("/settings").text
    assert "Compte par défaut" in html
    assert "PEA Boursorama" in html
    assert "Boursorama" in html
    assert "04/02/2019" in html


def test_invalid_envelope_form_is_shown_again_with_messages(client: TestClient) -> None:
    response = client.post(
        "/settings/envelopes",
        data={"name": " ", "envelope_type": "livret_a", "opening_date": "31/02/2019"},
    )
    assert response.status_code == 422
    assert "Nom requis" in response.text
    assert "Valeur invalide ou manquante" in response.text  # unknown type
    assert "Date invalide (jj/mm/aaaa)" in response.text


def test_two_envelopes_cannot_share_a_name(client: TestClient) -> None:
    client.post("/settings/envelopes", data=PEA)
    response = client.post("/settings/envelopes", data={**PEA, "envelope_type": "cto"})
    assert response.status_code == 422
    assert "Un compte porte déjà ce nom" in response.text
    assert client.get("/settings").text.count("<strong>PEA Boursorama</strong>") == 1


def test_edit_an_envelope(client: TestClient) -> None:
    client.post("/settings/envelopes", data=PEA)
    assert 'href="/settings/envelopes/2/edit"' in client.get("/settings").text

    form = client.get("/settings/envelopes/2/edit").text
    assert 'value="PEA Boursorama"' in form
    assert 'value="04/02/2019"' in form
    assert '<option value="pea" selected>' in form

    renamed = {**PEA, "name": "PEA Bourso", "opening_date": ""}
    response = client.post("/settings/envelopes/2", data=renamed, follow_redirects=False)
    assert response.status_code == 303
    html = client.get("/settings").text
    assert "PEA Bourso" in html and "PEA Boursorama" not in html

    assert client.get("/settings/envelopes/99/edit").status_code == 404
    # Renaming onto another envelope's name is refused.
    taken = client.post("/settings/envelopes/2", data={**PEA, "name": "Compte par défaut"})
    assert taken.status_code == 422
    assert "Un compte porte déjà ce nom" in taken.text


def test_an_opening_date_after_one_of_its_purchases_is_refused(client: TestClient) -> None:
    client.post("/lots", data=EUR_ETF_LOT)  # bought on 12/09/2023, in envelope 1
    later = {"name": "Compte par défaut", "envelope_type": "cto", "opening_date": "01/01/2024"}
    response = client.post("/settings/envelopes/1", data=later)
    assert response.status_code == 422
    assert "Un achat de ce compte est antérieur à cette date" in unescape(response.text)

    on_the_day = {**later, "opening_date": "12/09/2023"}
    response = client.post("/settings/envelopes/1", data=on_the_day, follow_redirects=False)
    assert response.status_code == 303


def test_delete_an_unused_envelope(client: TestClient) -> None:
    client.post("/settings/envelopes", data=PEA)
    response = client.post("/settings/envelopes/2/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert "PEA Boursorama" not in client.get("/settings").text
    assert client.post("/settings/envelopes/2/delete").status_code == 404


def test_an_envelope_holding_purchases_cannot_be_deleted(client: TestClient) -> None:
    client.post("/settings/envelopes", data=PEA)
    client.post("/lots", data={**EUR_ETF_LOT, "envelope_id": "2"})
    response = client.post("/settings/envelopes/2/delete")
    assert response.status_code == 422
    assert "contient des achats" in response.text
    assert "<strong>PEA Boursorama</strong>" in response.text  # still listed


def test_the_last_envelope_cannot_be_deleted(client: TestClient) -> None:
    response = client.post("/settings/envelopes/1/delete")
    assert response.status_code == 422
    assert "au moins un compte" in response.text
    assert "<strong>Compte par défaut</strong>" in client.get("/settings").text


# ---- the purchase form ------------------------------------------------------------


def test_a_purchase_goes_into_the_chosen_envelope(client: TestClient, session: Session) -> None:
    client.post("/settings/envelopes", data=PEA)
    in_pea = {**EUR_ETF_LOT, "envelope_id": "2"}
    response = client.post("/lots", data=in_pea, follow_redirects=False)
    assert response.status_code == 303

    assert session.get(Transaction, 1).envelope.name == "PEA Boursorama"

    # With a second lot the position gets sub-rows, each naming its envelope under the date.
    client.post("/lots", data={**EUR_ETF_LOT, "trade_date": "05/01/2024", "envelope_id": "1"})
    html = client.get("/").text
    assert '<span class="lot-envelope">PEA Boursorama</span>' in html
    assert '<span class="lot-envelope">Compte par défaut</span>' in html


def test_a_purchase_needs_a_known_envelope(client: TestClient, session: Session) -> None:
    missing = client.post("/lots", data={**EUR_ETF_LOT, "envelope_id": ""})
    assert missing.status_code == 422
    assert "Compte requis" in missing.text

    unknown = client.post("/lots", data={**EUR_ETF_LOT, "envelope_id": "99"})
    assert unknown.status_code == 422
    assert "Compte inconnu" in unknown.text

    client.post("/lots", data=EUR_ETF_LOT)
    moved_nowhere = client.post("/lots/1", data={**EUR_ETF_LOT, "envelope_id": "99"})
    assert moved_nowhere.status_code == 422
    assert "Compte inconnu" in moved_nowhere.text
    session.expire_all()
    assert session.get(Transaction, 1).envelope.name == "Compte par défaut"


def test_the_purchase_form_offers_the_last_used_envelope(client: TestClient) -> None:
    client.post("/settings/envelopes", data=PEA)
    # Nothing bought yet: the default envelope comes first.
    assert '<option value="1" selected>Compte par défaut</option>' in client.get("/lots/new").text

    client.post("/lots", data={**EUR_ETF_LOT, "envelope_id": "2"})
    assert '<option value="2" selected>PEA Boursorama</option>' in client.get("/lots/new").text


def test_a_purchase_cannot_predate_the_opening_of_its_envelope(
    client: TestClient, session: Session
) -> None:
    client.post("/settings/envelopes", data=PEA)  # opened on 04/02/2019
    too_early = client.post(
        "/lots", data={**EUR_ETF_LOT, "envelope_id": "2", "trade_date": "03/02/2019"}
    )
    assert too_early.status_code == 422
    assert "Date antérieure à l'ouverture du compte" in unescape(too_early.text)

    on_the_day = {**EUR_ETF_LOT, "envelope_id": "2", "trade_date": "04/02/2019"}
    assert client.post("/lots", data=on_the_day, follow_redirects=False).status_code == 303
    # Without an opening date, any purchase date is accepted.
    old_lot = {**EUR_ETF_LOT, "envelope_id": "1", "trade_date": "02/01/2001"}
    assert client.post("/lots", data=old_lot, follow_redirects=False).status_code == 303


# ---- CSV export -------------------------------------------------------------------


def test_csv_export_names_the_envelope_of_each_purchase(client: TestClient) -> None:
    client.post("/settings/envelopes", data=PEA)
    client.post("/lots", data={**EUR_ETF_LOT, "envelope_id": "2"})
    client.post("/lots", data={**EUR_ETF_LOT, "trade_date": "05/01/2024"})

    header, *rows = client.get("/export/transactions.csv").text.strip().split("\n")
    columns = header.split(",")
    assert "broker" not in columns
    envelope_column = columns.index("envelope")
    assert [row.split(",")[envelope_column] for row in rows] == [
        "PEA Boursorama",  # 12/09/2023
        "Compte par défaut",  # 05/01/2024
    ]
