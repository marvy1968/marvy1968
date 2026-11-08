"""America/Chicago clock for the alert policy. Eastern game dates are not weekdays."""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
SUMMARY_AT = time(21, 0)


def as_ct(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("naive timestamps are unknown, not Central")
    return now.astimezone(CT)


def ct_date(now: datetime):
    return as_ct(now).date()


def is_weekend(now: datetime) -> bool:
    """Saturday and Sunday in America/Chicago. Monday through Friday are weekdays."""
    return as_ct(now).weekday() >= 5


def summary_slot(now: datetime) -> str | None:
    """One weekday slot, the Central date, once local time is 21:00 or later."""
    local = as_ct(now)
    if local.weekday() >= 5 or local.time() < SUMMARY_AT:
        return None
    return local.date().isoformat()
