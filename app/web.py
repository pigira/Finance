"""Shared web plumbing: templates, Jinja filters and FastAPI dependencies."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import APP_NAME, formatting
from app.config import Settings
from app.providers.base import FxProvider, PriceProvider
from app.services.envelopes import ENVELOPE_TYPE_LABELS
from app.services.portfolio import last_refresh_at
from app.timeutil import utc_now

APP_DIR = Path(__file__).parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


def build_templates(settings: Settings) -> Jinja2Templates:
    """Create the Jinja environment and register the French formatting filters."""
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    tz_name = settings.display_timezone

    templates.env.filters.update(
        money=formatting.format_money,
        pct=formatting.format_percent,
        qty=formatting.format_quantity,
        rate=formatting.format_rate,
        date_fr=formatting.format_date,
        datetime_fr=lambda value: formatting.format_datetime(value, tz_name),
        short_datetime=lambda value: formatting.format_short_datetime(value, tz_name, utc_now()),
        input_number=formatting.format_input_number,
        trend=formatting.trend,
        pct_trend=formatting.percent_trend,
    )
    templates.env.globals.update(
        app_name=APP_NAME,
        settings=settings,
        envelope_type_labels=ENVELOPE_TYPE_LABELS,
        # Changes whenever a static file changes, so browsers never serve a stale copy.
        static_version=_static_version(),
    )
    return templates


def _static_version() -> str:
    newest = max(
        (path.stat().st_mtime for path in STATIC_DIR.rglob("*") if path.is_file()), default=0
    )
    return str(int(newest))


def render(
    request: Request, template_name: str, context: dict[str, Any], status_code: int = 200
) -> HTMLResponse:
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, template_name, context, status_code=status_code)


def page_context(
    request: Request, session: Session, active_page: str, **extra: Any
) -> dict[str, Any]:
    """Context needed by base.html (sidebar highlight, top bar) plus page-specific values."""
    settings: Settings = request.app.state.settings
    return {
        "active_page": active_page,
        "last_refresh_at": last_refresh_at(session, settings.display_timezone),
        **extra,
    }


# ---- dependencies ------------------------------------------------------------


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_price_provider(request: Request) -> PriceProvider:
    return request.app.state.price_provider


def get_fx_provider(request: Request) -> FxProvider:
    return request.app.state.fx_provider


async def read_form(request: Request) -> dict[str, str]:
    """The posted form as a plain dict of strings (uploaded files are ignored)."""
    form = await request.form()
    return {key: value for key, value in form.items() if isinstance(value, str)}
