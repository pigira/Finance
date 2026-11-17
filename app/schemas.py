"""Pydantic schemas: validation of what the user types in the purchase form.

The form posts plain strings. `LotForm` turns them into typed, checked
values, and reports problems as French messages that the template shows
next to the offending field.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.calc import EUR
from app.isin import is_valid_isin, normalize_isin
from app.providers.base import is_iso_currency, parse_currency_input

# fx_source values that mean "the rate came from a provider and was not edited".
PROVIDER_FX_SOURCES = ("ecb", "yahoo", "mock")


def parse_decimal(value: Any) -> Any:
    """Accept French input such as "1 234,56" (and "1234.56"). Empty means None."""
    if not isinstance(value, str):
        return value
    cleaned = value.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
    if cleaned == "":
        return None
    try:
        return float(cleaned)
    except ValueError:
        raise ValueError("Nombre invalide") from None


def parse_date(value: Any) -> Any:
    """Accept dd/mm/yyyy (what the form shows) and the ISO format yyyy-mm-dd."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError("Date invalide (jj/mm/aaaa)")


def parse_decimal_or_zero(value: Any) -> Any:
    parsed = parse_decimal(value)
    return 0.0 if parsed is None else parsed


RequiredDecimal = Annotated[float, BeforeValidator(parse_decimal)]
OptionalDecimal = Annotated[float | None, BeforeValidator(parse_decimal)]
DecimalOrZero = Annotated[float, BeforeValidator(parse_decimal_or_zero)]
DateInput = Annotated[date, BeforeValidator(parse_date)]


class LotForm(BaseModel):
    """A purchase as submitted by the form, after validation."""

    isin: str
    name: str
    asset_type: Literal["etf", "stock", "fund"]
    ticker: str = ""
    exchange: str = ""
    currency: str  # typed "EUR", "usd", "GBX"...; stored as the ISO major unit
    price_scale: float = 1.0  # derived from `currency` (0.01 when typed in pence)
    manual_price: bool = False
    trade_date: DateInput
    quantity: RequiredDecimal
    unit_price: RequiredDecimal
    fees: DecimalOrZero = 0.0
    fx_rate: OptionalDecimal = None
    fx_source: str | None = None
    broker: str | None = None

    @field_validator("isin", mode="before")
    @classmethod
    def _check_isin(cls, value: Any) -> str:
        isin = normalize_isin(str(value or ""))
        if len(isin) != 12:
            raise ValueError("Un ISIN compte 12 caractères")
        if not is_valid_isin(isin):
            raise ValueError("ISIN invalide (clé de contrôle incorrecte)")
        return isin

    @field_validator("name", mode="before")
    @classmethod
    def _check_name(cls, value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("Nom requis")
        return name[:200]

    @field_validator("ticker", "exchange", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> str:
        return str(value or "").strip().upper()[:30]

    @field_validator("manual_price", mode="before")
    @classmethod
    def _checkbox(cls, value: Any) -> bool:
        # An unchecked HTML checkbox is simply absent from the posted form.
        return value in (True, "on", "true", "1")

    @field_validator("trade_date")
    @classmethod
    def _not_in_future(cls, value: date, info: ValidationInfo) -> date:
        # The caller may pass "today" in the display time zone through the
        # validation context; the server's own date is the fallback.
        today = (info.context or {}).get("today") or date.today()
        if value > today:
            raise ValueError("La date d'achat ne peut pas être dans le futur")
        return value

    @field_validator("quantity", "unit_price")
    @classmethod
    def _strictly_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Doit être supérieur à 0")
        return value

    @field_validator("fees")
    @classmethod
    def _not_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("Ne peut pas être négatif")
        return value

    @field_validator("broker", mode="before")
    @classmethod
    def _broker(cls, value: Any) -> str | None:
        return str(value or "").strip()[:100] or None

    @model_validator(mode="after")
    def _check_consistency(self) -> "LotForm":
        self._normalize_currency()
        if self.manual_price:
            # The owner types manual prices in the major unit: no pence scaling.
            self.price_scale = 1.0
        elif not self.ticker:
            raise _field_error("ticker", "Ticker requis (ou cochez « Pas de cours via API »)")
        self._check_fx()
        return self

    def _normalize_currency(self) -> None:
        iso_code, price_scale = parse_currency_input(self.currency)
        if not is_iso_currency(iso_code):
            raise _field_error("currency", "Devise invalide (code ISO à 3 lettres, ex. EUR)")
        self.currency = iso_code
        self.price_scale = price_scale

    def _check_fx(self) -> None:
        if self.currency == EUR:
            self.fx_rate = None
            self.fx_source = None
            return
        if self.fx_rate is None or self.fx_rate <= 0:
            raise _field_error("fx_rate", f"Taux de change EUR/{self.currency} requis")
        if self.fx_source not in PROVIDER_FX_SOURCES:
            self.fx_source = "manual"


class _FieldError(ValueError):
    """A validation error raised by the model validator for one specific field."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _field_error(field: str, message: str) -> _FieldError:
    return _FieldError(field, message)


def french_errors(error: ValidationError) -> dict[str, str]:
    """Flatten a pydantic ValidationError into {field name: French message}."""
    messages: dict[str, str] = {}
    for item in error.errors():
        original = item.get("ctx", {}).get("error")
        if isinstance(original, _FieldError):
            field = original.field
        else:
            field = str(item["loc"][0]) if item["loc"] else "form"
        message = str(item["msg"]).removeprefix("Value error, ")
        if item["type"] != "value_error":
            message = "Valeur invalide ou manquante"
        messages.setdefault(field, message)
    return messages
