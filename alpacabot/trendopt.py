"""Optimized Trend Stock bot (owner 2026-11-03: uploaded bot_optimized.py, "analyze, optimize, install; 3 month test then run paper today").

The uploaded rules, UNCHANGED (the file's "do not change" list): 5-minute bars; long when ALL 4 agree (close > SMA20 and SMA20 rising, close >
session VWAP, RSI14 > 50, Stoch %K > %D), SMA20 angle (SMA20 - SMA20 5 bars ago) / 5 / ATR14 >= 0.30, close >= SMA20 + 0.25 ATR, bar volume /
20-bar average > 1.2, the last COMPLETED 15-min bar above its rising SMA20, the PREVIOUS day's close above its rising daily SMA20; short = mirror
(easy-to-borrow only). Hard stop 1 ATR (5-min ATR) at the broker; close-only virtual trail once the best close is +0.5R (trail = best - 0.5R,
no cap); 3 closes in a row on the wrong side of VWAP and SMA20 -> out; no fixed target; window 09:45-15:45 ET, flat at 15:45. Risk 0.5% of
equity, max 2 open, max 5 trades a day, daily loss cap 2% (no new trades after it).

Fixes ("optimize" = the script's bugs, not retuned settings - retuning on 3 months would only fit the noise):
  1. the 3-candle counter counted 60-second SCANS of the same bar (3 minutes on one bad bar = exit) -> counted per closed 5-min bar;
  2. the forming 5-min bar could be read as closed -> only bars whose end <= now;
  3. a broker stop that filled was not noticed, so the later market "exit" opened an opposite position -> the stop's status is checked
     every minute and the position is checked at the broker before any exit (the tested sleeve plumbing, alpacabot.vwaplive);
  4. no size cap: 0.5% risk to a 1x 5-min ATR stop can be 2-3x the account -> notional capped at 25% of equity (R unchanged);
  5. stop from the bar close -> from the actual fill; positions persisted (a restart no longer orphans them).
Backtest (velez-style harness on our cached SIP minute bars, 55-stock universe; the uploaded config's watchlist was not included):
entries at the next 5-min bar's open, the broker stop intrabar (stop first), close-based exits at the next bar's open, 3 bps a side + SEC fee,
2x stress, 20 random-entry (same symbol/day/side, random time) and 20 shuffled-direction controls.
"""

import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import vwapday as vd

CFG = {"consensus": 4, "angle_min": 0.30, "px_vs_sma": 0.25, "relvol_min": 1.2, "stop_atr": 1.0, "trail_start": 0.5, "trail_gap": 0.5,
       "risk_pct": 0.005, "max_open": 2, "max_new": 5, "daily_cap_pct": 0.02, "start_bar": 3, "flat_bar": 75, "notional_cap": 0.25}


# ---------------------------------------------------------------- bars and indicators (closed bars only)
def bars5(mf: pd.DataFrame) -> pd.DataFrame:
    """vwapday.minute_frame rows (d, m, o, h, l, c, v) -> 5-minute RTH bars (d, k), k = 0 for 09:30."""
    k = mf["m"].to_numpy() // 5
    g = mf.assign(k=k).groupby(["d", "k"], sort=True)
    return pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(), "v": g["v"].sum()}).reset_index()


def daily_flags(d1: pd.DataFrame) -> dict:
    """date -> (long_ok, short_ok) from the PREVIOUS trading day's daily bar (close vs a rising / falling SMA20)."""
    dd = d1.copy()
    di = pd.DatetimeIndex(dd.index)
    dd.index = (di.tz_convert(vd.NY).tz_localize(None) if di.tz is not None else di).normalize().date
    dd = dd[~pd.Index(dd.index).duplicated(keep="last")]
    c = dd["close"].astype(float)
    s = c.rolling(20).mean()
    lo = ((c > s) & (s > s.shift(1))).shift(1)
    sh = ((c < s) & (s < s.shift(1))).shift(1)
    out = {d: (bool(a) if a == a else False, bool(b) if b == b else False) for d, a, b in zip(dd.index, lo, sh)}
    if len(dd):                                     # the session after the last daily bar (live: today)
        out["_next"] = (bool((c.iloc[-1] > s.iloc[-1]) and (s.iloc[-1] > s.iloc[-2])) if len(c) > 21 else False,
                        bool((c.iloc[-1] < s.iloc[-1]) and (s.iloc[-1] < s.iloc[-2])) if len(c) > 21 else False)
    return out


def features(b: pd.DataFrame, dflags: dict, cfg=CFG) -> pd.DataFrame:
    """Every rule of the uploaded check_signal, per 5-min bar (continuous across days, as the script's 5-day history)."""
    b = b.copy()
    c, h, l, o, v = (b[k].astype(float) for k in ("c", "h", "l", "o", "v"))
    sma = c.rolling(20).mean()
    smap = sma.shift(1)
    pc = c.shift(1)
    atr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1).rolling(14).mean()
    dl = c.diff()
    up_, dn_ = dl.clip(lower=0).rolling(14).mean(), (-dl.clip(upper=0)).rolling(14).mean()
    rsi = (100 - 100 / (1 + up_ / dn_.replace(0, np.nan))).where(dn_ > 0, np.where(up_ > 0, 100.0, 50.0)).where(up_.notna())
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    sk = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    sdd = sk.rolling(3).mean()
    relvol = v / v.rolling(20).mean().replace(0, np.nan)
    typ = (h + l + c) / 3
    vw = (typ * v).groupby(b["d"]).cumsum() / v.groupby(b["d"]).cumsum().replace(0, np.nan)
    angle = (sma - sma.shift(5)) / 5 / atr.replace(0, np.nan)
    # 15-minute bars (09:30-aligned), SMA20 continuous; a 5-min bar uses the last COMPLETED 15-min bar of its own day
    k15 = b["k"] // 3
    g = b.assign(k15=k15).groupby(["d", "k15"], sort=True)["c"].last()
    s15 = g.rolling(20).mean()
    up15 = ((g > s15) & (s15 > s15.shift(1))).to_dict()
    dn15 = ((g < s15) & (s15 < s15.shift(1))).to_dict()
    prev_key = list(zip(b["d"], k15 - 1))
    htf_l = np.array([bool(up15.get(kk, False)) if kk[1] >= 0 else False for kk in prev_key])
    htf_s = np.array([bool(dn15.get(kk, False)) if kk[1] >= 0 else False for kk in prev_key])
    day_l = np.array([dflags.get(d, (False, False))[0] for d in b["d"]])
    day_s = np.array([dflags.get(d, (False, False))[1] for d in b["d"]])
    n_l = ((c > sma) & (sma > smap)).astype(int) + (c > vw).astype(int) + (rsi > 50).astype(int) + (sk > sdd).astype(int)
    n_s = ((c < sma) & (sma < smap)).astype(int) + (c < vw).astype(int) + (rsi < 50).astype(int) + (sk < sdd).astype(int)
    ok_vol = relvol > cfg["relvol_min"]
    long_ = ok_vol & (n_l >= cfg["consensus"]) & (angle >= cfg["angle_min"]) & (c >= sma + cfg["px_vs_sma"] * atr) & htf_l & day_l
    short_ = ok_vol & (n_s >= cfg["consensus"]) & (angle <= -cfg["angle_min"]) & (c <= sma - cfg["px_vs_sma"] * atr) & htf_s & day_s
    b["sma20"], b["vwap"], b["atr"], b["angle"], b["relvol"], b["rsi"] = sma, vw, atr, angle, relvol, rsi
    b["long"], b["short"] = long_.fillna(False).astype(bool), short_.fillna(False).astype(bool)
    return b


# ---------------------------------------------------------------- one trade on a day's 5-min bars
def walk(day: pd.DataFrame, j: int, side: int, e: float, R0: float, cfg=CFG):
    """Entry at the open of bar j (fill e); hard stop e -/+ R0 intrabar (stop first, gap -> open); at each close: best close, 3-candle,
    close-only trail; close-based exits fill at the next bar's open; flat at the 15:45 bar's open. Returns vwapday-style events."""
    o, h, l, c = (day[k].to_numpy(float) for k in ("o", "h", "l", "c"))
    sma, vw = day["sma20"].to_numpy(float), day["vwap"].to_numpy(float)
    kk = day["k"].to_numpy()
    n = len(o)
    stop = e - side * R0
    peak, below = e, 0
    for i in range(j, n):
        if kk[i] >= cfg["flat_bar"]:
            return [(i, 1.0, float(o[i]), "flatten_1545")]
        if side * (o[i] - stop) <= 0 and i > j:
            return [(i, 1.0, float(o[i]), "hard_stop")]
        if (l[i] <= stop) if side == 1 else (h[i] >= stop):
            return [(i, 1.0, float(stop), "hard_stop")]
        peak = max(peak, c[i]) if side == 1 else min(peak, c[i])
        below = below + 1 if (side * (c[i] - vw[i]) < 0 and side * (c[i] - sma[i]) < 0) else 0
        nxt = (i + 1, 1.0, float(o[i + 1])) if i + 1 < n else (i, 1.0, float(c[i]))
        if below >= 3:
            return [(*nxt, "3candle")]
        pr = side * (peak - e) / R0
        if pr >= cfg["trail_start"]:
            tl = e + side * (pr - cfg["trail_gap"]) * R0
            if side * (c[i] - tl) < 0:
                return [(*nxt, "virtual_trail")]
    return [(n - 1, 1.0, float(c[n - 1]), "end")]


# ---------------------------------------------------------------- backtest
def backtest(load, today: date, out_root, log=print, seeds: int = 20, months: int = 3, cfg=CFG) -> list[str]:
    from pathlib import Path
    from .universe import STOCKS
    end = today - timedelta(days=1)
    t0 = (pd.Timestamp(end) - pd.DateOffset(months=months)).date()
    start = t0 - timedelta(days=21)
    days_data = {}
    for i, s in enumerate(sorted(set(STOCKS))):
        try:
            mf = vd.minute_frame(load(s, "1Min", start, end))
            d1 = load(s, "1Day", start - timedelta(days=90), end)
            if not len(mf) or not len(d1):
                continue
            f = features(bars5(mf), daily_flags(d1), cfg)
            f = f[f["d"] >= t0]
            for d, g in f.groupby("d"):
                days_data[(s, d)] = g.reset_index(drop=True)
            log(f"[{i + 1}/{len(set(STOCKS))}] {s}")
        except Exception as exc:
            log(f"{s}: {exc!r}")
    test_days = sorted({d for _, d in days_data})
    spy_open = {}
    if cfg.get("spy_vs_open"):      # owner 2026-11-05: SPY's last 5-minute close vs today's open picks the side (longs above, shorts below)
        sb = bars5(vd.minute_frame(load("SPY", "1Min", start, end)))
        for dd_, gg in sb.groupby("d"):
            o0 = float(gg["o"].iloc[0])
            spy_open[dd_] = {int(k): (1 if c > o0 else -1 if c < o0 else 0) for k, c in zip(gg["k"], gg["c"])}
    regime = {}
    if cfg.get("spy_regime"):        # owner 2026-11-05: SPY's previous close vs its rising / falling SMA20 picks the side (daily_flags rule)
        regime = daily_flags(load("SPY", "1Day", start - timedelta(days=90), end))

    def allowed(d, side):
        lo, sh = regime.get(d, (False, False))
        return not ((lo and not sh and side == -1) or (sh and not lo and side == 1))

    def run(mult=1.0, rng=None, shuffle=False):
        trades = []
        for d in test_days:
            cands = []
            for (s, dd_), g in ((k, v) for k, v in days_data.items() if k[1] == d):
                for i in np.flatnonzero(g["long"].to_numpy() | g["short"].to_numpy()):
                    j = i + 1
                    if j >= len(g) or g["k"].iat[j] < cfg["start_bar"] or g["k"].iat[j] >= cfg["flat_bar"]:
                        continue
                    side = 1 if g["long"].iat[i] else -1
                    if regime and not allowed(d, side):
                        continue
                    if spy_open:
                        sgn = spy_open.get(d, {}).get(int(g["k"].iat[i]), 0)     # SPY at the close of the signal bar
                        if sgn and sgn != side:
                            continue
                    cands.append((j, s, side, float(g["atr"].iat[i]), g))
            cands.sort(key=lambda x: (x[0], x[1]))
            open_, n_new, realized, busy = [], 0, 0.0, {}
            for j, s, side, a, g in cands:
                for t in [t for t in open_ if t["x"] < j]:
                    open_.remove(t)
                    realized += t["R"]
                if realized <= -cfg["daily_cap_pct"] / cfg["risk_pct"] or n_new >= cfg["max_new"] or len(open_) >= cfg["max_open"] \
                        or busy.get(s, -1) >= j or not (a > 0):
                    continue
                jj = j
                if rng is not None and not shuffle:          # random-entry control: same symbol / day / side, random time
                    lo_, hi_ = int(np.searchsorted(g["k"].to_numpy(), cfg["start_bar"])), int(np.searchsorted(g["k"].to_numpy(), cfg["flat_bar"]))
                    if hi_ - lo_ < 2:
                        continue
                    jj = int(rng.integers(max(lo_, 1), hi_))
                    a = float(g["atr"].iat[jj - 1])
                    if not (a > 0):
                        continue
                sd = side if not shuffle else int(rng.choice([1, -1]))   # shuffled control: the real entry time, a coin-flip side
                e = float(g["o"].iat[jj])
                R0 = cfg["stop_atr"] * a
                ev = walk(g, jj, sd, e, R0, cfg)
                R = vd.r_of(ev, e, R0, sd, mult)
                t = {"date": str(d), "symbol": s, "side": sd, "entry_bar": int(g["k"].iat[jj]), "entry": e, "R0": R0, "R": R,
                     "x": ev[-1][0], "exit_reason": ev[-1][3], "month": str(d)[:7]}
                open_.append(t)
                trades.append(t)
                busy[s] = ev[-1][0]
                n_new += 1
        return trades

    tr, tr2 = run(), run(mult=2.0)
    ctrl_r = [vd.pf(t["R"] for t in run(rng=np.random.default_rng(k))) for k in range(seeds)]
    ctrl_s = [vd.pf(t["R"] for t in run(rng=np.random.default_rng(100 + k), shuffle=True)) for k in range(seeds)]
    st, st2 = vd.stats(tr), vd.stats(tr2)
    br, bs = sum(st["pf"] > x for x in ctrl_r), sum(st["pf"] > x for x in ctrl_s)
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in tr:
        eq += t["R"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    out = Path(out_root) / f"trendopt{'-spyregime' if regime else ''}{'-spyopen' if spy_open else ''}-{today:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"## Optimized Trend Stock bot - {months}-month backtest {test_days[0] if test_days else '-'}..{test_days[-1] if test_days else '-'} "
             f"({len(test_days)} sessions), 55 stocks, the uploaded settings unchanged (alpacabot/trendopt.py), 3 bps a side. "
             "No train/holdout split: the uploaded settings were already picked on this period (their note: test used to pick 1 of 18 variants).",
             "| Set | Trades | Win% | PF | Avg win R | Avg loss R | Exp R | Net R | Max DD R | PF @2x | Random /20 | Shuffled /20 |", "|" + "---|" * 12,
             f"| all | {st['n']} | {st['win']:.0%} | {st['pf']:.2f} | {st['avg_win']:+.2f} | {st['avg_loss']:+.2f} | {st['exp']:+.3f} | {st['net']:+.1f} | "
             f"{dd:.1f} | {st2['pf']:.2f} | {br}/{seeds} (median PF {np.median(ctrl_r):.2f}) | {bs}/{seeds} (median PF {np.median(ctrl_s):.2f}) |"]
    for side, nm in ((1, "long"), (-1, "short")):
        x = vd.stats([t for t in tr if t["side"] == side])
        lines.append(f"| {nm} | {x['n']} | {x['win']:.0%} | {x['pf']:.2f} | {x['avg_win']:+.2f} | {x['avg_loss']:+.2f} | {x['exp']:+.3f} | {x['net']:+.1f} | - | - | - | - |")
    by_m = {}
    for t in tr:
        by_m.setdefault(t["month"], []).append(t["R"])
    lines.append("- by month: " + ", ".join(f"{k} PF {vd.pf(v):.2f} ({len(v)}, {sum(v):+.1f}R)" for k, v in sorted(by_m.items())))
    if test_days:
        mid = test_days[len(test_days) // 2]
        h1, h2 = vd.stats([t for t in tr if t["date"] < str(mid)]), vd.stats([t for t in tr if t["date"] >= str(mid)])
        lines.append(f"- halves (split {mid}): 1st {h1['n']} trades PF {h1['pf']:.2f} {h1['net']:+.1f}R | 2nd {h2['n']} trades PF {h2['pf']:.2f} "
                     f"{h2['net']:+.1f}R" + (f" | SPY regime filter ON ({sum(1 for v in regime.values() if v[0] and not v[1])} long-only days, "
                                             f"{sum(1 for v in regime.values() if v[1] and not v[0])} short-only)" if regime else ""))
    why = pd.Series([t["exit_reason"] for t in tr]).value_counts().to_dict() if tr else {}
    lines.append(f"- exits: {why}")
    lines.append(f"- at 0.5% risk: net {st['net'] * 0.5:+.1f}% of equity over the period, worst run {dd * 0.5:.1f}% (before the 25% notional cap)")
    vd._csv(tr, out / "trades.csv")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    return lines


# ---------------------------------------------------------------- live paper sleeve
from .vwaplive import VwapLive  # noqa: E402

# owner 2026-11-04 ("switch the losing sleeves to log only"): 3-month backtest PF 0.40 (203 trades, lost to random entries 20/20).
# Signals still journalled; open trades keep their exits. UNDO: "log_only": False.
TRENDOPT_LIVE = {**CFG, "daily_limit_r": 1e9, "paused": False, "start_date": "2026-11-03", "log_only": True,
                 "spy_vs_open": True,      # owner 2026-11-05: SPY vs today's open picks the side. UNDO: False
                 "spy_regime": False}      # 2026-11-05 OFF: trendopt_regime PF 0.40 -> 0.33          # owner 2026-11-05: SPY regime gate (also in its backtest: trendopt_regime). UNDO: False


class TrendOptLive(VwapLive):
    """The uploaded bot's rules with the fixes above, on the VWAP sleeve's order plumbing (state in state/alpaca_trendopt)."""

    def __init__(self, broker, state_dir="state/alpaca_trendopt", cfg=None, **kw):
        super().__init__(broker, state_dir=state_dir, cfg=cfg or TRENDOPT_LIVE, **kw)
        self.prefix = "to"
        self._dflags = {}

    def b5(self, sym, now):
        start = (now - timedelta(days=8)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        df = self.fetch(sym, "5Min", start, now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "iex")
        if not len(df):
            return None
        idx = pd.DatetimeIndex(df.index)
        df = df[(idx + pd.Timedelta(minutes=5)) <= pd.Timestamp(now)]        # fix 2: closed bars only
        mf = vd.minute_frame(df)
        if not len(mf):
            return None
        return pd.DataFrame({"d": mf["d"].to_numpy(), "k": (mf["m"] // 5).to_numpy(), "o": mf["o"].to_numpy(), "h": mf["h"].to_numpy(),
                             "l": mf["l"].to_numpy(), "c": mf["c"].to_numpy(), "v": mf["v"].to_numpy()})

    def flags(self, sym, now):
        key = (sym, now.date())
        if key not in self._dflags:
            start = (now - timedelta(days=90)).astimezone(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
            end = pd.Timestamp(now.date(), tz=vd.NY).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
            fl = daily_flags(self.fetch(sym, "1Day", start, end, "sip"))
            fl[now.date()] = fl.get("_next", (False, False))
            self._dflags = {k: v for k, v in self._dflags.items() if k[1] == now.date()}
            self._dflags[key] = fl
        return self._dflags[key]

    def step(self, now):
        if not self.cfg:
            return
        day = now.date().isoformat()
        if self.state.get("date") != day:
            carried = self.state.get("open", {})
            self.state = {"date": day, "open": carried, "carried": sorted(carried), "done": [], "traded": [], "n_new": 0, "realized_R": 0.0}
        m = self.minute_of(now)
        if m < 0 or m >= 400 or now.second < 8 or m == self.last_min:
            return
        self.last_min = m
        flat_m = 195 if self.session_len(now) <= 210 else self.cfg["flat_bar"] * 5
        for sym in [x for x in self.state.get("carried", []) if x in self.state["open"]]:
            self.safe_exit(sym, now, "carried over (missed flat)")
        self.state["carried"] = []
        for sym, pos in list(self.state["open"].items()):           # fix 3: a filled broker stop is booked, never "exited" again
            if pos.get("stop_id"):
                try:
                    so = self.b.order(pos["stop_id"])
                except Exception:
                    so = {}
                if so.get("status") == "filled":
                    self.record(sym, pos, float(so.get("filled_avg_price") or pos["stop"]), pos["qty"], "hard_stop", now)
        if m >= flat_m:
            for sym in list(self.state["open"]):
                self.safe_exit(sym, now, "flatten_1545")
            self.save()
            return
        if m % 5 == 0 and m >= self.cfg["start_bar"] * 5:            # a 5-min bar just closed
            self.on_bar(now, m)
        self.save()

    def on_bar(self, now, m):
        st, cfg = self.state, self.cfg
        k_last = m // 5 - 1
        from .universe import STOCKS
        can_enter = (not cfg.get("paused") and now.date().isoformat() >= cfg.get("start_date", "") and st["n_new"] < cfg["max_new"]
                     and len(st["open"]) < cfg["max_open"] and st.get("realized_R", 0.0) > -cfg["daily_cap_pct"] / cfg["risk_pct"])
        names = sorted(set(STOCKS)) if can_enter else sorted(st["open"])
        held = self.b.positions() if can_enter else {}
        for sym in names:
            try:
                b = self.b5(sym, now)
                if b is None:
                    continue
                f = features(b, self.flags(sym, now), cfg)
                last = f[(f["d"] == now.date()) & (f["k"] == k_last)]
                if not len(last):
                    continue
                r = last.iloc[-1]
            except Exception as exc:
                self.journal("error", now, where="bar", symbol=sym, error=str(exc)[:160])
                continue
            pos = st["open"].get(sym)
            if pos:                                                    # exits on the closed bar (fix 1: once per bar)
                side, e, R0 = pos["side"], pos["entry"], pos["R0"]
                c = float(r["c"])
                pos["peak"] = max(pos.get("peak", e), c) if side == 1 else min(pos.get("peak", e), c)
                below = side * (c - float(r["vwap"])) < 0 and side * (c - float(r["sma20"])) < 0
                pos["below"] = pos.get("below", 0) + 1 if below else 0
                pr = side * (pos["peak"] - e) / R0
                if pos["below"] >= 3:
                    self.safe_exit(sym, now, "3candle")
                elif pr >= cfg["trail_start"] and side * (c - (e + side * (pr - cfg["trail_gap"]) * R0)) < 0:
                    self.safe_exit(sym, now, "virtual_trail")
                continue
            if not can_enter or sym in st["open"] or held.get(sym) or st["n_new"] >= cfg["max_new"] or len(st["open"]) >= cfg["max_open"]:
                continue
            side = 1 if r["long"] else -1 if r["short"] else 0
            if not side or not (float(r["atr"]) > 0):
                continue
            if side == -1 and not self.b.shortable(sym):
                self.journal("signal", now, symbol=sym, strategy="trend", side=-1, taken=False, skip_reason="not easy to borrow")
                continue
            try:
                bid, ask = (float(x) for x in self.b.quote(sym)[:2])
            except Exception:
                continue
            e = ask if side == 1 else bid
            R0 = cfg["stop_atr"] * float(r["atr"])
            self.enter(sym, "trend", side, {"entry": e, "stop": e - side * R0, "s": m - 1}, now, m)
            if sym in st["open"]:
                p = st["open"][sym]
                p.update(R0=abs(p["entry"] - p["stop"]), peak=p["entry"], below=0, angle=round(float(r["angle"]), 3),
                         relvol=round(float(r["relvol"]), 2))


def summary(state_dir="state/alpaca_trendopt") -> list[str]:
    from .vwaplive import summary as _s
    return [x.replace("VWAP / ORB day-trade sleeve (paper, 0.05% risk)", "Optimized Trend bot (paper, 0.5% risk)") for x in _s(state_dir)]
