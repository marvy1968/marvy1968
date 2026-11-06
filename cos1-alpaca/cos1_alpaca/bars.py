"""Completed bars only, and how far back a request must start."""

from __future__ import annotations

from datetime import datetime, timedelta

from .clock import ET, as_et, bar_session_date
from .errors import FeedRefused
from .types import Bar, Session


def assert_iex(feed: str) -> str:
    if str(feed).lower() != "iex":
        raise FeedRefused(str(feed))
    return "iex"


def history_start(now: datetime, kind: str) -> datetime:
    """Daily history covers a 200-day average plus slope and a buffer."""
    local = as_et(now)
    if kind == "daily":
        return local - timedelta(days=520)
    if kind == "day":
        return local - timedelta(days=12)
    return local - timedelta(days=5)


MIN_DAILY_BARS = 210


def drop_incomplete(bars: list[Bar], now: datetime, timeframe_minutes: int, sessions: dict | None = None) -> list[Bar]:
    """Drop the bar that is still forming. Daily bars stay out until that session's close."""
    local_now = as_et(now)
    kept: list[Bar] = []
    for bar in bars:
        if timeframe_minutes >= 1440:
            session_day = bar_session_date(bar.ts)
            close_at = None
            if sessions and session_day in sessions:
                close_at = sessions[session_day].close
            if session_day > local_now.date():
                continue
            if session_day == local_now.date():
                if close_at is None or local_now < close_at:
                    continue
            elif close_at is not None and local_now < close_at:
                continue
            kept.append(bar)
            continue
        end = as_et(bar.ts) + timedelta(minutes=timeframe_minutes)
        if end <= local_now:
            kept.append(bar)
    return kept


def sessions_by_day(sessions: list[Session]) -> dict:
    return {session.day: session for session in sessions}


def age_seconds(ts: datetime | None, now: datetime) -> float | None:
    if ts is None:
        return None
    return (as_et(now) - as_et(ts)).total_seconds()
