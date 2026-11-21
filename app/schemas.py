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
from app.models import ENVELOPE_TYPES
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
OptionalDate = Annotated[date | None, BeforeValidator(lambda v: parse_date(v) if v else None)]


class LotForm(BaseModel):
    """A purchase as submitted by the form, after validation.

    Field order matters: a validator can only look at the fields declared
    before its own (through `info.data`), e.g. `ticker` needs `manual_price`.
    """

    isin: str
    name: str
    asset_type: Literal["etf", "stock", "fund"]
    manual_price: bool = False
    ticker: str = ""
    exchange: str = ""
    currency: str  # typed "EUR", "usd", "GBX"...; ends up as the ISO major unit
    price_scale: float = 1.0  # derived from `currency` (0.01 when typed in pence)
    trade_date: DateInput
    quantity: RequiredDecimal
    unit_price: RequiredDecimal
    fees: DecimalOrZero = 0.0
    fx_rate: OptionalDecimal = None
    fx_source: str | None = None
    envelope_id: int

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

    @field_validator("manual_price", mode="before")
    @classmethod
    def _checkbox(cls, value: Any) -> bool:
        # An unchecked HTML checkbox is simply absent from the posted form.
        return value in (True, "on", "true", "1")

    @field_validator("ticker", "exchange", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> str:
        return str(value or "").strip().upper()[:30]

    @field_validator("ticker")
    @classmethod
    def _ticker_unless_manual(cls, value: str, info: ValidationInfo) -> str:
        if not value and not info.data.get("manual_price"):
            raise ValueError("Ticker requis (ou cochez « Pas de cours via API »)")
        return value

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, value: str) -> str:
        iso_code, _scale = parse_currency_input(value)
        if not is_iso_currency(iso_code):
            raise ValueError("Devise invalide (code ISO à 3 lettres, ex. EUR)")
        return value.strip()  # still as typed; normalized once everything is valid

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

    @field_validator("fx_rate")
    @classmethod
    def _rate_for_foreign_currency(cls, value: float | None, info: ValidationInfo) -> float | None:
        typed_currency = info.data.get("currency")
        if typed_currency is None:
            return value  # the currency itself is invalid: already reported
        iso_code, _scale = parse_currency_input(typed_currency)
        if iso_code != EUR and (value is None or value <= 0):
            raise ValueError(f"Taux de change EUR/{iso_code} requis")
        return value

    @field_validator("envelope_id", mode="before")
    @classmethod
    def _envelope_chosen(cls, value: Any) -> int:
        text = str(value or "").strip()
        if not text.isdigit():
            raise ValueError("Compte requis")
        return int(text)

    @model_validator(mode="after")
    def _normalize(self) -> "LotForm":
        """Runs once every field is valid: derive the stored values."""
        self.currency, self.price_scale = parse_currency_input(self.currency)
        if self.manual_price:
            # The owner types manual prices in the major unit: no pence scaling.
            self.price_scale = 1.0

        if self.currency == EUR:
            self.fx_rate = None
            self.fx_source = None
        elif self.fx_source not in PROVIDER_FX_SOURCES:
            self.fx_source = "manual"
        return self


class EnvelopeForm(BaseModel):
    """An envelope ("compte") as submitted from Paramètres > Comptes."""

    name: str
    envelope_type: Literal[ENVELOPE_TYPES]
    broker: str | None = None
    opening_date: OptionalDate = None

    @field_validator("name", mode="before")
    @classmethod
    def _check_name(cls, value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("Nom requis")
        return name[:100]

    @field_validator("broker", mode="before")
    @classmethod
    def _broker(cls, value: Any) -> str | None:
        return str(value or "").strip()[:100] or None

    @field_validator("opening_date")
    @classmethod
    def _not_in_future(cls, value: date | None, info: ValidationInfo) -> date | None:
        today = (info.context or {}).get("today") or date.today()
        if value is not None and value > today:
            raise ValueError("La date d'ouverture ne peut pas être dans le futur")
        return value


def french_errors(error: ValidationError) -> dict[str, str]:
    """Flatten a pydantic ValidationError into {field name: French message}."""
    messages: dict[str, str] = {}
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "form"
        if item["type"] == "value_error":
            # Raised by one of the validators above: already a French sentence.
            message = str(item["msg"]).removeprefix("Value error, ")
        else:
            message = "Valeur invalide ou manquante"
        messages.setdefault(field, message)
    return messages
