"""French (fr-FR) display formatting. This is the only place where numbers are rounded.

Typography follows what a French reader expects: "1 234,56 €", "+23,83 %",
"15/03/2024". Group separators are narrow no-break spaces and the unit is
glued to the number with a no-break space, so a value never wraps.
"""

from datetime import date, datetime

from app.timeutil import to_local

NBSP = " "  # no-break space, between a number and its unit
NNBSP = " "  # narrow no-break space, thousands separator
MINUS = "−"  # real minus sign: same width as "+", so columns stay aligned

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}


def _rounded(value: float, decimals: int) -> float:
    rounded = round(value, decimals)
    return 0.0 if rounded == 0 else rounded  # turns -0.0 into 0.0


def format_number(value: float, decimals: int = 2, signed: bool = False) -> str:
    """1234.5 -> "1 234,50". With signed=True a positive value gets a "+"."""
    rounded = _rounded(value, decimals)
    text = f"{abs(rounded):,.{decimals}f}".replace(",", NNBSP).replace(".", ",")
    if rounded < 0:
        return MINUS + text
    if rounded > 0 and signed:
        return "+" + text
    return text


def format_money(value: float, currency: str = "EUR", signed: bool = False) -> str:
    """1234.5 -> "1 234,50 €". Unknown currencies show their ISO code."""
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    return f"{format_number(value, 2, signed)}{NBSP}{symbol}"


def format_percent(ratio: float, signed: bool = False) -> str:
    """0.2383 -> "23,83 %"."""
    return f"{format_number(ratio * 100, 2, signed)}{NBSP}%"


def format_quantity(value: float) -> str:
    """Up to 6 decimals, without trailing zeros: 10.0 -> "10", 0.5 -> "0,5"."""
    text = format_number(value, 6)
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text


def format_rate(value: float) -> str:
    """FX rate with the usual 4 decimals: 1.09 -> "1,0900"."""
    return format_number(value, 4)


def format_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def format_datetime(utc_naive: datetime, tz_name: str) -> str:
    """Stored UTC datetime -> "16/11/2026 22:27" in the display time zone."""
    return to_local(utc_naive, tz_name).strftime("%d/%m/%Y %H:%M")


def format_short_datetime(utc_naive: datetime, tz_name: str, now_utc: datetime) -> str:
    """Time only when it is today ("22:27"), otherwise the date ("14/11/2026")."""
    local = to_local(utc_naive, tz_name)
    if local.date() == to_local(now_utc, tz_name).date():
        return local.strftime("%H:%M")
    return local.strftime("%d/%m/%Y")


def format_input_number(value: float | None) -> str:
    """Value of a form field: no grouping, decimal comma, no trailing zeros."""
    if value is None:
        return ""
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def trend(value: float, decimals: int = 2) -> str:
    """'up', 'down' or 'flat', decided on the value as it will be displayed.

    -0.004 EUR is displayed "0,00 €": it must not get a red "down" arrow.
    """
    rounded = _rounded(value, decimals)
    if rounded > 0:
        return "up"
    if rounded < 0:
        return "down"
    return "flat"


def percent_trend(ratio: float) -> str:
    return trend(ratio * 100)
