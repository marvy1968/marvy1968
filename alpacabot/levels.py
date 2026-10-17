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


TIMEFRAMES = (("15m", "15min"), ("1h", "60min"), ("1d", "1D"))
HTF_KEYS = [f"{k}_{t}" for t in ("15m", "1h") for k in ("rsi14", "stoch", "pctb", "ma20_atr")]


def indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """5-minute bars in, the indicators + ATR + 1-hour trend (+1 / -1 / 0, last completed hour) out."""
    c = df["close"]
    a = atr(df, 14)
    lo14, hi14 = df["low"].rolling(14, min_periods=14).min(), df["high"].rolling(14, min_periods=14).max()
    k = (100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan)).rolling(3, min_periods=3).mean()
    bl, bm, bu = bollinger(c, 20, 2.0)
    f = pd.DataFrame({"rsi14": rsi(c, 14), "stoch": k, "pctb": (c - bl) / (bu - bl).replace(0, np.nan),
                      "ma20_atr": (c - bm) / a, "vwap_atr": (c - session_vwap(df)) / a, "atr": a}, index=df.index)
    # owner 2026-10-17 "use multi time frames for trends and TP": the same indicators and the trend direction on the last COMPLETED 15-minute,
    # 1-hour and daily bars (no look-ahead), plus how many of the three agree
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    dirs = []
    for tag, rule in TIMEFRAMES:
        r = df.resample(rule, label="left", closed="left", origin="start_day").agg(agg).dropna()
        rc = r["close"]
        e9, e21 = ema(rc, 9), ema(rc, 21)
        d = pd.Series(np.where((e9 > e21) & (rc > e21), 1, np.where((e9 < e21) & (rc < e21), -1, 0)), index=r.index).shift(1)
        f[f"dir_{tag}"] = d.reindex(df.index, method="ffill").fillna(0).astype(int)
        dirs.append(f[f"dir_{tag}"])
        if tag != "1d":
            ra = atr(r, 14)
            rl, rh = r["low"].rolling(14, min_periods=14).min(), r["high"].rolling(14, min_periods=14).max()
            rk = (100 * (rc - rl) / (rh - rl).replace(0, np.nan)).rolling(3, min_periods=3).mean()
            b_l, b_m, b_u = bollinger(rc, 20, 2.0)
            hf = pd.DataFrame({f"rsi14_{tag}": rsi(rc, 14), f"stoch_{tag}": rk, f"pctb_{tag}": (rc - b_l) / (b_u - b_l).replace(0, np.nan),
                               f"ma20_atr_{tag}": (rc - b_m) / ra}, index=r.index).shift(1)
            f = f.join(hf.reindex(df.index, method="ffill"))
    f["trend"] = f["dir_1h"]
    f["align"] = sum(dirs).astype(int)                 # +3 = 15 min, 1 hour and daily all up; -3 = all down
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


def _runs(h, l, a, day, turn):
    """From each turn, how far price ran the reversal's way (in ATR at the turn) before the next opposite turn or the session's end."""
    n = len(h)
    out = np.full(n, np.nan)
    for i in np.nonzero(turn)[0]:
        if not np.isfinite(a[i]) or a[i] <= 0:
            continue
        best = 0.0
        for j in range(i + 1, n):
            if day[j] != day[i] or turn[j] == -turn[i]:
                break
            best = max(best, h[i] - l[j] if turn[i] == 1 else h[j] - l[i])
        out[i] = best / a[i]
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
        f["run_atr"] = _runs(h, l, a, day, f["turn"].to_numpy())
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
    up3, dn3 = allf["align"] == 3, allf["align"] == -3
    groups = (("UPTREND on all 3 (15m/1h/day): pullback lows = BUY zone", up3 & (allf["turn"] == -1)),
              ("UPTREND on all 3: swing highs = TP zone", up3 & (allf["turn"] == 1)),
              ("DOWNTREND on all 3: bounce highs = SHORT zone", dn3 & (allf["turn"] == 1)),
              ("DOWNTREND on all 3: swing lows = short TP zone", dn3 & (allf["turn"] == -1)),
              ("MIXED trends: highs", (allf["align"].abs() < 3) & (allf["turn"] == 1)),
              ("MIXED trends: lows", (allf["align"].abs() < 3) & (allf["turn"] == -1)))
    res["mtf"] = {}
    for label, mask in groups:
        g = allf.loc[mask]
        res["mtf"][label] = {"n": int(len(g)), "run_atr": q(g["run_atr"].dropna()),
                             **{k: q(g[k].dropna()) for k in list(INDICATORS) + HTF_KEYS if k in g}}
    res["htf_odds"] = {}
    for key in HTF_KEYS:
        base = key.rsplit("_", 1)[0]
        edges = INDICATORS[base][1]
        for label, mask in (("all 3 up", up3), ("all 3 down", dn3)):
            sub = allf.loc[mask]
            b = pd.cut(sub[key], edges)
            odds = []
            for bucket, s_ in sub.assign(b=b).dropna(subset=["b"]).groupby("b", observed=True)["out"]:
                dec = s_[s_ != 0]
                if len(dec) < 30:
                    continue
                hi_side = bucket.left >= np.median(edges)
                rev = (dec == -1).mean() if hi_side else (dec == 1).mean()
                odds.append({"bucket": str(bucket), "side": "top" if hi_side else "bottom", "n": int(len(dec)), "reverse_pct": round(100 * float(rev), 1)})
            res["htf_odds"][f"{key} | {label}"] = odds
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
    out.append("### MULTI-TIMEFRAME: turns grouped by the 15-minute / 1-hour / daily trend (median, middle half)")
    for label, v in res.get("mtf", {}).items():
        out.append(f"#### {label} ({v['n']} turns)")
        if v.get("run_atr"):
            r = v["run_atr"]
            out.append(f"- move after the turn (TP distance): median {r[1]} ATR, middle half {r[0]} to {r[2]} ATR (5-minute ATR)")
        for k in list(INDICATORS) + HTF_KEYS:
            if v.get(k):
                p = v[k]
                out.append(f"- {k}: median {p[1]}, middle half {p[0]} to {p[2]}")
    out.append("### MULTI-TIMEFRAME odds (15-minute and 1-hour indicators, only when all 3 trends agree)")
    for k, odds in res.get("htf_odds", {}).items():
        for o in odds:
            out.append(f"- {k}: {o['side']} {o['bucket']}: reversed first {o['reverse_pct']}% of {o['n']}")
    out.append("### per stock: median RSI / stochastic at tops and bottoms")
    for sym, v in sorted(res["per_stock"].items()):
        out.append(f"- {sym} ({v['turns']} turns): RSI top {v['rsi14']['top_med']} bottom {v['rsi14']['bottom_med']} | "
                   f"stoch top {v['stoch']['top_med']} bottom {v['stoch']['bottom_med']} | %B top {v['pctb']['top_med']} bottom {v['pctb']['bottom_med']}")
    return out
