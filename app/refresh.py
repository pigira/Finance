"""Command-line refresh of all prices and FX rates, meant for cron:

    python -m app.refresh

It ignores the 15-minute cache. The exit code is 1 when at least one price or
rate could not be fetched, so that cron can report the problem.
"""

import logging
import sys

from app.config import get_settings
from app.db import init_db, make_engine, make_session_factory
from app.providers import build_fx_provider, build_price_provider
from app.services import pricing


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)

    report = pricing.run_refresh(
        make_session_factory(engine),
        build_price_provider(settings),
        build_fx_provider(settings),
        settings,
    )
    print(
        f"prices: {report.prices_updated} updated, {report.prices_failed} failed | "
        f"fx: {report.fx_updated} updated, {report.fx_failed} failed"
    )
    return 1 if report.prices_failed or report.fx_failed else 0


if __name__ == "__main__":
    sys.exit(main())
