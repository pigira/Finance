"""Envelopes ("comptes"): managed in Paramètres, chosen on every purchase."""

from html import unescape

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Asset, Transaction
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


PEA_COSTS = {
    "management_fee_pct": "",
    "custody_fee_pct": "0,1",
    "custody_fee_per_position_eur": "2",
    "account_fee_eur": "",
    "order_fee_pct": "0,5",
    "order_fee_min_eur": "1,99",
    "entry_fee_pct": "",
    "exit_tax_rate_before_pct": "31,4",
    "exit_tax_threshold_years": "5",
    "exit_tax_rate_after_pct": "18,6",
}


def test_cost_properties_are_saved_and_shown_again(client: TestClient) -> None:
    response = client.post("/settings/envelopes", data={**PEA, **PEA_COSTS}, follow_redirects=False)
    assert response.status_code == 303

    form = client.get("/settings/envelopes/2/edit").text
    assert 'name="custody_fee_pct" value="0,1"' in " ".join(form.split())
    assert 'name="order_fee_min_eur" value="1,99"' in " ".join(form.split())
    assert 'name="exit_tax_threshold_years" value="5"' in " ".join(form.split())
    assert 'name="exit_tax_rate_after_pct" value="18,6"' in " ".join(form.split())
    # Left empty, shown empty: not a 0 the user never typed.
    assert 'name="management_fee_pct" value=""' in " ".join(form.split())


def test_cost_properties_are_checked(client: TestClient) -> None:
    wrong = {
        **PEA,
        **PEA_COSTS,
        "custody_fee_pct": "120",
        "order_fee_min_eur": "-1",
        "exit_tax_threshold_years": "2,5",
    }
    response = client.post("/settings/envelopes", data=wrong)
    assert response.status_code == 422
    assert "Entre 0 et 100 %" in response.text
    assert "Ne peut pas être négatif" in response.text
    assert "Nombre entier d&#39;années, de 1 à 30" in response.text


def test_the_exit_tax_is_one_rate_or_a_rate_before_and_after_a_threshold(
    client: TestClient,
) -> None:
    threshold_without_rate_after = {**PEA, **PEA_COSTS, "exit_tax_rate_after_pct": ""}
    response = client.post("/settings/envelopes", data=threshold_without_rate_after)
    assert response.status_code == 422
    assert "un seul taux, ou un taux, un seuil et un taux après le seuil" in unescape(response.text)

    single_rate = {
        **PEA,
        **PEA_COSTS,
        "exit_tax_threshold_years": "",
        "exit_tax_rate_after_pct": "",
    }
    assert (
        client.post("/settings/envelopes", data=single_rate, follow_redirects=False).status_code
        == 303
    )


def field_values(html: str, *names: str) -> list[str]:
    """The value attribute of each named input, as the form shows it."""
    flat = " ".join(html.split())
    return [flat.split(f'name="{name}" value="')[1].split('"')[0] for name in names]


TAX_FIELDS = ("exit_tax_rate_before_pct", "exit_tax_threshold_years", "exit_tax_rate_after_pct")


def test_a_new_envelope_starts_from_the_defaults_of_its_type(client: TestClient) -> None:
    # Rates of 2026 (DECISIONS.md): PFU 12.8 % + social charges 18.6 %, 17.2 % on assurance-vie.
    new_cto = client.get("/settings/envelopes/new").text
    assert field_values(new_cto, *TAX_FIELDS) == ["31,4", "", ""]
    assert "valeurs indicatives au 21/11/2026" in new_cto

    pea = client.get("/settings/envelopes/new?envelope_type=pea").text
    assert field_values(pea, *TAX_FIELDS) == ["31,4", "5", "18,6"]
    assert '<option value="pea" selected>' in pea

    life_insurance = client.get("/settings/envelopes/new?envelope_type=assurance_vie").text
    assert field_values(life_insurance, *TAX_FIELDS) == ["30", "8", "24,7"]

    # No default for a PER: its withdrawals are taxed at the income tax rate.
    per = client.get("/settings/envelopes/new?envelope_type=per").text
    assert field_values(per, *TAX_FIELDS) == ["", "", ""]
    # Fees depend on the broker: never prefilled.
    assert field_values(pea, "order_fee_pct", "custody_fee_pct") == ["", ""]


def test_defaults_are_reapplied_only_on_request(client: TestClient) -> None:
    own_rate = {**PEA, **PEA_COSTS, "exit_tax_rate_before_pct": "20"}
    client.post("/settings/envelopes", data=own_rate)

    # Changing the type keeps what was typed.
    client.post("/settings/envelopes/2", data={**own_rate, "envelope_type": "assurance_vie"})
    form = client.get("/settings/envelopes/2/edit").text
    assert field_values(form, *TAX_FIELDS) == ["20", "5", "18,6"]
    assert 'formaction="/settings/envelopes/2/defaults"' in form

    # The button fills in the exit tax of the type picked in the form (here switched
    # back to PEA without saving), keeps every other field as typed, and saves nothing.
    picked_pea = {**own_rate, "envelope_type": "pea", "broker": "Bourso"}
    response = client.post("/settings/envelopes/2/defaults", data=picked_pea)
    assert response.status_code == 200
    assert field_values(response.text, *TAX_FIELDS) == ["31,4", "5", "18,6"]
    assert field_values(response.text, "custody_fee_pct") == ["0,1"]
    assert 'value="Bourso"' in response.text  # the broker, as typed
    assert '<option value="pea" selected>' in response.text
    saved = client.get("/settings/envelopes/2/edit").text
    assert field_values(saved, *TAX_FIELDS) == ["20", "5", "18,6"]


def test_settings_show_the_opening_date_that_counts(client: TestClient) -> None:
    assert "date d'ouverture inconnue" in unescape(client.get("/settings").text)  # nothing bought

    client.post("/lots", data=EUR_ETF_LOT)  # bought on 12/09/2023, in the default envelope
    client.post("/settings/envelopes", data=PEA)  # opened on 04/02/2019
    html = unescape(client.get("/settings").text)
    assert "ouvert le 12/09/2023 (date du premier achat)" in html
    assert "ouvert le 04/02/2019" in html
    assert "date d'ouverture inconnue" not in html


def test_numbers_must_be_finite(client: TestClient) -> None:
    response = client.post(
        "/settings/envelopes",
        data={**PEA, **PEA_COSTS, "exit_tax_threshold_years": "inf", "order_fee_min_eur": "nan"},
    )
    assert response.status_code == 422
    assert response.text.count("Nombre invalide") == 2


def test_the_exit_tax_section_explains_the_per(client: TestClient) -> None:
    html = unescape(client.get("/settings/envelopes/new?envelope_type=per").text)
    assert "PER : pas de valeur par défaut" in html


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


def test_a_purchase_cannot_move_into_an_envelope_opened_after_it(
    client: TestClient, session: Session
) -> None:
    client.post("/lots", data=EUR_ETF_LOT)  # bought on 12/09/2023, in envelope 1
    client.post("/settings/envelopes", data={**PEA, "opening_date": "01/01/2024"})

    response = client.post("/lots/1", data={**EUR_ETF_LOT, "envelope_id": "2"})
    assert response.status_code == 422
    # Only the envelope changed, so the message sits under the envelope picker.
    assert 'id="envelope_id-error"' in response.text
    assert "Compte ouvert après la date de cet achat" in unescape(response.text)
    session.expire_all()
    assert session.get(Transaction, 1).envelope.name == "Compte par défaut"


def test_ongoing_charges_belong_to_the_asset(client: TestClient, session: Session) -> None:
    client.post("/lots", data={**EUR_ETF_LOT, "ongoing_charges_pct": "0,20"})
    assert session.get(Asset, 1).ongoing_charges_pct == 0.20

    # Shown again for the next purchase of the same asset, so it is not erased by it.
    assert '"ongoing_charges_pct": "0,2"' in client.get("/lots/new?asset_id=1").text
    assert '"ongoingCharges": "0,2"' in client.get("/lots/new?asset_id=1").text

    too_much = client.post("/lots", data={**EUR_ETF_LOT, "ongoing_charges_pct": "150"})
    assert too_much.status_code == 422
    assert "Entre 0 et 100 %" in too_much.text


def test_a_new_purchase_left_empty_keeps_the_ongoing_charges(
    client: TestClient, session: Session
) -> None:
    client.post("/lots", data={**EUR_ETF_LOT, "ongoing_charges_pct": "0,20"})
    # Same ISIN typed again, the field left empty: the asset keeps its 0.20 %.
    client.post("/lots", data={**EUR_ETF_LOT, "trade_date": "05/01/2024"})
    session.expire_all()
    assert session.get(Asset, 1).ongoing_charges_pct == 0.20

    # Editing a purchase shows the value; emptying it there clears it on purpose.
    client.post("/lots/1", data={**EUR_ETF_LOT, "ongoing_charges_pct": ""})
    session.expire_all()
    assert session.get(Asset, 1).ongoing_charges_pct is None


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
