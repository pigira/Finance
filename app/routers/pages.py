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
    return _csv_download("transactions", transactions_csv(session), settings)


@router.get("/export/envelopes.csv")
def export_envelopes(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    return _csv_download("comptes", envelopes_csv(session), settings)


def _csv_download(name: str, content: str, settings: Settings) -> Response:
    """A CSV file to save, dated with today's date: "transactions-2026-11-21.csv"."""
    filename = f"{name}-{local_today(settings.display_timezone).isoformat()}.csv"
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
