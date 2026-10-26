from __future__ import annotations

from datetime import datetime, time
from typing import Any

from core.common import utc_now


def parse_clock(
    value: str,
) -> time:
    hour, minute = value.split(":")

    return time(
        int(hour),
        int(minute),
    )


def session_is_active(
    current: time,
    start: time,
    end: time,
) -> bool:
    if start <= end:
        return (
            start
            <= current
            < end
        )

    return (
        current
        >= start
        or current
        < end
    )


def active_sessions(
    config: dict[str, Any],
    moment: datetime | None = None,
) -> list[str]:
    timestamp = (
        moment
        if moment is not None
        else utc_now()
    )

    current = timestamp.time()
    result: list[str] = []

    for name, settings in config.items():
        start = parse_clock(
            settings["start_utc"]
        )

        end = parse_clock(
            settings["end_utc"]
        )

        if session_is_active(
            current,
            start,
            end,
        ):
            result.append(name)

    return result


def primary_session(
    sessions: list[str],
) -> str:
    if (
        "london" in sessions
        and "new_york" in sessions
    ):
        return "london_new_york_overlap"

    if (
        "sydney" in sessions
        and "tokyo" in sessions
    ):
        return "sydney_tokyo_overlap"

    if not sessions:
        return "closed_or_transition"

    return "_".join(sessions)
