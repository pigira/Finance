"""Purchase form: add, edit and delete a lot."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.calc import EUR
from app.config import Settings
from app.db import get_session
from app.formatting import format_date, format_input_number
from app.models import Asset, Transaction
from app.providers.base import (
    FxProvider,
    PriceProvider,
    is_iso_currency,
    parse_currency_input,
    quoted_currency_code,
)
from app.schemas import LotForm, french_errors
from app.services import envelopes, lots, pricing
from app.services.errors import FormError
from app.services.portfolio import build_portfolio
from app.timeutil import local_today
from app.web import (
    get_fx_provider,
    get_price_provider,
    get_settings,
    page_context,
    read_form,
    render,
)

router = APIRouter()


# ---- pages -------------------------------------------------------------------


@router.get("/lots/new", response_class=HTMLResponse)
def new_lot_page(
    request: Request,
    asset_id: int | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Empty form. With ?asset_id=..., the asset part is prefilled (add a lot to it)."""
    values = {"trade_date": format_date(local_today(settings.display_timezone)), "currency": EUR}
    envelope = envelopes.last_used_envelope(session)
    if envelope is not None:
        values["envelope_id"] = str(envelope.id)
    asset = session.get(Asset, asset_id) if asset_id is not None else None
    if asset is not None:
        values.update(_asset_values(asset))
    return _render_form(request, session, values=values)


@router.get("/lots/{lot_id}/edit", response_class=HTMLResponse)
def edit_lot_page(
    request: Request, lot_id: int, session: Session = Depends(get_session)
) -> HTMLResponse:
    lot = _get_lot_or_404(session, lot_id)
    return _render_form(request, session, values=_lot_values(lot), lot=lot)


# ---- actions -----------------------------------------------------------------


@router.post("/lots", response_class=HTMLResponse)
def create_lot(
    request: Request,
    data: dict[str, str] = Depends(read_form),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    price_provider: PriceProvider = Depends(get_price_provider),
    fx_provider: FxProvider = Depends(get_fx_provider),
):
    try:
        lots.create_lot(session, _validate(data, settings), settings)
    except ValidationError as exc:
        return _render_form(request, session, values=data, errors=french_errors(exc))
    except FormError as exc:
        session.rollback()
        return _render_form(request, session, values=data, errors={exc.field: exc.message})

    # Fetch the price (and FX rate) of a newly added asset right away. Assets
    # refreshed recently are skipped, and a provider failure is not an error here.
    pricing.refresh_all(session, price_provider, fx_provider, settings)
    return RedirectResponse("/", status_code=303)


@router.post("/lots/{lot_id}", response_class=HTMLResponse)
def update_lot(
    request: Request,
    lot_id: int,
    data: dict[str, str] = Depends(read_form),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    price_provider: PriceProvider = Depends(get_price_provider),
    fx_provider: FxProvider = Depends(get_fx_provider),
):
    lot = _get_lot_or_404(session, lot_id)
    # The identity of the asset is not editable: ignore whatever the form sent for it.
    data = {**data, **_identity_values(lot.asset)}
    try:
        lots.update_lot(session, lot, _validate(data, settings), settings)
    except ValidationError as exc:
        return _render_form(request, session, values=data, errors=french_errors(exc), lot=lot)
    except FormError as exc:
        session.rollback()
        return _render_form(request, session, values=data, errors={exc.field: exc.message}, lot=lot)

    pricing.refresh_all(session, price_provider, fx_provider, settings)
    return RedirectResponse("/", status_code=303)


@router.delete("/lots/{lot_id}", response_class=HTMLResponse)
def delete_lot(
    request: Request,
    lot_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """HTMX: delete the lot and answer with the refreshed portfolio fragment."""
    lots.delete_lot(session, _get_lot_or_404(session, lot_id))
    view = build_portfolio(session, settings)
    return render(request, "partials/portfolio_content.html", {"view": view})


# ---- helpers -----------------------------------------------------------------


def _validate(data: dict[str, str], settings: Settings) -> LotForm:
    """Validate the posted form; "today" is taken in the display time zone."""
    today = local_today(settings.display_timezone)
    return LotForm.model_validate(data, context={"today": today})


def _get_lot_or_404(session: Session, lot_id: int) -> Transaction:
    lot = session.get(Transaction, lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="Achat introuvable")
    return lot


def _identity_values(asset: Asset) -> dict[str, str]:
    return {
        "isin": asset.isin,
        "exchange": asset.exchange,
        "currency": quoted_currency_code(asset.currency, asset.price_scale),
    }


def _asset_values(asset: Asset) -> dict[str, str]:
    """Form values describing an existing asset."""
    values = {
        **_identity_values(asset),
        "name": asset.name,
        "asset_type": asset.asset_type,
        "ticker": asset.ticker or "",
    }
    if asset.manual_price:
        values["manual_price"] = "on"
    return values


def _lot_values(lot: Transaction) -> dict[str, str]:
    """Form values of an existing lot (edit mode)."""
    return {
        **_asset_values(lot.asset),
        "trade_date": format_date(lot.trade_date),
        "quantity": format_input_number(lot.quantity),
        "unit_price": format_input_number(lot.unit_price),
        "fees": format_input_number(lot.fees),
        "fx_rate": format_input_number(lot.fx_rate),
        "fx_source": lot.fx_source or "",
        "envelope_id": str(lot.envelope_id),
    }


def _fx_field_context(values: dict[str, str]) -> dict[str, Any]:
    """What partials/fx_field.html needs when it is rendered with the page."""
    iso_currency, _scale = parse_currency_input(values.get("currency", EUR))
    if not is_iso_currency(iso_currency) or iso_currency == EUR:
        return {"fx_currency": None}

    source = values.get("fx_source") or "manual"
    if source == "manual":
        message = "Indiquez le taux appliqué par votre courtier."
    else:
        message = (
            "Taux de référence du jour d'achat. "
            "Modifiable : indiquez le taux réel de votre courtier."
        )
    return {
        "fx_currency": iso_currency,
        "fx_value": values.get("fx_rate", ""),
        "fx_source": source,
        "fx_message": message,
    }


def _render_form(
    request: Request,
    session: Session,
    *,
    values: dict[str, str],
    errors: dict[str, str] | None = None,
    lot: Transaction | None = None,
) -> HTMLResponse:
    known_assets = [_asset_values(asset) for asset in lots.all_assets(session)]
    # Initial state of the Alpine component that drives the form (see app.js).
    form_state: dict[str, Any] = {
        "isin": values.get("isin", ""),
        "name": values.get("name", ""),
        "assetType": values.get("asset_type", "etf"),
        "ticker": values.get("ticker", ""),
        "exchange": values.get("exchange", ""),
        "currency": values.get("currency", EUR),
        "manualPrice": values.get("manual_price") == "on",
        "tradeDate": values.get("trade_date", ""),
        "quantity": values.get("quantity", ""),
        "unitPrice": values.get("unit_price", ""),
        "fees": values.get("fees", ""),
        "fxRate": values.get("fx_rate", ""),
        "fxSource": values.get("fx_source", ""),
        "isEdit": lot is not None,
        "knownAssets": known_assets,
    }
    context = page_context(
        request,
        session,
        "portfolio" if lot else "add",  # which sidebar entry is highlighted
        values=values,
        errors=errors or {},
        lot=lot,
        known_assets=known_assets,
        envelopes=envelopes.all_envelopes(session),
        form_state=form_state,
        today_iso=local_today(request.app.state.settings.display_timezone).isoformat(),
        **_fx_field_context(values),
    )
    return render(request, "lot_form.html", context, status_code=422 if errors else 200)
