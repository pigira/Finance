"""FastAPI application: wiring of settings, database, providers, routes."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import APP_NAME
from app.config import Settings, get_settings
from app.db import init_db, make_engine, make_session_factory
from app.providers import build_fx_provider, build_price_provider
from app.routers import fragments, lots, pages
from app.security import SameOriginMiddleware
from app.services import pricing
from app.web import STATIC_DIR, build_templates

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests pass their own settings (temp database, mock providers)."""
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)

    app = FastAPI(
        title=APP_NAME,
        lifespan=_lifespan,
        # The app serves HTML, not a JSON API: no interactive API docs.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.price_provider = build_price_provider(settings)
    app.state.fx_provider = build_fx_provider(settings)
    app.state.templates = build_templates(settings)

    app.add_middleware(SameOriginMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(pages.router)
    app.include_router(lots.router)
    app.include_router(fragments.router)
    return app


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the periodic refresh when the server starts, stop it on shutdown."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")
    settings: Settings = app.state.settings

    task: asyncio.Task[None] | None = None
    if settings.background_refresh_hours > 0:
        task = asyncio.create_task(_background_refresh_loop(app))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        app.state.engine.dispose()


async def _background_refresh_loop(app: FastAPI) -> None:
    """Refresh every price and rate, every BACKGROUND_REFRESH_HOURS, forever."""
    settings: Settings = app.state.settings
    interval_seconds = settings.background_refresh_hours * 3600
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            # The refresh does blocking I/O: run it in a worker thread so the
            # event loop keeps serving pages meanwhile.
            report = await asyncio.to_thread(
                pricing.run_refresh,
                app.state.session_factory,
                app.state.price_provider,
                app.state.fx_provider,
                settings,
            )
            logger.info("background refresh: %s", report)
        except Exception:  # noqa: BLE001 - the loop must survive any failure
            logger.exception("background refresh failed")


app = create_app()
