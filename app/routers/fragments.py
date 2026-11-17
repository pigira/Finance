"""HTMX endpoints. Each one answers with an HTML fragment, never a full page."""

from datetime import date
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.calc import EUR
from app.config import Settings
from app.db import get_session
from app.formatting import format_date, format_input_number
from app.isin import is_valid_isin, normalize_isin
from app.models import Asset
from app.providers.base import (
    FxProvider,
    PriceProvider,
    ProviderError,
    is_iso_currency,
    parse_currency_input,
    quoted_currency_code,
)
from app.schemas import parse_date, parse_decimal
from app.services import pricing
from app.services.fx import get_rate_for_date
from app.services.portfolio import build_portfolio, last_refresh_at
from app.web import get_fx_provider, get_price_provider, get_settings, render

router = APIRouter()

FX_SOURCE_LABELS = {"ecb": "BCE", "yahoo": "Yahoo Finance", "mock": "de démonstration"}


@router.post("/refresh", response_class=HTMLResponse)
def refresh(
    request: Request,
    auto: bool = False,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    price_provider: PriceProvider = Depends(get_price_provider),
    fx_provider: FxProvider = Depends(get_fx_provider),
) -> HTMLResponse:
    """Refresh stale prices, then answer with the new "last refresh" label and,
    when the request comes from the portfolio page, the portfolio itself
    (as an out-of-band swap).

    `auto=1` is sent by the automatic refresh on page load: it does not retry
    tickers that have just failed. The "Actualiser" button does.
    """
    pricing.refresh_all(session, price_provider, fx_provider, settings, retry_failed=not auto)

    # HTMX sends the address of the page the request comes from.
    current_path = urlsplit(request.headers.get("HX-Current-URL", "/")).path
    view = build_portfolio(session, settings) if current_path == "/" else None
    refreshed_at = last_refresh_at(session, settings.display_timezone)
    context = {"last_refresh_at": refreshed_at, "view": view, "oob": True}
    return render(request, "partials/refresh_response.html", context)


@router.get("/fragments/listings", response_class=HTMLResponse)
def listing_candidates(
    request: Request,
    isin: str = "",
    price_provider: PriceProvider = Depends(get_price_provider),
) -> HTMLResponse:
    """ "Rechercher la cotation": candidate listings of an ISIN."""
    isin = normalize_isin(isin)
    context: dict[str, object] = {"isin": isin, "candidates": [], "error": None}

    if not is_valid_isin(isin):
        context["error"] = "Saisissez d'abord un ISIN valide (12 caractères)."
    else:
        try:
            listings = price_provider.search(isin)
        except ProviderError:
            listings = []
            context["error"] = (
                "Recherche indisponible pour le moment. "
                "Saisissez le ticker, la place et la devise à la main."
            )
        context["candidates"] = [
            {
                "ticker": listing.ticker,
                "exchange": listing.exchange,
                "exchange_name": listing.exchange_name or listing.exchange,
                # What goes into the form's currency field ("GBX" for a quote in pence).
                "currency": quoted_currency_code(listing.currency, listing.price_scale),
                "name": listing.name,
                "asset_type": listing.asset_type,
                "isin_match": listing.isin_match,
            }
            for listing in listings
        ]
    return render(request, "partials/listing_candidates.html", context)


@router.get("/fragments/fx-rate", response_class=HTMLResponse)
def fx_rate_field(
    request: Request,
    currency: str = "",
    trade_date: str = "",
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    fx_provider: FxProvider = Depends(get_fx_provider),
) -> HTMLResponse:
    """The FX rate field of the purchase form, prefilled for (currency, date)."""
    iso_currency, _scale = parse_currency_input(currency)
    if not is_iso_currency(iso_currency) or iso_currency == EUR:
        return HTMLResponse("")  # no FX rate needed (or not answerable yet)

    context = {"fx_currency": iso_currency, "fx_value": "", "fx_source": "manual", "errors": {}}
    try:
        on_date: date = parse_date(trade_date)
    except ValueError:
        context["fx_message"] = "Indiquez la date d'achat pour préremplir le taux."
        return render(request, "partials/fx_field.html", context)

    try:
        quote = get_rate_for_date(session, fx_provider, settings, iso_currency, on_date)
    except ProviderError:
        context["fx_message"] = "Taux indisponible : saisissez celui appliqué par votre courtier."
        context["fx_failed"] = True
        return render(request, "partials/fx_field.html", context)

    label = FX_SOURCE_LABELS.get(quote.source, quote.source)
    message = f"Taux {label} applicable le {format_date(on_date)}"
    if quote.rate_date != on_date:
        # Weekend or holiday: the provider answered with an earlier publication.
        message += f" (publié le {format_date(quote.rate_date)}, dernier jour ouvré précédent)"
    context.update(
        fx_value=format_input_number(round(quote.rate, 6)),
        fx_source=quote.source,
        fx_message=message + ". Modifiable : indiquez le taux réel de votre courtier.",
    )
    return render(request, "partials/fx_field.html", context)


@router.post("/assets/{asset_id}/price", response_class=HTMLResponse)
def set_manual_price(
    request: Request,
    asset_id: int,
    price: str = Form(""),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Inline edit of the current price of a manual-price asset."""
    asset = session.get(Asset, asset_id)
    if asset is None or not asset.manual_price:
        raise HTTPException(status_code=404, detail="Actif à prix manuel introuvable")

    flash_error = None
    try:
        value = parse_decimal(price)
    except ValueError:
        value = None
    if value is None or value <= 0:
        flash_error = f"Prix invalide pour {asset.name} : saisissez un nombre supérieur à 0."
    else:
        pricing.set_manual_price(session, asset, value, settings)

    view = build_portfolio(session, settings)
    context = {"view": view, "flash_error": flash_error}
    return render(request, "partials/portfolio_content.html", context)
