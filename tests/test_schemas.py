"""Validation of the purchase form."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas import LotForm, french_errors
from tests.conftest import EUR_ETF_LOT, GBX_ETF_LOT, MANUAL_FUND_LOT, MSFT_LOT_A

TODAY = {"today": date(2026, 11, 16)}


def errors_of(values: dict[str, str]) -> dict[str, str]:
    with pytest.raises(ValidationError) as caught:
        LotForm.model_validate(values, context=TODAY)
    return french_errors(caught.value)


def test_french_decimals_and_dates_are_parsed() -> None:
    form = LotForm.model_validate(
        {**MSFT_LOT_A, "quantity": "1 234,5", "unit_price": "400,25"}, context=TODAY
    )
    assert form.quantity == 1234.5
    assert form.unit_price == 400.25
    assert form.trade_date == date(2024, 3, 15)
    assert form.fx_rate == 1.09
    assert form.fx_source == "ecb"


def test_isin_is_upper_cased_and_checked() -> None:
    form = LotForm.model_validate({**EUR_ETF_LOT, "isin": " ie00b4l5y983 "}, context=TODAY)
    assert form.isin == "IE00B4L5Y983"

    assert "clé de contrôle" in errors_of({**EUR_ETF_LOT, "isin": "IE00B4L5Y984"})["isin"]
    assert "12 caractères" in errors_of({**EUR_ETF_LOT, "isin": "IE00B4L5Y98"})["isin"]


def test_gbx_becomes_gbp_with_a_price_scale() -> None:
    form = LotForm.model_validate(GBX_ETF_LOT, context=TODAY)
    assert (form.currency, form.price_scale) == ("GBP", 0.01)

    lower_case = LotForm.model_validate({**GBX_ETF_LOT, "currency": "gbx"}, context=TODAY)
    assert (lower_case.currency, lower_case.price_scale) == ("GBP", 0.01)

    yahoo_spelling = LotForm.model_validate({**GBX_ETF_LOT, "currency": "GBp"}, context=TODAY)
    assert (yahoo_spelling.currency, yahoo_spelling.price_scale) == ("GBP", 0.01)

    pounds = LotForm.model_validate({**GBX_ETF_LOT, "currency": "GBP"}, context=TODAY)
    assert (pounds.currency, pounds.price_scale) == ("GBP", 1.0)


def test_eur_lot_has_no_fx_rate_and_fees_default_to_zero() -> None:
    values = {**EUR_ETF_LOT, "fx_rate": "1,05", "fx_source": "ecb"}
    del values["fees"]
    form = LotForm.model_validate(values, context=TODAY)
    assert form.fx_rate is None
    assert form.fx_source is None
    assert form.fees == 0.0


def test_foreign_lot_requires_an_fx_rate() -> None:
    assert "fx_rate" in errors_of({**MSFT_LOT_A, "fx_rate": ""})
    assert "fx_rate" in errors_of({**MSFT_LOT_A, "fx_rate": "0"})


def test_an_edited_rate_is_marked_manual() -> None:
    form = LotForm.model_validate({**MSFT_LOT_A, "fx_source": "anything"}, context=TODAY)
    assert form.fx_source == "manual"


def test_ticker_is_required_unless_the_price_is_manual() -> None:
    assert "ticker" in errors_of({**EUR_ETF_LOT, "ticker": ""})

    manual = LotForm.model_validate(MANUAL_FUND_LOT, context=TODAY)
    assert manual.manual_price is True
    assert manual.ticker == ""
    assert manual.exchange == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", "0"),
        ("quantity", "-1"),
        ("quantity", "abc"),
        ("quantity", ""),
        ("unit_price", "0"),
        ("fees", "-1"),
        ("trade_date", "17/11/2026"),  # the day after "today"
        ("trade_date", "2024-13-45"),
        ("currency", "EURO"),
        ("name", "   "),
        ("asset_type", "bond"),
    ],
)
def test_invalid_values_are_reported_on_their_field(field: str, value: str) -> None:
    errors = errors_of({**EUR_ETF_LOT, field: value})
    assert field in errors
    assert not errors[field].startswith("Value error")
