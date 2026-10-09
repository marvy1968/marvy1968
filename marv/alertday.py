"""Day-of alerts: unsolicited Telegram messages only cover games that start today (Eastern time).

Queries (/board, /game, /gaps, /check, /grade) and the stored projections still cover every upcoming game;
set ALERT_DAY_ONLY=false in .env to send alerts for upcoming days as well.
"""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def day_only() -> bool:
    return os.environ.get("ALERT_DAY_ONLY", "true").lower() not in ("0", "false", "no", "off")


def is_today(start, now: datetime | None = None) -> bool:
    """True when `start` (datetime or ISO string) falls on today's Eastern date; always True if the rule is off."""
    if not day_only():
        return True
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return start.astimezone(ET).date() == now.astimezone(ET).date()
