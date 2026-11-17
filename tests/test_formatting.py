from datetime import date, datetime

from app.formatting import (
    MINUS,
    NBSP,
    NNBSP,
    format_date,
    format_datetime,
    format_input_number,
    format_money,
    format_number,
    format_percent,
    format_quantity,
    format_rate,
    format_short_datetime,
    trend,
)


def test_money_uses_french_separators() -> None:
    assert format_money(1234.56) == f"1{NNBSP}234,56{NBSP}€"
    assert format_money(1234567.891) == f"1{NNBSP}234{NNBSP}567,89{NBSP}€"
    assert format_money(500, "USD") == f"500,00{NBSP}$"
    assert format_money(82.5, "GBP") == f"82,50{NBSP}£"
    assert format_money(12, "CHF") == f"12,00{NBSP}CHF"


def test_signed_values_always_carry_a_sign() -> None:
    assert format_money(874.81, signed=True) == f"+874,81{NBSP}€"
    assert format_money(-41.7, signed=True) == f"{MINUS}41,70{NBSP}€"
    assert format_percent(0.2383, signed=True) == f"+23,83{NBSP}%"
    assert format_percent(-0.0091, signed=True) == f"{MINUS}0,91{NBSP}%"


def test_values_rounding_to_zero_are_neither_negative_nor_positive() -> None:
    assert format_money(-0.004, signed=True) == f"0,00{NBSP}€"
    assert format_number(0.004, signed=True) == "0,00"
    assert trend(-0.004) == "flat"
    assert trend(0.006) == "up"
    assert trend(-0.006) == "down"


def test_quantity_drops_trailing_zeros() -> None:
    assert format_quantity(10) == "10"
    assert format_quantity(0.5) == "0,5"
    assert format_quantity(1234.5678) == f"1{NNBSP}234,5678"
    assert format_quantity(100) == "100"


def test_rate_and_dates() -> None:
    assert format_rate(1.09) == "1,0900"
    assert format_date(date(2024, 3, 15)) == "15/03/2024"
    # 22:27 UTC in November is 23:27 in Amsterdam (UTC+1).
    stored = datetime(2026, 11, 16, 22, 27)
    assert format_datetime(stored, "Europe/Amsterdam") == "16/11/2026 23:27"


def test_short_datetime_shows_time_today_and_date_otherwise() -> None:
    now = datetime(2026, 11, 16, 12, 0)
    assert format_short_datetime(datetime(2026, 11, 16, 9, 5), "Europe/Amsterdam", now) == "10:05"
    assert (
        format_short_datetime(datetime(2026, 11, 14, 9, 5), "Europe/Amsterdam", now) == "14/11/2026"
    )


def test_input_number_is_plain_with_a_decimal_comma() -> None:
    assert format_input_number(400.0) == "400"
    assert format_input_number(1.09) == "1,09"
    assert format_input_number(0.000001) == "0,000001"
    assert format_input_number(None) == ""
