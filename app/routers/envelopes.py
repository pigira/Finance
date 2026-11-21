"""Paramètres > Comptes: add, edit and delete envelopes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.formatting import format_date
from app.models import Envelope
from app.providers.base import FxProvider, PriceProvider
from app.routers.pages import render_settings
from app.schemas import EnvelopeForm, french_errors
from app.services import envelopes
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


@router.get("/settings/envelopes/new", response_class=HTMLResponse)
def new_envelope_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return _render_form(request, session, values={"envelope_type": "cto"})


@router.post("/settings/envelopes", response_class=HTMLResponse)
def create_envelope(
    request: Request,
    data: dict[str, str] = Depends(read_form),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    try:
        envelopes.create_envelope(session, _validate(data, settings))
    except ValidationError as exc:
        return _render_form(request, session, values=data, errors=french_errors(exc))
    except envelopes.EnvelopeError as exc:
        session.rollback()
        return _render_form(request, session, values=data, errors={exc.field: exc.message})
    return RedirectResponse("/settings", status_code=303)


@router.get("/settings/envelopes/{envelope_id}/edit", response_class=HTMLResponse)
def edit_envelope_page(
    request: Request, envelope_id: int, session: Session = Depends(get_session)
) -> HTMLResponse:
    envelope = _get_envelope_or_404(session, envelope_id)
    return _render_form(request, session, values=_envelope_values(envelope), envelope=envelope)


@router.post("/settings/envelopes/{envelope_id}", response_class=HTMLResponse)
def update_envelope(
    request: Request,
    envelope_id: int,
    data: dict[str, str] = Depends(read_form),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    envelope = _get_envelope_or_404(session, envelope_id)
    try:
        envelopes.update_envelope(session, envelope, _validate(data, settings))
    except ValidationError as exc:
        errors = french_errors(exc)
        return _render_form(request, session, values=data, errors=errors, envelope=envelope)
    except envelopes.EnvelopeError as exc:
        session.rollback()
        errors = {exc.field: exc.message}
        return _render_form(request, session, values=data, errors=errors, envelope=envelope)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/envelopes/{envelope_id}/delete", response_class=HTMLResponse)
def delete_envelope(
    request: Request,
    envelope_id: int,
    session: Session = Depends(get_session),
    price_provider: PriceProvider = Depends(get_price_provider),
    fx_provider: FxProvider = Depends(get_fx_provider),
):
    try:
        envelopes.delete_envelope(session, _get_envelope_or_404(session, envelope_id))
    except envelopes.EnvelopeError as exc:
        session.rollback()
        return render_settings(
            request, session, price_provider, fx_provider, envelope_error=exc.message
        )
    return RedirectResponse("/settings", status_code=303)


def _get_envelope_or_404(session: Session, envelope_id: int) -> Envelope:
    envelope = session.get(Envelope, envelope_id)
    if envelope is None:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    return envelope


def _envelope_values(envelope: Envelope) -> dict[str, str]:
    """Form values of an existing envelope (edit mode)."""
    return {
        "name": envelope.name,
        "envelope_type": envelope.envelope_type,
        "broker": envelope.broker or "",
        "opening_date": format_date(envelope.opening_date) if envelope.opening_date else "",
    }


def _validate(data: dict[str, str], settings: Settings) -> EnvelopeForm:
    today = local_today(settings.display_timezone)
    return EnvelopeForm.model_validate(data, context={"today": today})


def _render_form(
    request: Request,
    session: Session,
    *,
    values: dict[str, str],
    errors: dict[str, str] | None = None,
    envelope: Envelope | None = None,
) -> HTMLResponse:
    context = page_context(
        request,
        session,
        "settings",
        values=values,
        errors=errors or {},
        envelope=envelope,
        envelope_types=envelopes.ENVELOPE_TYPE_LABELS,
    )
    return render(request, "envelope_form.html", context, status_code=422 if errors else 200)
