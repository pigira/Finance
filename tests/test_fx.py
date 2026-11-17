"""FX lookup: weekend/holiday fallback, caching, Frankfurter payloads."""

from datetime import date, datetime

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import FxRate
from app.providers import FallbackFxProvider
from app.providers.base import FxProvider, FxQuote, ProviderError
from app.providers.frankfurter import FrankfurterProvider
from app.providers.mock import MockFxProvider
from app.services.fx import MAX_LOOKBACK_DAYS, fetch_rate_with_lookback, get_rate_for_date

FRIDAY = date(2024, 3, 15)
SATURDAY = date(2024, 3, 16)
SUNDAY = date(2024, 3, 17)


class BusinessDaysOnlyFx(FxProvider):
    """Publishes on weekdays only and does *not* fall back by itself."""

    name = "ecb"

    def __init__(self) -> None:
        self.asked: list[date] = []

    def get_rate(self, currency: str, on_date: date) -> FxQuote | None:
        self.asked.append(on_date)
        if on_date.weekday() >= 5:
            return None
        return FxQuote(currency, on_date, 1.0 + on_date.day / 1000, self.name)


class NeverPublishesFx(FxProvider):
    name = "ecb"

    def __init__(self) -> None:
        self.calls = 0

    def get_rate(self, currency: str, on_date: date) -> FxQuote | None:
        self.calls += 1
        return None


class BrokenFx(FxProvider):
    name = "ecb"

    def get_rate(self, currency: str, on_date: date) -> FxQuote | None:
        raise ProviderError("network down")


def test_weekend_uses_the_last_previous_business_day() -> None:
    provider = BusinessDaysOnlyFx()

    saturday_quote = fetch_rate_with_lookback(provider, "USD", SATURDAY)
    assert saturday_quote is not None
    assert saturday_quote.rate_date == FRIDAY
    assert saturday_quote.rate == pytest.approx(1.015)
    assert provider.asked == [SATURDAY, FRIDAY]

    provider.asked.clear()
    sunday_quote = fetch_rate_with_lookback(provider, "USD", SUNDAY)
    assert sunday_quote is not None
    assert sunday_quote.rate_date == FRIDAY
    assert provider.asked == [SUNDAY, SATURDAY, FRIDAY]


def test_business_day_is_answered_directly() -> None:
    provider = BusinessDaysOnlyFx()
    quote = fetch_rate_with_lookback(provider, "USD", FRIDAY)
    assert quote is not None
    assert quote.rate_date == FRIDAY
    assert provider.asked == [FRIDAY]


def test_lookback_gives_up_after_a_week() -> None:
    provider = NeverPublishesFx()
    assert fetch_rate_with_lookback(provider, "USD", SATURDAY) is None
    assert provider.calls == MAX_LOOKBACK_DAYS + 1


def test_weekend_rate_is_cached_under_the_requested_date(
    session: Session, settings: Settings
) -> None:
    provider = BusinessDaysOnlyFx()

    first = get_rate_for_date(session, provider, settings, "USD", SATURDAY)
    assert first.rate == pytest.approx(1.015)
    cached = session.get(FxRate, ("USD", SATURDAY))
    assert cached is not None
    assert cached.rate == pytest.approx(1.015)
    assert cached.source == "ecb"

    # A past date never changes: the second lookup must not call the provider.
    provider.asked.clear()
    second = get_rate_for_date(session, provider, settings, "USD", SATURDAY)
    assert second.rate == pytest.approx(1.015)
    assert provider.asked == []


def test_todays_rate_follows_the_cache_ttl(session: Session, settings: Settings) -> None:
    provider = BusinessDaysOnlyFx()
    monday_noon = datetime(2024, 3, 18, 12, 0)
    monday = monday_noon.date()

    get_rate_for_date(session, provider, settings, "USD", monday, now=monday_noon)
    get_rate_for_date(session, provider, settings, "USD", monday, now=datetime(2024, 3, 18, 12, 5))
    assert provider.asked == [monday]  # 5 minutes later: still fresh

    get_rate_for_date(session, provider, settings, "USD", monday, now=datetime(2024, 3, 18, 13, 0))
    assert provider.asked == [monday, monday]  # an hour later: fetched again


def test_no_rate_at_all_is_an_error(session: Session, settings: Settings) -> None:
    with pytest.raises(ProviderError):
        get_rate_for_date(session, NeverPublishesFx(), settings, "USD", SATURDAY)


def test_mock_provider_answers_weekends_with_friday() -> None:
    provider = MockFxProvider()
    saturday = provider.get_rate("USD", SATURDAY)
    friday = provider.get_rate("USD", FRIDAY)
    assert saturday is not None and friday is not None
    assert saturday.rate_date == FRIDAY
    assert saturday.rate == friday.rate
    with pytest.raises(ProviderError):
        provider.get_rate("XXX", FRIDAY)


# ---- Frankfurter (HTTP answers are simulated with httpx.MockTransport) --------


def frankfurter(handler) -> FrankfurterProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FrankfurterProvider(base_url="https://fx.example/v1/", http_client=client)


def test_frankfurter_reports_the_effective_date_of_a_weekend_request() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        # What the real API answers for a Saturday: Friday's rate, dated Friday.
        return httpx.Response(
            200, json={"amount": 1.0, "base": "EUR", "date": "2024-03-15", "rates": {"USD": 1.0892}}
        )

    quote = frankfurter(handler).get_rate("USD", SATURDAY)

    assert quote == FxQuote("USD", FRIDAY, 1.0892, "ecb")
    assert seen[0].path == "/v1/2024-03-16"
    assert seen[0].params["base"] == "EUR"
    assert seen[0].params["symbols"] == "USD"


def test_frankfurter_404_means_no_rate() -> None:
    provider = frankfurter(lambda request: httpx.Response(404, json={"message": "not found"}))
    assert provider.get_rate("USD", date(1990, 1, 1)) is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"date": "2024-03-15", "rates": {}}),
        httpx.Response(200, json={"date": "2024-03-15", "rates": {"USD": 0}}),
    ],
)
def test_frankfurter_bad_answers_raise_provider_error(response: httpx.Response) -> None:
    with pytest.raises(ProviderError):
        frankfurter(lambda request: response).get_rate("USD", FRIDAY)


def test_frankfurter_network_failure_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(ProviderError):
        frankfurter(handler).get_rate("USD", FRIDAY)


# ---- fallback chain -----------------------------------------------------------


def test_fallback_provider_is_used_when_the_primary_fails() -> None:
    provider = FallbackFxProvider(primary=BrokenFx(), fallback=MockFxProvider())
    quote = provider.get_rate("USD", FRIDAY)
    assert quote is not None
    assert quote.source == "mock"


def test_fallback_provider_is_not_used_when_the_primary_answers() -> None:
    provider = FallbackFxProvider(primary=BusinessDaysOnlyFx(), fallback=BrokenFx())
    quote = provider.get_rate("USD", FRIDAY)
    assert quote is not None
    assert quote.source == "ecb"


def test_fallback_failure_is_reported() -> None:
    provider = FallbackFxProvider(primary=BrokenFx(), fallback=BrokenFx())
    with pytest.raises(ProviderError):
        provider.get_rate("USD", FRIDAY)
