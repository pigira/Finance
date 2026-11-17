"""ECB reference rates through the Frankfurter API (https://frankfurter.dev).

The ECB publishes one reference rate per currency on every TARGET business
day, around 16:00 CET. Frankfurter exposes them as, for instance:

    GET {base}/2024-03-16?base=EUR&symbols=USD
    -> {"amount": 1.0, "base": "EUR", "date": "2024-03-15", "rates": {"USD": 1.0892}}

2024-03-16 is a Saturday: the API answers with the rate of the previous
business day and says so in "date". We keep that effective date in
`FxQuote.rate_date`. The service layer additionally walks back day by day when
a provider returns nothing, so the app does not depend on this behaviour.
"""

from datetime import date

import httpx

from app.providers.base import FxProvider, FxQuote, ProviderError


class FrankfurterProvider(FxProvider):
    name = "ecb"

    def __init__(
        self,
        base_url: str,
        timeout: float = 5.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=timeout)

    def get_rate(self, currency: str, on_date: date) -> FxQuote | None:
        url = f"{self._base_url}/{on_date.isoformat()}"
        try:
            response = self._client.get(url, params={"base": "EUR", "symbols": currency})
        except httpx.HTTPError as exc:
            raise ProviderError(f"Frankfurter unreachable: {exc}") from exc

        if response.status_code == 404:
            return None  # nothing published on or before that date
        if response.status_code != 200:
            raise ProviderError(f"Frankfurter answered HTTP {response.status_code} for {currency}")

        try:
            payload = response.json()
            rate = float(payload["rates"][currency])
            rate_date = date.fromisoformat(payload["date"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(f"Frankfurter: unexpected answer for {currency}") from exc

        if rate <= 0:
            raise ProviderError(f"Frankfurter: invalid rate {rate} for {currency}")
        return FxQuote(currency=currency, rate_date=rate_date, rate=rate, source=self.name)
