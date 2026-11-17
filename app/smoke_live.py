"""One-off check of the real providers. Needs Internet access:

    python -m app.smoke_live

It asks Yahoo Finance and Frankfurter (ECB) a few well-known questions and
prints the answers, so that you can see at a glance whether the live
integrations work from your machine. Nothing is written to the database.
"""

import sys
from collections.abc import Callable
from datetime import date

from app.config import get_settings
from app.providers.base import ProviderError
from app.providers.frankfurter import FrankfurterProvider
from app.providers.yahoo import YahooFxProvider, YahooProvider

SATURDAY = date(2024, 3, 16)
PREVIOUS_FRIDAY = date(2024, 3, 15)


def main() -> int:
    settings = get_settings()
    yahoo = YahooProvider(timeout=settings.http_timeout_seconds)
    frankfurter = FrankfurterProvider(
        settings.frankfurter_base_url, timeout=settings.http_timeout_seconds
    )

    checks: list[tuple[str, Callable[[], object]]] = [
        ("Yahoo: listings of ISIN IE00B4L5Y983", lambda: yahoo.search("IE00B4L5Y983")),
        ("Yahoo: quote of MSFT (USD)", lambda: yahoo.get_quote("MSFT")),
        ("Yahoo: quote of SWDA.L (pence)", lambda: yahoo.get_quote("SWDA.L")),
        (
            "Frankfurter: EUR/USD on Saturday 2024-03-16",
            lambda: frankfurter.get_rate("USD", SATURDAY),
        ),
        (
            "Yahoo FX: EUR/USD on Saturday 2024-03-16",
            lambda: YahooFxProvider(yahoo).get_rate("USD", SATURDAY),
        ),
    ]

    failures = 0
    for label, check in checks:
        try:
            answer = check()
        except ProviderError as exc:
            failures += 1
            print(f"FAIL  {label}\n        {exc}")
            continue
        print(f"OK    {label}")
        for item in answer if isinstance(answer, list) else [answer]:
            print(f"        {item}")

    print()
    print("What to look for:")
    print("- listings: IWDA.L first (found by ISIN), then other exchanges found by name")
    print("  (IWDA.AS, EUNL.DE...), each with a currency;")
    print("- SWDA.L: currency='GBP' and price_scale=0.01 (Yahoo quotes it in pence);")
    print(f"- both FX answers: rate_date={PREVIOUS_FRIDAY} (the Friday before that Saturday).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
