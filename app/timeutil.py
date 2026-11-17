"""Clock helpers.

Convention: every datetime stored in the database is UTC *without* tzinfo
(SQLite has no time-zone-aware type). Conversion to the display time zone
happens only at the edge, in the templates.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    """Current time as a naive UTC datetime (the storage convention)."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_local(utc_naive: datetime, tz_name: str) -> datetime:
    """Convert a stored (naive UTC) datetime to an aware datetime in `tz_name`."""
    return utc_naive.replace(tzinfo=UTC).astimezone(ZoneInfo(tz_name))


def local_today(tz_name: str, now_utc: datetime | None = None) -> date:
    """Calendar date in the display time zone (decides which row is "today's")."""
    return to_local(now_utc or utc_now(), tz_name).date()
