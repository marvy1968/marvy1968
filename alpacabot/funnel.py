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
         "stage_ma": 150, "stage_slope": 20,
         # owner 2026-10-17 ("S/R and RSI determine buy zones"): two more votes, then `need6` of 6 must agree
         "stop_frac": 1.0,                       # research: the stop at this share of the band distance (take-profit stays tp_r x the FULL distance)
         "sr_rsi": False, "need6": 4, "sr_atr": 0.5, "sr_lookback": 120, "sr_wing": 2, "sr_min_touches": 2,
         "rsi_long": (40.0, 55.0), "rsi_short": (45.0, 60.0),
         # owner 2026-10-19 (majority oversold confluence, longs only): RSI14 < 30, stochastic %K < 20, close at / under the lower Bollinger
         # band, and a 4th price-stretch vote ("sd" = 1.5 SD under the 20-day average, "vwap" = 1 ATR under the 20-day VWAP); `os_need` of 4
         # plus volume; `os_stage` = stages allowed (None = any stage)
         "oversold": False, "os_need": 3, "os_fourth": "sd", "os_stage": (2,), "os_vol": 1.1,
         "os_rsi": 30.0, "os_stoch": 20.0,
         # owner 2026-10-22 Velez hybrid on the daily swing longs: env = the 20-day SMA rising over the 200-day; look_left = no prior 20-day high
         # between the entry and the target; no_chase = close within 2 ATR of the 20-day SMA; tail = a bottoming tail on the signal day;
         # bars35 = 3+ lower closes in the 5 days before (his 3-5 bar dip)
         "v_env": False, "v_look_left": False, "v_no_chase": False, "v_tail": False, "v_bars35": False,
         # owner 2026-10-22 Pristine hybrid (fxbot.strategies.PristineFx rules on daily stock bars, longs): pristine = a setup kind ("pbs", "cbs",
         # "cog", "pbo") REPLACES the entry (its own one-bar stop distance and trail exit pr_trail); pr_confirm = a kind that must also have fired
         # on the signal day or the day before (confirmation for the funnel / oversold entries)
         "pristine": None, "pr_trail": "stop", "pr_confirm": None,
         # owner 2026-10-22 redesign: a Velez-style swing trader of its own (longs, daily bars) REPLACING the entry: trend = the 20-day SMA
         # rising over a rising 200-day; signal day = a pullback that tags the 20 (low within vz_touch ATR of it, close holding near it) with a
         # Velez bar: bottoming tail, red bar ignored, narrow range bar or a 3-5 bar dip; the NEXT day confirms (closes up, above the signal
         # day's high, volume >= vz_vol x average); look left (at least 1R of room before the prior 20-day high); not extended (within 2 ATR of
         # the 20). Stop under the two-day low less 0.25 ATR (1-3 ATR); take-profit tp_r; out on vz_exit_days closes under the 20.
         "velez_swing": False, "vz_touch": 0.5, "vz_vol": 1.0, "vz_exit_days": 2}


def sr_votes(d: pd.DataFrame, a: pd.Series, lookback: int = 120, wing: int = 2, tol_atr: float = 0.5, min_touches: int = 2) -> tuple:
    """(at_support, at_resistance) per day, causal: swing highs / lows (the extreme of 2*wing+1 bars) known by that day (a pivot needs `wing`
    later bars), over the last `lookback` days, merged within tol_atr ATR into zones; a zone touched >= min_touches times is strong (the
    zone map's rule). At support = the day's low reached a strong zone (within tol_atr ATR) and the close held above it; resistance mirrors."""
    hi, lo, c = (d[k].to_numpy(float) for k in ("high", "low", "close"))
    av = a.to_numpy(float)
    n = len(c)
    piv = []                                                     # (bar index, price)
    sup, res = np.zeros(n, bool), np.zeros(n, bool)
    for i in range(n):
        j = i - wing                                              # the newest bar that can now be confirmed as a pivot
        if j >= wing:
            if hi[j] == hi[j - wing:j + wing + 1].max():
                piv.append((j, hi[j]))
            if lo[j] == lo[j - wing:j + wing + 1].min():
                piv.append((j, lo[j]))
        if not np.isfinite(av[i]) or av[i] <= 0:
            continue
        tol = tol_atr * av[i]
        pts = sorted(p for k, p in piv if k > i - lookback)
        zones, cur = [], []
        for p in pts:
            if cur and p - cur[-1] > tol:
                zones.append(cur)
                cur = []
            cur.append(p)
        if cur:
            zones.append(cur)
        for z in zones:
            if len(z) < min_touches:
                continue
            L = float(np.mean(z))
            if lo[i] <= L + tol and c[i] >= L - 0.1 * av[i] and c[i] <= L + 2 * tol:
                sup[i] = True
            if hi[i] >= L - tol and c[i] <= L + 0.1 * av[i] and c[i] >= L - 2 * tol:
                res[i] = True
    return pd.Series(sup, index=d.index), pd.Series(res, index=d.index)


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
    if r["sr_rsi"]:
        from .indicators import rsi as _rsi
        rs14 = _rsi(c, 14)
        at_sup, at_res = sr_votes(d, a, r["sr_lookback"], r["sr_wing"], r["sr_atr"], r["sr_min_touches"])
    out = pd.DataFrame(index=d.index)
    out["stage"], out["atr"], out["close"] = stage, a, c
    for side in (1, -1):
        near = ((c >= ma) & (c <= ma + r["near_atr"] * a)) if side == 1 else ((c <= ma) & (c >= ma - r["near_atr"] * a))
        vwap_ok = (c >= vw) if side == 1 else (c <= vw)
        cross = ((k > dd) & (k.shift(1) <= dd.shift(1))) if side == 1 else ((k < dd) & (k.shift(1) >= dd.shift(1)))
        votes = near.astype(int) + vwap_ok.astype(int) + vol_ok.astype(int) + cross.astype(int)
        need = r["need"]
        if r["sr_rsi"]:                                                    # 5: at strong support (resistance); 6: RSI pulled back and turning
            lo_, hi_ = r["rsi_long"] if side == 1 else r["rsi_short"]
            rsi_ok = (rs14 >= lo_) & (rs14 <= hi_) & ((rs14 > rs14.shift(1)) if side == 1 else (rs14 < rs14.shift(1)))
            votes = votes + (at_sup if side == 1 else at_res).astype(int) + rsi_ok.astype(int)
            need = r["need6"]
        want = 2 if side == 1 else 4
        band = lo if side == 1 else up
        dist = (side * (c - band)).clip(lower=r["stop_min_atr"] * a, upper=r["stop_max_atr"] * a)
        tag = "long" if side == 1 else "short"
        out[f"{tag}_entry"] = (stage == want) & (votes >= need) & a.notna() & (a > 0) & ma.notna()
        if r["oversold"] and side == 1:
            from .indicators import rsi as _rsi_os
            rr = _rsi_os(c, 14)
            raw_k = 100 * (c - lo14) / (hi14 - lo14).replace(0, np.nan)
            fourth = (c <= ma - 1.5 * sd) if r["os_fourth"] == "sd" else (c <= vw - a)
            ov = ((rr < r["os_rsi"]).astype(int) + (raw_k < r["os_stoch"]).astype(int) + (c <= lo).astype(int) + fourth.astype(int))
            ok = (ov >= r["os_need"]) & (v >= r["os_vol"] * vavg) & a.notna() & (a > 0) & ma.notna()
            if r["os_stage"] is not None:
                ok &= stage.isin(list(r["os_stage"]))
            out[f"{tag}_entry"] = ok
            out[f"{tag}_exit"] = c > ma                                    # mean reversion done: back over the 20-day average
        out[f"{tag}_stop"] = c - side * r["stop_frac"] * dist
        out[f"{tag}_tp"] = c + side * r["tp_r"] * dist
        under = (c < ma) if side == 1 else (c > ma)
        three = under & under.shift(1, fill_value=False) & under.shift(2, fill_value=False)
        lost_vwap = (c < vw) if side == 1 else (c > vw)
        turn = ((k < dd) & (k.shift(1) >= r["stoch_top"])) if side == 1 else ((k > dd) & (k.shift(1) <= 100 - r["stoch_top"]))
        out[f"{tag}_exit"] = ((three.astype(int) + lost_vwap.astype(int) + turn.astype(int)) >= 2) | (stage != want)
    if r["pristine"] or r["pr_confirm"]:
        from fxbot.strategies import PristineFx
        if r["pristine"]:
            pf = PristineFx(r["pristine"], r["pr_trail"], cbs_atr=2.5).frame(d)
            dist = pf["stop"].where(pf["long"])
            out["long_entry"] = pf["long"] & a.notna() & (a > 0)
            out["long_stop"] = c - r["stop_frac"] * dist
            out["long_tp"] = c + r["tp_r"] * dist
            out["long_exit"] = pf["exit_long"] if r["pr_trail"] != "stop" else pd.Series(False, index=d.index)
        if r["pr_confirm"]:
            pc = PristineFx(r["pr_confirm"], "stop", cbs_atr=2.5).frame(d)["long"]
            out["long_entry"] = out["long_entry"] & (pc | pc.shift(1, fill_value=False))
    if r["velez_swing"]:
        o_ = d["open"].astype(float)
        s20, s200 = c.rolling(20, min_periods=20).mean(), c.rolling(200, min_periods=200).mean()
        env = (s20 > s200) & (s20 > s20.shift(3)) & (s200 > s200.shift(10)) & (c > s200)
        rng = (h - l).replace(0, np.nan)
        tail = (np.minimum(o_, c) - l) >= 0.4 * rng
        rbi = (c.shift(1) < o_.shift(1)) & (c > h.shift(1))
        nrb = (h - l) <= 0.6 * (h - l).rolling(10, min_periods=10).mean().shift(1)
        dip = (c < c.shift(1)).astype(int).shift(1).rolling(5, min_periods=5).sum() >= 3
        touch = (l <= s20 + r["vz_touch"] * a) & (c >= s20 - 0.25 * a)
        setup = env & touch & (tail | rbi | nrb | dip)
        prev = setup.shift(1, fill_value=False)
        conf = prev & (c > o_) & (c > h.shift(1)) & (v >= r["vz_vol"] * vavg) & ((c - s20).abs() <= 2.0 * a)
        low2 = np.minimum(l, l.shift(1))
        vdist = (c - (low2 - 0.25 * a)).clip(lower=r["stop_min_atr"] * a, upper=r["stop_max_atr"] * a)
        hi20 = h.rolling(20, min_periods=20).max().shift(1)
        clear = ~((hi20 > c) & (hi20 < c + vdist))             # look left: at least 1R of room before the prior 20-day high
        out["long_entry"] = (conf & clear & a.notna() & (a > 0)).fillna(False)
        out["long_stop"] = c - r["stop_frac"] * vdist
        out["long_tp"] = c + r["tp_r"] * vdist
        under = c < s20
        ex = under
        for k_ in range(1, int(r["vz_exit_days"])):
            ex = ex & under.shift(k_, fill_value=False)
        out["long_exit"] = ex
        out["short_entry"] = False
    if r["v_env"] or r["v_look_left"] or r["v_no_chase"] or r["v_tail"] or r["v_bars35"]:
        sma20, sma200 = c.rolling(20, min_periods=20).mean(), c.rolling(200, min_periods=200).mean()
        vok = pd.Series(True, index=d.index)
        if r["v_env"]:
            vok &= (sma20 > sma200) & (sma20 > sma20.shift(3))
        if r["v_no_chase"]:
            vok &= (c - sma20).abs() <= 2.0 * a
        if r["v_tail"]:
            o_ = d["open"].astype(float)
            rng = (h - l).replace(0, np.nan)
            vok &= (np.minimum(o_, c) - l) >= 0.4 * rng
        if r["v_bars35"]:
            dn = (c < c.shift(1)).astype(int)
            vok &= dn.shift(1).rolling(5, min_periods=5).sum() >= 3
        if r["v_look_left"]:
            hi20 = h.rolling(20, min_periods=20).max().shift(1)
            vok &= ~((hi20 > c) & (hi20 < out["long_tp"]))
        out["long_entry"] = out["long_entry"] & vok.fillna(False)
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
             short_exit_vote: bool = True, no_stop: bool = False, entry_at: str | None = None, minute: dict | None = None) -> dict:
    """Signals on days start..end: enter at the next open (a gap through the stop or past the take-profit is skipped, like the live runner),
    stop first then take-profit inside a day (conservative), the sell vote at a close exits at the next open. One position per stock at a
    time. Result per trade in R; return = sum(R) x risk_pct (no portfolio cap, so it overstates exposure on busy days).
    entry_at = "HH:MM" (owner 2026-10-22, "no new entries before 10:00"): enter at that minute's open on the entry day instead of the 09:30 open,
    from `minute` = {symbol: 1-minute bars}; skipped when price already went through the stop or the take-profit before then (the live
    runner skips a trade whose stop is already gone), and only the rest of that day counts for the stop / take-profit. A stock or day without
    minute bars is skipped."""
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
            o1, h1, l1 = o[i + 1], h[i + 1], l[i + 1]                  # the entry day as the trade sees it
            if entry_at:
                mb = (minute or {}).get(sym)
                if mb is None or not len(mb):
                    i += 1
                    continue
                day_ = mb[mb.index.date == dates[i + 1]]
                t_ = day_.index.strftime("%H:%M")
                pre, post = day_[(t_ >= "09:30") & (t_ < entry_at)], day_[t_ >= entry_at]
                if not len(post) or not len(pre):
                    i += 1
                    continue
                if (side == 1 and (pre["low"].min() <= stop or pre["high"].max() >= tp)) or \
                        (side == -1 and (pre["high"].max() >= stop or pre["low"].min() <= tp)):
                    i += 1                                       # gone through the stop (or the target) before the entry time
                    continue
                o1, h1, l1 = float(post["open"].iloc[0]), float(post["high"].max()), float(post["low"].min())
            fill = o1 * (1 + side * slip)
            risk = side * (fill - stop)
            if risk <= 0 or side * (tp - fill) <= 0:
                i += 1
                continue
            j, px, why = i + 1, None, ""
            while j < n:
                oj, hj, lj = (o1, h1, l1) if j == i + 1 else (o[j], h[j], l[j])
                if not no_stop and ((side == 1 and lj <= stop) or (side == -1 and hj >= stop)):
                    px, why = (min(stop, oj) if side == 1 else max(stop, oj)), "stop"
                elif (side == 1 and hj >= tp) or (side == -1 and lj <= tp):
                    px, why = (max(tp, oj) if side == 1 else min(tp, oj)), "tp"
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
