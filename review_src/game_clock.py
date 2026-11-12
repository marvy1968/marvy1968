"""One game-clock parser for the Kalshi and Polymarket live-prop lanes.

Colon-less values such as '38.0' and '0.0' are ambiguous: pacer_nba_live.clk
and live_props.remaining used to treat them as minutes, while
pm_nba_live.remaining and nba_pacer_shadow.clk treated them as seconds.
Both readings are rejected. A clock is usable only as M:SS (seconds < 60)
whose length is within the period. None and other bad strings fail closed
so the caller skips the bet. '0:00' is a real end-of-period clock and is
kept; '0.0' is not.

Soccer clocks stay on the ESPN elapsed form ("67'", "45'+2'") and are not
read as a colon-less float.
"""
import re


def parse_period_clock(clock, period_minutes):
    """Minutes left in the current period, or None if the clock cannot be used."""
    if clock is None or isinstance(clock, (bool, int, float)):
        return None
    text = str(clock).strip()
    match = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)", text)
    if not match:
        return None
    minutes = int(match.group(1))
    seconds = float(match.group(2))
    if seconds >= 60.0:
        return None
    left = minutes + seconds / 60.0
    try:
        cap = float(period_minutes)
    except (TypeError, ValueError):
        return None
    if cap < 0 or left - cap > 1e-9:
        return None
    return left


def game_minutes_left(period, clock, *, period_minutes, n_periods, ot_minutes=None):
    """Regulation minutes still on the clock, counting full periods not yet started.

    Overtime (period > n_periods) returns only the current overtime clock.
    None means skip the bet.
    """
    try:
        per = int(period)
        n_per = int(n_periods)
        reg = float(period_minutes)
    except (TypeError, ValueError):
        return None
    if per <= 0 or n_per <= 0:
        return None
    cap = reg if per <= n_per else (reg if ot_minutes is None else ot_minutes)
    left = parse_period_clock(clock, cap)
    if left is None:
        return None
    if per > n_per:
        return left
    return (n_per - per) * reg + left


def epl_minutes_left(clock, length=90.0):
    """Minutes left from an ESPN soccer elapsed clock ('67'', '45'+2'').

    The stoppage suffix is accepted and, matching the previous live_props
    regex, the first number is the elapsed minute. A bare float is rejected.
    """
    if clock is None or isinstance(clock, (bool, int, float)):
        return None
    text = str(clock).strip()
    match = re.fullmatch(r"(\d+)'(?:\+\d+')?", text)
    if not match:
        return None
    try:
        length = float(length)
        elapsed = float(match.group(1))
    except (TypeError, ValueError):
        return None
    if length <= 0 or elapsed < 0 or elapsed > length + 15:
        return None
    return max(0.0, length - elapsed)
