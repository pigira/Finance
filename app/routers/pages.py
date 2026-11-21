"""Full pages: portfolio dashboard, CSV export. Paramètres lives in settings.py."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.services.export import envelopes_csv, transactions_csv
from app.services.portfolio import build_portfolio
from app.timeutil import local_today
from app.web import get_settings, page_context, render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def portfolio_page(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    view = build_portfolio(session, settings)
    return render(request, "portfolio.html", page_context(request, session, "portfolio", view=view))


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


@router.get("/export/envelopes.csv")
def export_envelopes(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    filename = f"comptes-{local_today(settings.display_timezone).isoformat()}.csv"
    return Response(
        content=envelopes_csv(session),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
