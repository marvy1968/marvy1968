"""Where does price usually reverse? (owner 2026-10-17: "deep search each indicator level when the price usually reverses").

On 5-minute regular-session bars, for each indicator the owner's design uses (RSI 14, stochastic %K 14/3, Bollinger %B 20/2, distance from the
20 MA and from VWAP in ATR units), two measurements:

1. AT TURNS: the indicator's value at real swing highs / lows. A swing high is the highest high of the 7 bars around it (3 each side) after
   which price falls at least `move_atr` ATR within `look` bars (same session) BEFORE making a new high; swing lows mirror it. Reported as the
   median and the middle half (25th-75th percentile), split by the 1-hour trend (with the trend / against it).
2. ODDS BY LEVEL: from EVERY bar whose indicator sits in a bucket, did price move 1 ATR back the other way before moving 1 ATR further
   (within `look` bars, same session)? A coin flip is ~50%; the buckets well above 50% are the levels where reversals really happen.

Hindsight is used only to LABEL turns (that is the question asked); the indicator values themselves are causal. Per-stock medians are saved so
a strategy can use each stock's own levels."""

import numpy as np
import pandas as pd

from .indicators import atr, bollinger, ema, rsi, session_vwap

INDICATORS = {
    "rsi14": ("RSI 14", [0, 20, 25, 30, 35, 40, 60, 65, 70, 75, 80, 101]),
    "stoch": ("stochastic %K 14/3", [0, 5, 10, 20, 30, 70, 80, 90, 95, 101]),
    "pctb": ("Bollinger %B (0 = lower band, 1 = upper band)", [-9, -0.25, 0, 0.1, 0.2, 0.8, 0.9, 1.0, 1.25, 9]),
    "ma20_atr": ("distance from the 20 MA, in ATR", [-99, -3, -2.5, -2, -1.5, -1, 1, 1.5, 2, 2.5, 3, 99]),
    "vwap_atr": ("distance from VWAP, in ATR", [-99, -4, -3, -2, -1.5, -1, 1, 1.5, 2, 3, 4, 99]),
}


def indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """5-minute bars in, the indicators + ATR + 1-hour trend (+1 / -1 / 0, last completed hour) out."""
    c = df["close"]
    a = atr(df, 14)
    lo14, hi14 = df["low"].rolling(14, min_periods=14).min(), df["high"].rolling(14, min_periods=14).max()
    k = (100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan)).rolling(3, min_periods=3).mean()
    bl, bm, bu = bollinger(c, 20, 2.0)
    f = pd.DataFrame({"rsi14": rsi(c, 14), "stoch": k, "pctb": (c - bl) / (bu - bl).replace(0, np.nan),
                      "ma20_atr": (c - bm) / a, "vwap_atr": (c - session_vwap(df)) / a, "atr": a}, index=df.index)
    r = df.resample("60min", label="left", closed="left", origin="start_day").agg({"close": "last"}).dropna()
    e9, e21 = ema(r["close"], 9), ema(r["close"], 21)
    d = pd.Series(np.where((e9 > e21) & (r["close"] > e21), 1, np.where((e9 < e21) & (r["close"] < e21), -1, 0)), index=r.index).shift(1)
    f["trend"] = d.reindex(df.index, method="ffill").fillna(0).astype(int)
    return f


def _outcomes(h, l, c, a, day, look: int, move: float):
    """Per bar: +1 if price rose `move` ATR first, -1 if it fell `move` ATR first, 0 if neither within `look` bars of the same session."""
    n = len(c)
    out = np.zeros(n, dtype=np.int8)
    for i in range(n):
        if not np.isfinite(a[i]) or a[i] <= 0:
            continue
        up, dn = c[i] + move * a[i], c[i] - move * a[i]
        for j in range(i + 1, min(n, i + 1 + look)):
            if day[j] != day[i]:
                break
            hu, hd = h[j] >= up, l[j] <= dn
            if hu and hd:
                break                                   # both in one bar: order unknown, not counted
            if hu:
                out[i] = 1
                break
            if hd:
                out[i] = -1
                break
    return out


def _turns(h, l, a, day, look: int, move: float, w: int = 3):
    """Swing highs (+1) / lows (-1) that were followed by a `move` ATR reversal before a new extreme."""
    n = len(h)
    out = np.zeros(n, dtype=np.int8)
    for i in range(w, n - 1):
        if not np.isfinite(a[i]) or a[i] <= 0 or day[i - w] != day[i]:
            continue
        lo_, hi_ = max(0, i - w), min(n, i + w + 1)
        same = day[lo_:hi_] == day[i]
        if h[i] >= h[lo_:hi_][same].max():
            for j in range(i + 1, min(n, i + 1 + look)):
                if day[j] != day[i] or h[j] > h[i]:
                    break
                if l[j] <= h[i] - move * a[i]:
                    out[i] = 1
                    break
        elif l[i] <= l[lo_:hi_][same].min():
            for j in range(i + 1, min(n, i + 1 + look)):
                if day[j] != day[i] or l[j] < l[i]:
                    break
                if h[j] >= l[i] + move * a[i]:
                    out[i] = -1
                    break
    return out


def study(bars5: dict[str, pd.DataFrame], start=None, end=None, look: int = 12, move_atr: float = 1.0) -> dict:
    """bars5: symbol -> 5-minute regular-session bars (continuous series). Returns tables + per-stock levels."""
    rows = []
    for sym, df in bars5.items():
        if len(df) < 200:
            continue
        f = indicator_frame(df)
        h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
        a = f["atr"].to_numpy(float)
        day = np.asarray(df.index.date)
        f["out"] = _outcomes(h, l, c, a, day, look, move_atr)
        f["turn"] = _turns(h, l, a, day, look, move_atr)
        f["sym"] = sym
        keep = np.ones(len(f), bool)
        if start is not None:
            keep &= day >= start
        if end is not None:
            keep &= day <= end
        rows.append(f[keep])
    if not rows:
        return {"family": "reversal_levels", "verdict": "no data"}
    allf = pd.concat(rows)
    res = {"family": "reversal_levels", "bars": int(len(allf)), "stocks": int(allf["sym"].nunique()),
           "turn_tops": int((allf["turn"] == 1).sum()), "turn_bottoms": int((allf["turn"] == -1).sum()), "at_turns": {}, "odds": {}, "per_stock": {}}
    q = lambda s: [round(float(x), 2) for x in s.quantile([0.25, 0.5, 0.75])] if len(s) else None
    for key, (name, edges) in INDICATORS.items():
        t = {}
        for label, mask in (("tops, uptrend", (allf["turn"] == 1) & (allf["trend"] == 1)), ("tops, downtrend", (allf["turn"] == 1) & (allf["trend"] == -1)),
                            ("tops, all", allf["turn"] == 1), ("bottoms, uptrend", (allf["turn"] == -1) & (allf["trend"] == 1)),
                            ("bottoms, downtrend", (allf["turn"] == -1) & (allf["trend"] == -1)), ("bottoms, all", allf["turn"] == -1)):
            s = allf.loc[mask, key].dropna()
            t[label] = {"n": int(len(s)), "p25_med_p75": q(s)}
        res["at_turns"][key] = t
        b = pd.cut(allf[key], edges)
        g = allf.assign(b=b).dropna(subset=["b"]).groupby("b", observed=True)["out"]
        odds = []
        for bucket, s in g:
            dec = s[s != 0]
            if len(dec) < 30:
                continue
            hi_side = bucket.left >= np.median(edges)            # high readings: a reversal is DOWN; low readings: UP
            rev = (dec == -1).mean() if hi_side else (dec == 1).mean()
            odds.append({"bucket": str(bucket), "side": "top" if hi_side else "bottom", "n": int(len(dec)), "reverse_pct": round(100 * float(rev), 1)})
        res["odds"][key] = odds
    for sym, g in allf.groupby("sym"):
        res["per_stock"][sym] = {key: {"top_med": (round(float(g.loc[g["turn"] == 1, key].median()), 2) if (g["turn"] == 1).any() else None),
                                       "bottom_med": (round(float(g.loc[g["turn"] == -1, key].median()), 2) if (g["turn"] == -1).any() else None)}
                                 for key in INDICATORS}
        res["per_stock"][sym]["turns"] = int((g["turn"] != 0).sum())
    res["verdict"] = "see the tables: AT TURNS = where reversals happened; ODDS = how often a level reverses (50% = coin flip)"
    return res


def report_lines(res: dict) -> list[str]:
    if "at_turns" not in res:
        return [f"- {res.get('verdict')}"]
    out = [f"- {res['bars']:,} five-minute bars, {res['stocks']} stocks, {res['turn_tops']:,} swing tops and {res['turn_bottoms']:,} swing bottoms "
           f"(a turn = a 1 ATR reversal within 1 hour)"]
    for key, (name, _) in INDICATORS.items():
        out.append(f"### {name}")
        for label, v in res["at_turns"][key].items():
            if v["p25_med_p75"]:
                p = v["p25_med_p75"]
                out.append(f"- at {label} ({v['n']}): median {p[1]}, middle half {p[0]} to {p[2]}")
        for o in res["odds"][key]:
            out.append(f"- odds {o['side']} {o['bucket']}: reversed first {o['reverse_pct']}% of {o['n']}")
    out.append("### per stock: median RSI / stochastic at tops and bottoms")
    for sym, v in sorted(res["per_stock"].items()):
        out.append(f"- {sym} ({v['turns']} turns): RSI top {v['rsi14']['top_med']} bottom {v['rsi14']['bottom_med']} | "
                   f"stoch top {v['stoch']['top_med']} bottom {v['stoch']['bottom_med']} | %B top {v['pctb']['top_med']} bottom {v['pctb']['bottom_med']}")
    return out
