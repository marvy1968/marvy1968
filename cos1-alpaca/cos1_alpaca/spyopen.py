"""Marvin's intraday rule: SPY versus today's open.

Above the open, look long. Below the open, look short. The side is recomputed
every run, so a reversal flips the bias. Muse's tests found this filter hurt
every system, so it stays off unless config turns it on.
"""

from __future__ import annotations

from datetime import date

from .clock import bar_session_date
from .types import Bar


def spy_open_bias(bars: list[Bar], session_date: date) -> str:
    """``long``, ``short``, or ``flat`` from the session open to the latest completed bar."""
    today = [bar for bar in bars if bar_session_date(bar.ts) == session_date]
    if not today:
        return "flat"
    opened = today[0].open
    last = today[-1].close
    if last > opened:
        return "long"
    if last < opened:
        return "short"
    return "flat"


def spy_open_decision(side: str, bias: str, enabled: bool) -> tuple[bool, str]:
    if not enabled:
        return True, "bypass"
    if bias == "flat":
        return False, "flat"
    if side == bias:
        return True, f"allow {bias}"
    return False, f"block want={side} spy={bias}"
