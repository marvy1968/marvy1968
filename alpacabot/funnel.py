"""The owner's "Stock Funnel" as a daily swing strategy (pasted script 2026-10-17, rebuilt with its bugs fixed; owner: "add it to the swing
bot as its own strategy with a 3-month check, the required bug fixes, and 0.5% risk").

On COMPLETED daily bars (the runner scans after the close; the pasted script read today's unfinished bar and, without a start date, got only
one bar so its 20-day averages were blank and it could never trade):

TREND     Weinstein stage 2 (close above a rising 150-day average) for longs, stage 4 for shorts.
BUY       at least `need` of 4 agree (the owner's 2-of-3 / 3-of-4 rule):
            1. at the 20-day MA, not extended: MA20 <= close <= MA20 + 1 ATR (the script bought anything above the MA = chasing; the 58-stock
               reversal study put real buy-zone lows at the 20 MA),
            2. close above the 20-day volume-weighted average (the script's "VWAP proxy"),
            3. volume >= 1.2 x the prior 20 days' average,
            4. the daily stochastic (14,3) crosses up.
STOP      the lower Bollinger band (20, 2), kept 1-3 ATR below the close; the stop PRICE is derived from that distance, so a band above the price
          can no longer send a stop on the wrong side (the script's fallback changed the size but still sent the bad stop).
TAKE PROFIT  1.5R, held at the broker with the stop (one-cancels-other).
SELL      at the next open when 2 of 3 agree: 3 closes in a row under the MA20, a close under the 20-day VWAP, the stochastic turning down from
          75+; or the stage is no longer 2.
SHORTS    the mirror image (stage 4, upper band stop, ...); the runner checks the stock is easy to borrow and that no other bot holds it.
"""

import numpy as np
import pandas as pd

from .indicators import atr

RULES = {"need": 3, "near_atr": 1.0, "vol_mult": 1.2, "stop_min_atr": 1.0, "stop_max_atr": 3.0, "tp_r": 1.5, "stoch_top": 75.0,
         "stage_ma": 150, "stage_slope": 20}


def frame(d: pd.DataFrame, rules: dict | None = None) -> pd.DataFrame:
    """Daily bars in (index = dates, open/high/low/close/volume), signal columns out (every value uses that day's close or earlier)."""
    r = {**RULES, **(rules or {})}
    c, h, l, v = (d[k].astype(float) for k in ("close", "high", "low", "volume"))
    ma, sd = c.rolling(20, min_periods=20).mean(), c.rolling(20, min_periods=20).std()
    up, lo = ma + 2 * sd, ma - 2 * sd
    a = atr(d, 14)
    tp = (h + l + c) / 3
    vw = (tp * v).rolling(20, min_periods=20).sum() / v.rolling(20, min_periods=20).sum()
    vavg = v.rolling(20, min_periods=20).mean().shift(1)                 # the PRIOR 20 days
    lo14, hi14 = l.rolling(14, min_periods=14).min(), h.rolling(14, min_periods=14).max()
    k = (100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan)).rolling(3, min_periods=3).mean()
    dd = k.rolling(3, min_periods=3).mean()
    m150 = c.rolling(r["stage_ma"], min_periods=r["stage_ma"]).mean()
    rising = m150 > m150.shift(r["stage_slope"])
    stage = pd.Series(np.where(m150.isna(), 0, np.where(c > m150, np.where(rising, 2, 3), np.where(rising, 1, 4))), index=d.index)
    vol_ok = v >= r["vol_mult"] * vavg
    out = pd.DataFrame(index=d.index)
    out["stage"], out["atr"], out["close"] = stage, a, c
    for side in (1, -1):
        near = ((c >= ma) & (c <= ma + r["near_atr"] * a)) if side == 1 else ((c <= ma) & (c >= ma - r["near_atr"] * a))
        vwap_ok = (c >= vw) if side == 1 else (c <= vw)
        cross = ((k > dd) & (k.shift(1) <= dd.shift(1))) if side == 1 else ((k < dd) & (k.shift(1) >= dd.shift(1)))
        votes = near.astype(int) + vwap_ok.astype(int) + vol_ok.astype(int) + cross.astype(int)
        want = 2 if side == 1 else 4
        band = lo if side == 1 else up
        dist = (side * (c - band)).clip(lower=r["stop_min_atr"] * a, upper=r["stop_max_atr"] * a)
        tag = "long" if side == 1 else "short"
        out[f"{tag}_entry"] = (stage == want) & (votes >= r["need"]) & a.notna() & (a > 0) & ma.notna()
        out[f"{tag}_stop"] = c - side * dist
        out[f"{tag}_tp"] = c + side * r["tp_r"] * dist
        under = (c < ma) if side == 1 else (c > ma)
        three = under & under.shift(1, fill_value=False) & under.shift(2, fill_value=False)
        lost_vwap = (c < vw) if side == 1 else (c > vw)
        turn = ((k < dd) & (k.shift(1) >= r["stoch_top"])) if side == 1 else ((k > dd) & (k.shift(1) <= 100 - r["stoch_top"]))
        out[f"{tag}_exit"] = ((three.astype(int) + lost_vwap.astype(int) + turn.astype(int)) >= 2) | (stage != want)
    return out


def trend_strength_down(d: pd.DataFrame, spy: pd.DataFrame, adx_min: float = 25.0, rs_days: int = 63) -> pd.DataFrame:
    """The owner's "trend strength indicators strongly negative" for shorts, per day (completed bars): adx_dn = ADX(14) >= adx_min with -DI
    above +DI (a strong trend, pointing down); ma_dn = the 150-day average lower than 20 days ago; rs_dn = the stock's `rs_days` return below
    SPY's. Columns: those three, votes (0-3), rs (stock minus SPY return, for ranking the weakest per sector)."""
    h, l, c = (d[k].astype(float) for k in ("high", "low", "close"))
    up, dn = h.diff(), -l.diff()
    pdm = ((up > dn) & (up > 0)) * up
    mdm = ((dn > up) & (dn > 0)) * dn
    a = atr(d, 14)
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / a
    mdi = 100 * mdm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx_ = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    m150 = c.rolling(150, min_periods=150).mean()
    sc = spy["close"].astype(float)
    sc_by_day = dict(zip(sc.index.date, sc.to_numpy()))
    spy_al = pd.Series([sc_by_day.get(x, np.nan) for x in d.index.date], index=d.index).ffill()
    rs = c.pct_change(rs_days) - spy_al.pct_change(rs_days)
    out = pd.DataFrame({"adx_dn": (adx_ >= adx_min) & (mdi > pdi), "ma_dn": m150 < m150.shift(20), "rs_dn": rs < 0, "rs": rs}, index=d.index)
    out["votes"] = out[["adx_dn", "ma_dn", "rs_dn"]].astype(int).sum(axis=1)
    return out


def weakest_per_sector(ts: dict, sector_of: dict, n: int = 3) -> dict:
    """{symbol: bool Series}: True on days the stock is among the `n` weakest (lowest rs) of its sector."""
    frames = {}
    for etf in set(sector_of.values()):
        syms = [s for s in ts if sector_of.get(s) == etf]
        if not syms:
            continue
        m = pd.DataFrame({s: pd.Series(ts[s]["rs"].to_numpy(), index=ts[s].index.date) for s in syms})
        rank = m.rank(axis=1, method="first")
        for s in syms:
            ok = (rank[s] <= n) & m[s].notna()
            frames[s] = pd.Series(ok.to_numpy(bool), index=pd.DatetimeIndex(pd.to_datetime(ok.index)))
    return frames


def market_down(spy: pd.DataFrame) -> pd.Series:
    """True on days the market itself is falling: SPY closes under its 50-day average and that average is lower than 10 days ago."""
    c = spy["close"].astype(float)
    m = c.rolling(50, min_periods=50).mean()
    return (c < m) & (m < m.shift(10))


def backtest(bars: dict[str, pd.DataFrame], start, end, risk_pct: float = 0.005, slip_bps: float = 5.0, rules: dict | None = None,
             sides: tuple = ("long", "short"), short_ok=None, short_rules: dict | None = None,
             short_exit_vote: bool = True) -> dict:
    """Signals on days start..end: enter at the next open (a gap through the stop or past the take-profit is skipped, like the live runner),
    stop first then take-profit inside a day (conservative), the sell vote at a close exits at the next open. One position per stock at a
    time. Result per trade in R; return = sum(R) x risk_pct (no portfolio cap, so it overstates exposure on busy days)."""
    slip = slip_bps / 1e4
    trades = []
    for sym, d in bars.items():
        if len(d) < 200:
            continue
        f = frame(d, rules)
        if short_rules:                                         # a different rule set for the short side only
            fs = frame(d, {**(rules or {}), **short_rules})
            for col in ("short_entry", "short_stop", "short_tp", "short_exit"):
                f[col] = fs[col]
        if "long" not in sides:
            f["long_entry"] = False
        if "short" not in sides:
            f["short_entry"] = False
        elif short_ok is not None:                              # shorts only on days the filter allows (one series, or one per stock)
            ok = short_ok.get(sym) if isinstance(short_ok, dict) else short_ok
            if ok is None:
                ok = pd.Series(False, index=d.index)
            ok_by_day = dict(zip(ok.index.date, ok.to_numpy(bool)))
            f["short_entry"] = f["short_entry"] & np.array([ok_by_day.get(x, False) for x in f.index.date], dtype=bool)
        if not short_exit_vote:
            f["short_exit"] = False
        o, h, l, c = (d[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        dates = np.asarray(d.index.date)
        i, n = 0, len(d)
        while i < n - 1:
            if not (start <= dates[i] <= end):
                i += 1
                continue
            side = 1 if bool(f["long_entry"].iloc[i]) else -1 if bool(f["short_entry"].iloc[i]) else 0
            if side == 0:
                i += 1
                continue
            tag = "long" if side == 1 else "short"
            stop, tp = float(f[f"{tag}_stop"].iloc[i]), float(f[f"{tag}_tp"].iloc[i])
            fill = o[i + 1] * (1 + side * slip)
            risk = side * (fill - stop)
            if risk <= 0 or side * (tp - fill) <= 0:
                i += 1
                continue
            j, px, why = i + 1, None, ""
            while j < n:
                if (side == 1 and l[j] <= stop) or (side == -1 and h[j] >= stop):
                    px, why = (min(stop, o[j]) if side == 1 else max(stop, o[j])), "stop"
                elif (side == 1 and h[j] >= tp) or (side == -1 and l[j] <= tp):
                    px, why = (max(tp, o[j]) if side == 1 else min(tp, o[j])), "tp"
                elif bool(f[f"{tag}_exit"].iloc[j]) and j + 1 < n:
                    j, px, why = j + 1, o[j + 1], "signal"
                elif j == n - 1:
                    px, why = c[j], "open"
                if px is not None:
                    break
                j += 1
            px = px * (1 - side * slip)
            trades.append({"symbol": sym, "side": tag, "entry_date": str(dates[i + 1]), "exit_date": str(dates[min(j, n - 1)]),
                           "r": round(side * (px - fill) / risk, 3), "reason": why})
            i = j + 1
    rs = [t["r"] for t in trades]
    gw, gl = sum(x for x in rs if x > 0), -sum(x for x in rs if x <= 0)
    by = lambda s: [t["r"] for t in trades if t["side"] == s]                      # noqa: E731
    return {"trades": len(rs), "win_rate": round(np.mean([x > 0 for x in rs]), 3) if rs else None,
            "profit_factor": round(gw / gl, 2) if gl > 0 else (None if not rs else float("inf")),
            "avg_r": round(float(np.mean(rs)), 3) if rs else None, "return_pct": round(100 * risk_pct * sum(rs), 2),
            "longs": len(by("long")), "longs_r": round(sum(by("long")), 2), "shorts": len(by("short")), "shorts_r": round(sum(by("short")), 2),
            "exits": {k: sum(1 for t in trades if t["reason"] == k) for k in ("stop", "tp", "signal", "open")}, "list": trades}
