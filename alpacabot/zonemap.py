"""Direction first, then zones (owner, 2026-10-14). Everything is computed from a stock's own DAILY bars, completed days only.

bias(daily)        +1 up / -1 down / 0 range, per date, as of that date's close (use the previous date's value for a session):
                   up = close > EMA20 > SMA50 and ADX14 >= 20; down = the mirror; anything else = range.
level_map(daily)   the stock's support/resistance zones: swing highs/lows (a day's high/low that is the extreme of the 2 days on each side)
                   over the last 120 days, merged when within 0.5 ATR14 of each other. A zone's `touches` counts how many swings
                   formed there, so a level price keeps turning at gets stronger every time it is retested. strong = touches >= 2.
zone_target(...)   take-profit for a trade: the nearest STRONG zone in the trade's direction at least min_r x risk away, else None.

Nothing here is tuned per stock: the same frozen rules run on every symbol; only the stock's own prices differ.
"""

import numpy as np
import pandas as pd

from .indicators import adx, atr, ema, sma

BIAS_RULES = {"ema": 20, "sma": 50, "adx_min": 20.0}
STAGE_RULES = {"ma": 150, "slope_bars": 20}          # Weinstein: the 30-week moving average = 150 trading days
LEVEL_RULES = {"lookback": 120, "wing": 2, "merge_atr": 0.5, "strong": 2}


def bias(daily: pd.DataFrame) -> pd.Series:
    c = daily["close"]
    e, s, a = ema(c, BIAS_RULES["ema"]), sma(c, BIAS_RULES["sma"]), adx(daily, 14)
    up = (c > e) & (e > s) & (a >= BIAS_RULES["adx_min"])
    dn = (c < e) & (e < s) & (a >= BIAS_RULES["adx_min"])
    return pd.Series(np.where(up, 1, np.where(dn, -1, 0)), index=daily.index)


def bias_for_session(daily: pd.DataFrame, session_date) -> int:
    """The bias decided at the last close BEFORE session_date (no look-ahead)."""
    if daily is None or not len(daily):
        return 0
    prior = daily[daily.index.date < session_date]
    if len(prior) < BIAS_RULES["sma"] + 15:
        return 0
    return int(bias(prior).iloc[-1])


def level_map(daily: pd.DataFrame) -> list[dict]:
    """Zones from completed daily bars (pass only bars before the session). Sorted by price."""
    d = daily.iloc[-LEVEL_RULES["lookback"]:]
    if len(d) < 2 * LEVEL_RULES["wing"] + 15:
        return []
    w = LEVEL_RULES["wing"]
    hi, lo = d["high"].to_numpy(float), d["low"].to_numpy(float)
    pts = []
    for i in range(w, len(d) - w):
        if hi[i] == hi[i - w:i + w + 1].max():
            pts.append(hi[i])
        if lo[i] == lo[i - w:i + w + 1].min():
            pts.append(lo[i])
    if not pts:
        return []
    tol = LEVEL_RULES["merge_atr"] * float(atr(d, 14).iloc[-1])
    zones = []
    for p in sorted(pts):
        if zones and p - zones[-1]["hi"] <= tol:
            z = zones[-1]
            z["prices"].append(p)
            z["hi"] = p
        else:
            zones.append({"prices": [p], "hi": p})
    return [{"price": round(float(np.mean(z["prices"])), 4), "touches": len(z["prices"]), "strong": len(z["prices"]) >= LEVEL_RULES["strong"]}
            for z in zones]


def zone_target(levels: list[dict], fill: float, side: int, risk: float, min_r: float = 1.0) -> float | None:
    """Nearest strong zone beyond fill + min_r x risk in the trade's direction (side +1 long, -1 short)."""
    cands = [z["price"] for z in levels if z["strong"] and side * (z["price"] - fill) >= min_r * risk]
    if not cands:
        return None
    return min(cands) if side == 1 else max(cands)


def attach(days: dict, daily_by_symbol: dict) -> dict:
    """Backtests: put each session's bias and level map on its intraday frame (df.attrs), computed from earlier daily bars only."""
    warm = BIAS_RULES["sma"] + 15
    for sym, frames in days.items():
        dly = daily_by_symbol.get(sym)
        if dly is None or not len(dly):
            continue
        b = bias(dly).to_numpy()
        dates = np.array(dly.index.date)
        for f in frames:
            dd = f.index[0].date()
            k = int(np.searchsorted(dates, dd))           # daily bars strictly before the session
            f.attrs["bias"] = int(b[k - 1]) if k >= warm else 0
            f.attrs["levels"] = level_map(dly.iloc[max(0, k - LEVEL_RULES["lookback"]):k]) if k else []
    return days


def stage(daily: pd.DataFrame) -> int:
    """Stan Weinstein's stage from completed daily bars (pass only bars before the session): the 30-week (150-day) average and its
    direction over the last 20 days. 2 = advancing (close above a rising average), 4 = declining (below a falling one), 3 = topping
    (above a falling average), 1 = basing (below a rising one). 0 = not enough history."""
    n, k = STAGE_RULES["ma"], STAGE_RULES["slope_bars"]
    if daily is None or len(daily) < n + k:
        return 0
    c = daily["close"].astype(float)
    ma = c.rolling(n).mean()
    up, above = ma.iloc[-1] > ma.iloc[-1 - k], c.iloc[-1] > ma.iloc[-1]
    return 2 if above and up else 4 if not above and not up else 3 if above else 1
