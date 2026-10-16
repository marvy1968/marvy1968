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
STAGE_RULES = {"ma": 150, "slope_bars": 20}
WEEKLY_RULES = {"fast": 10, "slow": 30}              # weekly EMA10 vs EMA30 (about the 50- and 150-day averages)          # Weinstein: the 30-week moving average = 150 trading days
LEVEL_RULES = {"lookback": 120, "wing": 2, "merge_atr": 0.5, "strong": 2}
# The owner's OANDA range box (2026-10-14) scaled from 200 / 20 MA and 100 hourly bars to a stock's daily bars: 50 / 10 MA and 40 days.
BOX_RULES = {"lookback": 40, "slow": 50, "fast": 10, "flat_atr": 0.5, "flat_share": 0.8, "min_crosses": 2, "min_h_atr": 2.0, "max_h_atr": 20.0}


def bias(daily: pd.DataFrame) -> pd.Series:
    c = daily["close"]
    e, s, a = ema(c, BIAS_RULES["ema"]), sma(c, BIAS_RULES["sma"]), adx(daily, 14)
    up = (c > e) & (e > s) & (a >= BIAS_RULES["adx_min"])
    dn = (c < e) & (e < s) & (a >= BIAS_RULES["adx_min"])
    return pd.Series(np.where(up, 1, np.where(dn, -1, 0)), index=daily.index)


def range_box(daily: pd.DataFrame) -> pd.DataFrame:
    """The owner's range box on daily bars, as of each date's close (columns valid, top, bot): RANGING = the 50-day average is flat (moved <= 0.5 ATR
    over 10 days) on at least 80% of the last 40 days and the 10-day average crossed it at least twice in them; the BOX = the high / low of the
    prior 40 days, 2-20 ATR14 tall. Use the previous date's row for a session (no look-ahead)."""
    r = BOX_RULES
    c, a = daily["close"], atr(daily, 14)
    slow, fast = sma(c, r["slow"]), sma(c, r["fast"])
    flat = (slow - slow.shift(10)).abs() <= r["flat_atr"] * a
    side = np.sign(fast - slow)
    crosses = (side != side.shift(1)).astype(float).where(side.shift(1).notna(), 0.0).rolling(r["lookback"]).sum()
    ranging = (flat.astype(float).rolling(r["lookback"]).mean() >= r["flat_share"]) & (crosses >= r["min_crosses"])
    top, bot = daily["high"].shift(1).rolling(r["lookback"]).max(), daily["low"].shift(1).rolling(r["lookback"]).min()
    h = top - bot
    valid = (ranging & (h >= r["min_h_atr"] * a) & (h <= r["max_h_atr"] * a)).fillna(False)
    return pd.DataFrame({"valid": valid, "top": top, "bot": bot, "atr": a}, index=daily.index)


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


def attach(days: dict, daily_by_symbol: dict, with_box: bool = False) -> dict:
    """Backtests: put each session's bias and level map on its intraday frame (df.attrs), computed from earlier daily bars only."""
    warm = BIAS_RULES["sma"] + 15
    for sym, frames in days.items():
        dly = daily_by_symbol.get(sym)
        if dly is None or not len(dly):
            continue
        b = bias(dly).to_numpy()
        wb = weekly_bias(dly).to_numpy()
        bx = range_box(dly) if with_box else None
        dates = np.array(dly.index.date)
        for f in frames:
            dd = f.index[0].date()
            k = int(np.searchsorted(dates, dd))           # daily bars strictly before the session
            f.attrs["bias"] = int(b[k - 1]) if k >= warm else 0
            f.attrs["wbias"] = int(wb[k - 1]) if k >= 160 else 0
            f.attrs["levels"] = level_map(dly.iloc[max(0, k - LEVEL_RULES["lookback"]):k]) if k else []
            f.attrs["stage"] = stage(dly.iloc[max(0, k - STAGE_RULES["ma"] - STAGE_RULES["slope_bars"] - 5):k]) if k else 0
            if bx is not None:
                f.attrs["box_atr"] = float(bx["atr"].iloc[k - 1]) if k >= warm + 10 else 0.0
                f.attrs["box"] = ((bool(bx["valid"].iloc[k - 1]), float(bx["top"].iloc[k - 1]), float(bx["bot"].iloc[k - 1])) if k >= warm + 10 else (False, 0.0, 0.0))
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


def weekly_bias(daily: pd.DataFrame) -> pd.Series:
    """+1 / -1 / 0 per DAILY date: the weekly trend as of the last COMPLETED week (EMA10 > EMA30 of weekly closes with the weekly close above
    EMA10; mirror for -1). A day's value never includes its own (incomplete) week; consumers still use the previous row, like bias()."""
    c = daily["close"].astype(float)
    w = c.resample("W-FRI").last().dropna()
    e1, e2 = ema(w, WEEKLY_RULES["fast"]), ema(w, WEEKLY_RULES["slow"])
    wd = pd.Series(np.where((e1 > e2) & (w > e1), 1, np.where((e1 < e2) & (w < e1), -1, 0)), index=w.index)
    wd[: WEEKLY_RULES["slow"]] = 0                               # not enough weeks yet
    return wd.reindex(c.index, method="ffill").fillna(0).astype(int)


def weekly_for_session(daily: pd.DataFrame, session_date) -> int:
    if daily is None or len(daily) < 160:
        return 0
    prior = daily[daily.index.date < session_date]
    return int(weekly_bias(prior).iloc[-1]) if len(prior) >= 160 else 0


FIBS = (0.236, 0.382, 0.5, 0.618, 0.786)
HYBRID_RULES = {"tol_atr": 0.25, "zone_atr": 0.25, "chase_atr": 0.5, "stop_atr": 0.5, "min_r1": 1.0, "min_r2": 0.5,
                "deep": 0.382}


def hybrid_plan(top: float, bot: float, atr: float, levels: list | None, side: int) -> dict | None:
    """The owner's HYBRID zones (2026-10-16): where the range box, its Fibonacci levels and the stock's / pair's own swing levels agree.
    Long: candidate supports are the box low, the 0.618 and 0.786 retracements and every swing level in the lower `deep` part of the box; each
    scores 1 + the number of OTHER candidates within tol_atr x ATR (+1 more for a strong swing level). The best-scored support is the ENTRY
    level L (ties: nearest the 0.618 level). Targets = the 0.5 / 0.382 / 0.236 levels, the box top and the swing levels above the deep part,
    sorted outward. Short = mirror. Returns {"entry", "score", "targets"} or None."""
    h = top - bot
    if not (h > 0 and atr > 0):
        return None
    r = HYBRID_RULES
    lv = [(float(x["price"]), bool(x.get("strong"))) for x in (levels or []) if bot - 0.5 * atr <= float(x["price"]) <= top + 0.5 * atr]
    if side == 1:
        fib_lo = [top - f * h for f in (0.618, 0.786)]
        sup = [(bot, False), *[(p, False) for p in fib_lo], *[(p, st) for p, st in lv if p <= bot + r["deep"] * h]]
        tg = sorted({top - f * h for f in (0.5, 0.382, 0.236)} | {top} | {p for p, _ in lv if p > bot + r["deep"] * h})
        anchor = top - 0.618 * h
    else:
        fib_hi = [bot + f * h for f in (0.618, 0.786)]
        sup = [(top, False), *[(p, False) for p in fib_hi], *[(p, st) for p, st in lv if p >= top - r["deep"] * h]]
        tg = sorted({bot + f * h for f in (0.5, 0.382, 0.236)} | {bot} | {p for p, _ in lv if p < top - r["deep"] * h}, reverse=True)
        anchor = bot + 0.618 * h
    tol = r["tol_atr"] * atr
    best = None
    for k, (p, strong) in enumerate(sup):
        score = 1 + sum(1 for j, (q, _) in enumerate(sup) if j != k and abs(q - p) <= tol) + (1 if strong else 0)
        key = (score, -abs(p - anchor))
        if best is None or key > best[0]:
            best = (key, p, score)
    return {"entry": best[1], "score": best[2], "targets": tg}


def hybrid_trade(plan: dict, bot: float, top: float, atr: float, side: int, close: float) -> dict | None:
    """Stop and the two take-profit lines for a trade entered at `close` from a hybrid plan: stop = 0.5 ATR past the entry level (the level
    the zones agree on is the "Sell ALL" line; past it the confluence has failed); TP1 = the first target at least 1R away, TP2 = the next
    target at least 0.5R beyond TP1 (0 = none). None when no target is far enough."""
    r = HYBRID_RULES
    L = plan["entry"]
    stop_px = L - r["stop_atr"] * atr if side == 1 else L + r["stop_atr"] * atr
    risk = side * (close - stop_px)
    if risk <= 0:
        return None
    t1 = next((t for t in plan["targets"] if side * (t - close) >= r["min_r1"] * risk), None)
    if t1 is None:
        return None
    t2 = next((t for t in plan["targets"] if side * (t - t1) >= r["min_r2"] * risk), None)
    return {"stop_dist": risk, "tp1": float(t1), "tp2": float(t2) if t2 is not None else 0.0}
