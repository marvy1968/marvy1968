"""Session clock in America/New_York, including half-days."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .types import Session

ET = ZoneInfo("America/New_York")


def as_et(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=ET)
    return ts.astimezone(ET)


def session_for(day: date, open_t: time, close_t: time) -> Session:
    """Build a session. A close before 16:00 ET is a half-day."""
    open_dt = datetime.combine(day, open_t, tzinfo=ET)
    close_dt = datetime.combine(day, close_t, tzinfo=ET)
    return Session(day=day, open=open_dt, close=close_dt, half_day=close_dt.hour < 16)


def entry_cutoff(session: Session, minutes_before_close: int = 60) -> datetime:
    """No new day entries at or after this time. 60 minutes before 16:00 is 15:00 ET."""
    return session.close - timedelta(minutes=minutes_before_close)


def flatten_at(session: Session, minutes_before_close: int = 10) -> datetime:
    """Day positions are flattened here. 10 minutes before 16:00 is 15:50 ET."""
    return session.close - timedelta(minutes=minutes_before_close)


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def combine_day_time(day: date, clock: time | datetime) -> datetime:
    if isinstance(clock, datetime):
        return as_et(clock).replace(year=day.year, month=day.month, day=day.day) if clock.tzinfo else datetime.combine(day, clock.time(), tzinfo=ET)
    return datetime.combine(day, clock, tzinfo=ET)


def bar_session_date(ts: datetime) -> date:
    """Alpaca daily bars are often stamped at 00:00 of the session date."""
    local = as_et(ts)
    return local.date()
