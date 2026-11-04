"""Velez-Confirmed day-trading bot (owner 2026-11-02: README "Velez-Confirmed Day Trading Bot for Alpaca"; "analyze, install and backtest.
Run tomorrow paper no matter the results"). PAPER. Backtest (velez_bt family) + live sleeve (VelezLive, paper minute loop).

The README's rules, turned into code with these choices fixed BEFORE any run:
UNIVERSE  the bot's 55 stocks (universe.STOCKS). Scanner at 09:35: price >= $5, 20-day average volume >= 1M shares, |gap| open vs prior close
          1-10%, relative volume of the first 5 minutes vs the prior 14 sessions > 1.2 -> watchlist for the day (top 10 by RVOL).
BARS      2-minute primary (aligned to 09:30), 5-minute confirmation, 1-minute micro; indicators run continuously across days.
CORE GATE (long only; shorts are off in the README) trend bias: close > SMA20, SMA20 rising (vs 5 bars ago), SMA20 > SMA200; wide state:
          ATR14 above its 20-bar average and |SMA20 slope over 5 bars| >= 0.1 ATR; stand aside if price crossed SMA20 4+ times in 20 bars
          or the slope is flat (< 0.1 ATR). Trigger: pullback (a low within SMA20 + 0.1 ATR in the last 5 bars) then a bullish bar closing
          above SMA20 on higher volume than the bar before; or a 2-bar reversal (bearish bar making a lower low, then a bullish bar closing
          above its high); or a flag breakout (prior 5 bars' range < 1 ATR, close above their high).
VOTES     need 3 of 5: (1) momentum: RSI14 55-75, volume > 1.5x the 20-bar average, 5-min close > 5-min SMA20 and that SMA rising;
          (2) breakout: close above the prior 20-bar high, the 15-minute opening-range high or the flag high, volume > 1.2x average;
          (3) VWAP: close above VWAP and either a low within 0.2% of VWAP in the last 3 bars or 5 closes above VWAP; (4) micro: 1-min EMA9 >
          EMA20 and the last 1-min candle bullish; (5) guard passes. The guard VETOES the trade if RSI > 80, close > SMA20 + 3 ATR or the bar
          is a bearish engulfing.
NO TRADE  close below VWAP; first 5 minutes; after 15:30; daily loss cap; max trades.
EXHAUSTION mode (half size): close < SMA20 - 3 ATR, RSI < 20, volume > 3x average, bullish engulfing -> long, stop under the bar low - 0.1 ATR.
STOP      the nearer of the 5-bar pullback low and SMA20 - 0.5 ATR, at least max(0.5 ATR, 0.15% of price) from entry.
EXITS     half at +2R and the stop to break-even; the rest at +3R or on a 2-min close below SMA20 (after the first half is off); flat 15:50.
RISK      0.1% of equity per trade in paper (README 0.5%, owner can raise it), exhaustion half; max 5 trades/day, 2 open, 2 per symbol;
          daily cap -4R (= the README's 2% at its 0.5% risk). PDT counter not used (paper; FINRA retired the rule 2026-06-04 per the
          VWAP spec). Bracket orders = market entry + broker stop on fill (the existing sleeve's tested pattern), targets watched by the bot.
COSTS     3 bps a side + SEC fee; 2x stress. Fills: signal on a closed 2-min bar, entry at the next minute's open; stop first when a bar
          touches stop and target.
CONTROL   20 seeds: the same symbols / days / number of trades, random entry minute (09:35-15:30), same stop distance and exits.
"""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import vwapday as vd

CFG = {"risk_pct": 0.001, "max_new": 5, "max_open": 2, "per_symbol": 2, "daily_cap_r": 4.0, "first_min": 5, "last_entry": 360,
       "flat": 380, "gap_lo": 0.01, "gap_hi": 0.10, "rvol5": 1.2, "adv_sh": 1e6, "watch_max": 10, "exhaustion": True}


# ---------------------------------------------------------------- features (continuous history; past bars only)
def _bars(mf: pd.DataFrame, mins: int) -> pd.DataFrame:
    k = mf["m"].to_numpy() // mins
    g = mf.assign(k=k).groupby(["d", "k"], sort=True)
    b = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(), "v": g["v"].sum()})
    b["close_min"] = b.index.get_level_values("k") * mins + mins - 1
    return b


def _rsi(c: pd.Series, n=14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def features(mf: pd.DataFrame) -> pd.DataFrame:
    """One row per 2-minute bar with every gate, vote and the signal (mode 'trend' / 'exhaustion'), stop and entry minute."""
    b2 = _bars(mf, 2)
    c, o, h, l, v = b2["c"], b2["o"], b2["h"], b2["l"], b2["v"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    sma20, sma200 = c.rolling(20).mean(), c.rolling(200).mean()
    slope = sma20 - sma20.shift(5)
    rsi = _rsi(c)
    vavg = v.rolling(20).mean().shift(1)
    side_sma = np.sign(c - sma20)
    crosses = (side_sma != side_sma.shift(1)).astype(float).rolling(20).sum()
    bias = (c > sma20) & (slope > 0) & (sma20 > sma200)
    wide = (atr > atr.rolling(20).mean()) & (slope.abs() >= 0.1 * atr)
    choppy, flat = crosses >= 4, slope.abs() < 0.1 * atr
    low5 = l.rolling(5).min()
    pull = (low5 <= sma20 + 0.1 * atr) & (c > o) & (c > sma20) & (v > v.shift(1))
    twobar = (c.shift(1) < o.shift(1)) & (l.shift(1) < l.shift(2)) & (c > h.shift(1)) & (c > o)
    hi5, lo5p = h.shift(1).rolling(5).max(), l.shift(1).rolling(5).min()
    flag = ((hi5 - lo5p) < atr) & (c > hi5)
    trigger = pull | twobar | flag
    # day-level: VWAP (from 1-min), 15-minute opening range
    mf = mf.copy()
    typ = (mf["h"] + mf["l"] + mf["c"]) / 3
    mf["pv"] = typ * mf["v"]
    g = mf.groupby("d")
    mf["vwap"] = g["pv"].cumsum() / g["v"].cumsum().replace(0, np.nan)
    orh = mf[mf["m"] < 15].groupby("d")["h"].max()
    m1 = mf.set_index(["d", "m"])
    keys = list(zip(b2.index.get_level_values("d"), b2["close_min"]))
    vw = m1["vwap"].reindex(keys).to_numpy()
    e9 = mf["c"].ewm(span=9, adjust=False).mean()
    e20 = mf["c"].ewm(span=20, adjust=False).mean()
    mf["micro"] = (e9 > e20) & (mf["c"] > mf["o"])
    micro = m1.assign(micro=mf["micro"].to_numpy())["micro"].reindex(keys).fillna(False).to_numpy(bool)
    b5 = _bars(mf, 5)
    s5 = b5["c"].rolling(20).mean()
    agree5 = ((b5["c"] > s5) & (s5 > s5.shift(1))).rename("agree")
    # last COMPLETED 5-min bar at each 2-min bar close: 5-min bar k5 closes at minute 5*k5+4 <= close_min
    a5 = []
    a5_map = {(d, k): bool(x) for (d, k), x in agree5.items()}
    for d, cm in keys:
        k5 = (cm + 1) // 5 - 1
        a5.append(a5_map.get((d, k5), False) if k5 >= 0 else False)
    agree = np.asarray(a5)
    orh_v = np.asarray([orh.get(d, np.nan) for d, _ in keys])
    cm_arr = b2["close_min"].to_numpy()
    v1 = (rsi.between(55, 75)) & (v > 1.5 * vavg) & agree
    brk = (c > h.shift(1).rolling(20).max()) | ((cm_arr >= 15) & (c > orh_v)) | flag
    v2 = brk & (v > 1.2 * vavg)
    above = pd.Series(c.to_numpy() > vw, index=c.index)
    near = pd.Series(l.to_numpy() <= vw * 1.002, index=c.index)
    v3 = above & (near.rolling(3).max().astype(bool) | above.rolling(5).min().astype(bool))
    v4 = pd.Series(micro, index=c.index)
    engulf_bear = (c < o) & (o >= c.shift(1)) & (c <= o.shift(1)) & (c.shift(1) > o.shift(1))
    guard = (rsi <= 80) & (c <= sma20 + 3 * atr) & ~engulf_bear
    votes = v1.astype(int) + v2.astype(int) + v3.astype(int) + v4.astype(int) + guard.astype(int)
    trend = bias & wide & ~choppy & ~flat & trigger & guard & (votes >= 3) & above
    engulf_bull = (c > o) & (o <= c.shift(1)) & (c >= o.shift(1)) & (c.shift(1) < o.shift(1))
    exh = (c < sma20 - 3 * atr) & (rsi < 20) & (v > 3 * vavg) & engulf_bull
    stop_t = np.maximum(low5, sma20 - 0.5 * atr)
    f = pd.DataFrame({"d": b2.index.get_level_values("d"), "k": b2.index.get_level_values("k"), "close_min": cm_arr, "c": c.to_numpy(),
                      "sma20": sma20.to_numpy(), "atr": atr.to_numpy(), "rsi": rsi.to_numpy(), "votes": votes.to_numpy(),
                      "trend": trend.fillna(False).to_numpy(bool), "exh": exh.fillna(False).to_numpy(bool),
                      "stop_t": stop_t.to_numpy(), "stop_x": (l - 0.1 * atr).to_numpy(), "vwap": vw})
    return f


def stop_for(row, entry: float, mode: str) -> float | None:
    atr = row["atr"]
    if not np.isfinite(atr) or atr <= 0:
        return None
    raw = row["stop_t"] if mode == "trend" else row["stop_x"]
    if not np.isfinite(raw) or raw >= entry:
        raw = entry - 0.5 * atr
    dist = max(entry - raw, 0.5 * atr, 0.0015 * entry)
    return entry - dist


# ---------------------------------------------------------------- one trade on 1-minute bars
TGT = {"t1": 2.0, "t2": 3.0}      # the README's targets in R; alpacabot.tpearly patches these for the early-exit test (live uses 2R / 3R)


def walk(sd: dict, j: int, e: float, stop: float, f_day: pd.DataFrame, flat: int = 380):
    from . import minexits as _mx
    if _mx.OVERRIDE["exit"] and _mx.OVERRIDE["exit"] != "own":          # minute-bar exit lab only (never set by the live sleeve)
        return _mx.min_walk(_mx.OVERRIDE["exit"], sd, 1, j, e, stop, min(flat, sd["n"] - 1))[0]
    o, h, l = sd["o"], sd["h"], sd["l"]
    R0 = e - stop
    t1, t2 = e + TGT["t1"] * R0, e + TGT["t2"] * R0
    sma_at = {int(cm): (float(cc), float(s)) for cm, cc, s in zip(f_day["close_min"], f_day["c"], f_day["sma20"])}
    ev, rem, half = [], 1.0, False
    end = min(flat, sd["n"] - 1)
    for i in range(j, end):
        oi, hi, li = float(o[i]), float(h[i]), float(l[i])
        if oi <= stop:
            ev.append((i, rem, oi, "be" if half else "stop"))
            return ev
        if li <= stop:
            ev.append((i, rem, stop, "be" if half else "stop"))
            return ev
        if not half and hi >= t1:
            ev.append((i, 0.5, max(oi, t1), "t1"))
            rem, half, stop = 0.5, True, e
            if hi >= t2:
                ev.append((i, rem, max(oi, t2), "t2"))
                return ev
            continue
        if half and hi >= t2:
            ev.append((i, rem, max(oi, t2), "t2"))
            return ev
        if half and i in sma_at and i + 1 < end:
            cc, s = sma_at[i]
            if np.isfinite(s) and cc < s:
                ev.append((i + 1, rem, float(o[i + 1]), "trail"))
                return ev
    ev.append((end, rem, float(o[end]), "eod_1550"))
    return ev


def r_of(ev, e, R0, mult=1.0):
    return vd.r_of(ev, e, R0, 1, mult)


# ---------------------------------------------------------------- scanner (daily)
def scan_table(mf: pd.DataFrame, d1: pd.DataFrame) -> pd.DataFrame:
    g = mf.groupby("d")
    first5 = mf[mf["m"] < 5].groupby("d")["v"].sum()
    t = pd.DataFrame({"open": g["o"].first(), "first5": first5})
    t["rvol5"] = t["first5"] / t["first5"].shift(1).rolling(14, min_periods=10).mean()
    dd = d1.copy()
    di = pd.DatetimeIndex(dd.index)
    dd.index = (di.tz_convert(vd.NY).tz_localize(None) if di.tz is not None else di).normalize().date
    dd = dd[~pd.Index(dd.index).duplicated(keep="last")]
    t = t.join(pd.DataFrame({"prev_close": dd["close"].shift(1), "advsh20": dd["volume"].rolling(20).mean().shift(1)}), how="left")
    t["gap"] = t["open"] / t["prev_close"] - 1
    return t


def watchlist(tables: dict, d, cfg=CFG) -> list:
    cand = []
    for s, t in tables.items():
        if d not in t.index:
            continue
        r = t.loc[d]
        if (np.isfinite(r["rvol5"]) and r["rvol5"] > cfg["rvol5"] and np.isfinite(r["gap"]) and cfg["gap_lo"] <= abs(r["gap"]) <= cfg["gap_hi"]
                and r["prev_close"] >= 5 and r["advsh20"] >= cfg["adv_sh"]):
            cand.append((float(r["rvol5"]), s))
    return [s for _, s in sorted(cand, reverse=True)[:cfg["watch_max"]]]


# ---------------------------------------------------------------- backtest
def prep(load, today: date, log=print, days_back: int = 120, cfg=CFG) -> dict:
    """Data for the backtest (loaded once; alpacabot.minexits re-runs the trades with other exits)."""
    from .universe import STOCKS
    end = today - timedelta(days=1)
    start = end - timedelta(days=days_back)
    feats, sds, tables, sess = {}, {}, {}, {}
    spy = vd.minute_frame(load("SPY", "1Min", start, end))
    lm = spy.groupby("d")["m"].max()
    days = sorted(lm.index)
    sess = {d: (210 if lm[d] < 230 else 390) for d in days}
    for i, s in enumerate(sorted(set(STOCKS))):
        try:
            mf = vd.minute_frame(load(s, "1Min", start, end))
            d1 = load(s, "1Day", start - timedelta(days=60), end)
            if not len(mf) or not len(d1):
                continue
            tables[s] = scan_table(mf, d1)
            feats[s] = features(mf)
            sds[s] = mf
            log(f"[{i + 1}/{len(set(STOCKS))}] {s}")
        except Exception as exc:
            log(f"{s}: {exc!r}")
    test_days = [d for d in days if d >= days[0] + timedelta(days=30)]      # warm-up: RVOL history, SMA200 on 2-min bars
    wl = {d: watchlist(tables, d, cfg) for d in test_days}
    need = {}
    for d, ss in wl.items():
        for s in ss:
            need.setdefault(s, set()).add(d)
    sd_map = {}
    for s, ds in need.items():
        for d, v in vd.symbol_days(sds[s], ds, sess).items():
            sd_map[(s, d)] = v
    sds.clear()
    return {"cfg": cfg, "test_days": test_days, "wl": wl, "sd_map": sd_map, "feats": feats, "sess": sess}


def run_trades(P: dict, mult=1.0, rng=None, recs=None):
    cfg, test_days, wl, sd_map, feats, sess = P["cfg"], P["test_days"], P["wl"], P["sd_map"], P["feats"], P["sess"]
    from . import minexits as _mx
    trades = []
    for d in test_days:
        _mx.CUR["week"] = d.isocalendar()[:2]
        flat = 200 if sess[d] <= 210 else cfg["flat"]
        last_entry = 195 if sess[d] <= 210 else cfg["last_entry"]
        cands = []
        for s in wl[d]:
            sd = sd_map.get((s, d))
            if sd is None:
                continue
            fd = feats[s][feats[s]["d"] == d]
            sig = fd[(fd["trend"] | (fd["exh"] if cfg["exhaustion"] else False))]
            for _, r in sig.iterrows():
                j = int(r["close_min"]) + 1
                if j < cfg["first_min"] or j > last_entry:
                    continue
                mode = "trend" if r["trend"] else "exhaustion"
                cands.append((j, s, mode, r, fd, sd))
        if rng is not None:                               # control: same names / count, random entry minutes
            cands = [(int(rng.integers(cfg["first_min"], last_entry + 1)), s, m, r, fd, sd) for _, s, m, r, fd, sd in cands]
        cands.sort(key=lambda x: (x[0], x[1]))
        open_, n_new, per, realized, stopped = [], 0, {}, 0.0, False
        for j, s, mode, r, fd, sd in cands:
            for t in [t for t in open_ if t["exit_min"] < j]:
                open_.remove(t)
                realized += t["w"] * t["R"]
                if realized <= -cfg["daily_cap_r"]:
                    stopped = True
            if stopped or n_new >= cfg["max_new"] or len(open_) >= cfg["max_open"] or per.get(s, 0) >= cfg["per_symbol"] \
                    or any(t["symbol"] == s for t in open_):
                continue
            e = float(sd["o"][j])
            st = stop_for(r, e, mode)
            if st is None:
                continue
            ev = walk(sd, j, e, st, fd, flat)
            R = r_of(ev, e, e - st, mult)
            t = {"date": d, "symbol": s, "mode": mode, "entry_min": j, "entry": e, "stop": st, "R": R, "w": 1.0 if mode == "trend" else 0.5,
                 "exit_min": ev[-1][0], "exit_reason": ev[-1][3], "votes": int(r["votes"]), "rsi": float(r["rsi"])}
            open_.append(t)
            trades.append(t)
            n_new += 1
            per[s] = per.get(s, 0) + 1
    return trades


def backtest(load, today: date, out_root: Path, log=print, seeds: int = 20, days_back: int = 120, cfg=CFG) -> list[str]:
    P = prep(load, today, log=log, days_back=days_back, cfg=cfg)
    test_days = P["test_days"]
    wl = P["wl"]

    def run(mult=1.0, rng=None, recs=None):
        return run_trades(P, mult, rng, recs)

    tr, tr2 = run(), run(mult=2.0)
    ctrl = [vd.pf(t["R"] for t in run(rng=np.random.default_rng(k))) for k in range(seeds)]
    st, st2 = vd.stats(tr), vd.stats(tr2)
    beat = sum(st["pf"] > x for x in ctrl)
    last2 = [t for t in tr if t["date"] >= test_days[-10]]
    s2w = vd.stats(last2)
    out = out_root / f"velez-{today:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"## Velez-Confirmed day bot - backtest {test_days[0]}..{test_days[-1]} ({len(test_days)} sessions), 55 stocks, README rules "
             "(alpacabot/velez.py), long only, flat 15:50, 3 bps a side. SHORT HISTORY: about 3 months - a sample, not proof.",
             "| Set | Trades | Win% | PF | Avg win R | Avg loss R | Exp R | Net R | PF @2x | Random-entry beaten |", "|" + "---|" * 10,
             f"| all | {st['n']} | {st['win']:.0%} | {st['pf']:.2f} | {st['avg_win']:+.2f} | {st['avg_loss']:+.2f} | {st['exp']:+.3f} | "
             f"{st['net']:+.1f} | {st2['pf']:.2f} | {beat}/{seeds} (median PF {np.median(ctrl):.2f}) |"]
    for mode in ("trend", "exhaustion"):
        x = vd.stats([t for t in tr if t["mode"] == mode])
        lines.append(f"| {mode} | {x['n']} | {x['win']:.0%} | {x['pf']:.2f} | {x['avg_win']:+.2f} | {x['avg_loss']:+.2f} | {x['exp']:+.3f} | "
                     f"{x['net']:+.1f} | - | - |")
    lines.append(f"| last 2 weeks | {s2w['n']} | {s2w['win']:.0%} | {s2w['pf']:.2f} | {s2w['avg_win']:+.2f} | {s2w['avg_loss']:+.2f} | "
                 f"{s2w['exp']:+.3f} | {s2w['net']:+.1f} | - | - |")
    why = pd.Series([t["exit_reason"] for t in tr]).value_counts().to_dict() if tr else {}
    by_m = {}
    for t in tr:
        by_m.setdefault(str(t["date"])[:7], []).append(t["R"])
    lines.append(f"- exits: {why}; by month: " + ", ".join(f"{k} PF {vd.pf(v):.2f} ({len(v)}, {sum(v):+.1f}R)" for k, v in sorted(by_m.items())))
    lines.append(f"- watchlist size per day: avg {np.mean([len(v) for v in wl.values()]):.1f}; days with no watchlist: "
                 f"{sum(1 for v in wl.values() if not v)}")
    vd._csv([{**t, "date": str(t["date"])} for t in tr], out / "trades.csv")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    return lines


# ---------------------------------------------------------------- live paper sleeve
from .vwaplive import VwapLive  # noqa: E402

VELEZ_LIVE = {**CFG, "notional_cap": 0.25, "daily_limit_r": CFG["daily_cap_r"], "paused": False, "start_date": "2026-11-03",
              # 2026-11-03 owner-approved pre-registered test (alpacabot.tpearly): first target 1.9R beat 2R in both halves and at 2x costs
              # (avg R -0.314 vs -0.351 2nd half, -0.248 vs -0.257 1st, PF@2x 0.43 vs 0.41; 112 trades). Small - the system still loses.
              # UNDO: "t1_r": 2.0
              "t1_r": 1.9, "t2_r": 3.0}


class VelezLive(VwapLive):
    """Same order / stop / partial / exit plumbing as the VWAP sleeve (alpacabot.vwaplive), Velez signals and exits."""

    def __init__(self, broker, state_dir="state/alpaca_velez", cfg=None, **kw):
        super().__init__(broker, state_dir=state_dir, cfg=cfg or VELEZ_LIVE, **kw)
        self.prefix = "ve"

    def build_universe(self, now):
        from .universe import STOCKS
        refs = {}
        for s in sorted(set(STOCKS)):
            try:
                r = self.daily_refs(s, now)
            except Exception as exc:
                self.journal("error", now, where="daily", symbol=s, error=str(exc)[:160])
                continue
            if r:
                refs[s] = r
        self.state.update(universe=sorted(refs), refs=refs)
        self.journal("universe", now, n=len(refs))

    def build_inplay(self, now):
        cand = []
        for s in self.state.get("universe", []):
            ref = self.state["refs"][s]
            try:
                mf = self.bars(s, now, 5, days=25)
            except Exception as exc:
                self.journal("error", now, where="scanner", symbol=s, error=str(exc)[:160])
                continue
            today = now.date()
            f5 = mf[mf["m"] < 5].groupby("d")["v"].sum()
            if today not in f5.index:
                continue
            prev = f5[f5.index < today].tail(14)
            op = mf[(mf["d"] == today)].sort_values("m")["o"]
            if len(prev) < 10 or prev.mean() <= 0 or not len(op):
                continue
            rv, gap = float(f5[today] / prev.mean()), float(op.iloc[0]) / ref["prev_close"] - 1
            if rv > self.cfg["rvol5"] and self.cfg["gap_lo"] <= abs(gap) <= self.cfg["gap_hi"] and ref["prev_close"] >= 5 \
                    and ref.get("advsh20", 0) >= self.cfg["adv_sh"]:
                cand.append((rv, s, gap))
        self.state["inplay"] = [s for _, s, _ in sorted(cand, reverse=True)[:self.cfg["watch_max"]]]
        self.state["rvol"] = {s: round(r, 2) for r, s, _ in cand}
        self.journal("inplay", now, names=self.state["inplay"], scan={s: {"rvol5": round(r, 2), "gap": round(g, 4)} for r, s, g in cand})

    def step(self, now):
        """As the VWAP sleeve, with the README's windows: scanner at 09:35, entries 09:35-15:30, flat 15:50, -4R daily cap."""
        if not self.cfg:
            return
        day = now.date().isoformat()
        if self.state.get("date") != day:
            carried = self.state.get("open", {})
            self.state = {"date": day, "open": carried, "carried": sorted(carried), "done": [], "traded": [], "n_new": 0, "realized_R": 0.0,
                          "per": {}}
        m = self.minute_of(now)
        if m < 0 or m >= 400 or now.second < 8 or m == self.last_min:
            return
        self.last_min = m
        sess = self.session_len(now)
        last_entry, flat = (195, 200) if sess <= 210 else (self.cfg["last_entry"], self.cfg["flat"])
        for sym in [x for x in self.state.get("carried", []) if x in self.state["open"]]:
            self.safe_exit(sym, now, "carried over (missed flat)")
        self.state["carried"] = []
        if "universe" not in self.state:
            self.build_universe(now)
        if m >= 5 and "inplay" not in self.state:
            self.build_inplay(now)
        if m >= flat:
            for sym in list(self.state["open"]):
                self.safe_exit(sym, now, "eod_1550")
            self.save()
            return
        self.manage(now, m)
        if self.cfg.get("paused") or self.state.get("stopped") or "inplay" not in self.state or day < self.cfg.get("start_date", ""):
            self.save()
            return
        if self.cfg["first_min"] <= m <= last_entry and m % 2 == 0:       # right after a 2-minute bar closed
            self.scan(now, m)
        self.save()

    def scan(self, now, m):
        st, cfg = self.state, self.cfg
        if st["n_new"] >= cfg["max_new"] or len(st["open"]) >= cfg["max_open"]:
            return
        held = self.b.positions()
        for sym in st["inplay"]:
            if st["n_new"] >= cfg["max_new"] or len(st["open"]) >= cfg["max_open"]:
                break
            if sym in st["open"] or held.get(sym) or st["per"].get(sym, 0) >= cfg["per_symbol"]:
                continue
            try:
                mf = self.bars(sym, now, m, days=5)
                f = features(mf)
                last = f[(f["d"] == now.date()) & (f["close_min"] == m - 1)]
                if not len(last):
                    continue
                r = last.iloc[-1]
                mode = "trend" if r["trend"] else "exhaustion" if (cfg["exhaustion"] and r["exh"]) else None
                if not mode:
                    continue
                bid, ask = (float(x) for x in self.b.quote(sym)[:2])
                stop = stop_for(r, ask, mode)
            except Exception as exc:
                self.journal("error", now, where="signal", symbol=sym, error=str(exc)[:160])
                continue
            if stop is None:
                continue
            saved = cfg["risk_pct"]
            if mode == "exhaustion":
                self.cfg = {**cfg, "risk_pct": saved / 2}
            try:
                self.enter(sym, mode, 1, {"entry": ask, "stop": stop, "s": m - 1}, now, m)
            finally:
                self.cfg = cfg
            if sym in st["open"]:
                st["open"][sym].update(t1=ask + cfg.get("t1_r", 2.0) * (ask - stop), t2=ask + cfg.get("t2_r", 3.0) * (ask - stop), votes=int(r["votes"]))
                st["per"][sym] = st["per"].get(sym, 0) + 1

    def manage(self, now, m):
        for sym, pos in list(self.state["open"].items()):
            if pos.get("stop_id"):
                try:
                    so = self.b.order(pos["stop_id"])
                except Exception:
                    so = {}
                if so.get("status") == "filled":
                    self.record(sym, pos, float(so.get("filled_avg_price") or pos["stop"]), pos["qty"], "stop", now)
                    continue
            try:
                bid, _ = (float(x) for x in self.b.quote(sym)[:2])
            except Exception as exc:
                self.journal("error", now, where="manage", symbol=sym, error=str(exc)[:160])
                continue
            e, R0 = pos["entry"], pos["R0"]
            # 2026-11-03 fix: the targets set at entry (2R / 3R on the signal's own risk). Recomputing them from the fill made a slipped
            # fill push them out (CEG: fill 20 bps above the quote -> R 1.48 instead of 0.95, tp1 266.73 instead of 265.14: never locked)
            t1, t2 = float(pos.get("t1") or e + 2 * R0), float(pos.get("t2") or e + 3 * R0)
            pos["best"] = max(float(pos.get("best", e)), bid)
            if pos["parts"] == 0 and bid >= t1:
                pos["stop"] = round(e, 2)                     # break-even for the rest (partial re-places the stop at pos["stop"])
                self.partial(sym, pos, now)
                continue
            if pos["parts"] >= 1 and bid >= t2:
                self.safe_exit(sym, now, "t2")
                continue
            if pos["parts"] >= 1 and m % 2 == 0:
                try:
                    f = features(self.bars(sym, now, m, days=5))
                    last = f[(f["d"] == now.date()) & (f["close_min"] == m - 1)]
                    if len(last) and np.isfinite(last.iloc[-1]["sma20"]) and last.iloc[-1]["c"] < last.iloc[-1]["sma20"]:
                        self.safe_exit(sym, now, "trail")
                except Exception as exc:
                    self.journal("error", now, where="trail", symbol=sym, error=str(exc)[:160])

    def record(self, sym, pos, px, qty, reason, now):
        super().record(sym, pos, px, qty, reason, now)
        if self.state.get("realized_R", 0.0) <= -self.cfg["daily_cap_r"] and not self.state.get("stopped"):
            self.state["stopped"] = True
            self.journal("daily_limit", now, realized_R=round(self.state["realized_R"], 3))
            for s2 in list(self.state["open"]):
                self.safe_exit(s2, now, "daily_limit")


def summary(state_dir="state/alpaca_velez") -> list[str]:
    from .vwaplive import summary as _s
    return [x.replace("VWAP / ORB day-trade sleeve (paper, 0.05% risk)", "Velez-Confirmed sleeve (paper, 0.1% risk)") for x in _s(state_dir)]
