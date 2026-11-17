"""Full pages: portfolio dashboard, settings, CSV export."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import Asset, Transaction
from app.providers.base import FxProvider, PriceProvider
from app.services.export import transactions_csv
from app.services.portfolio import build_portfolio
from app.timeutil import local_today
from app.web import get_fx_provider, get_price_provider, get_settings, page_context, render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def portfolio_page(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    view = build_portfolio(session, settings)
    return render(request, "portfolio.html", page_context(request, session, "portfolio", view=view))


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    price_provider: PriceProvider = Depends(get_price_provider),
    fx_provider: FxProvider = Depends(get_fx_provider),
) -> HTMLResponse:
    context = page_context(
        request,
        session,
        "settings",
        price_provider_name=price_provider.name,
        fx_provider_name=fx_provider.name,
        asset_count=session.scalar(select(func.count()).select_from(Asset)),
        transaction_count=session.scalar(select(func.count()).select_from(Transaction)),
    )
    return render(request, "settings.html", context)


@router.get("/export/transactions.csv")
def export_transactions(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    filename = f"transactions-{local_today(settings.display_timezone).isoformat()}.csv"
    return Response(
        content=transactions_csv(session),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
