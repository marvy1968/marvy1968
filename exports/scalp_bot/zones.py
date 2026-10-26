"""Weinstein / Velez 365-day zone study (research + scanner).

    python zones.py --start 2017-01-01 --end 2021-12-31      # discovery backtest
    python zones.py --scan                                    # today's Stage 2 stocks in their buy zones
    python zones.py --zones AAPL NVDA CELH                    # print the zones for specific symbols

WHETHER (Weinstein, last COMPLETED week): Stage 2 = weekly close > 30-week SMA, SMA not falling, Mansfield RS > 0.
WHERE (365 daily candles): volume-at-price profile of the last 365 sessions (typical price x volume, 50 bins,
  3-bin smoothing). Nodes = local peaks holding >= 3% of the volume.
  BUY ZONE  = highest node below price that is >= 97% of the 30-week SMA (else the SMA itself), +/- 0.5 ATR(14).
  TP ZONE   = lowest node above 1.02 x price (overhead supply). None -> "open air": target = 2R.
WHEN (Velez, daily): a bullish tail bar, 180 or elephant bar whose low touches the buy zone and closes at or
  above it -> stop-entry 1 cent above that bar's high, live 3 sessions.
STOP  = 1 cent below min(bar low, zone low); skip if more than 12% away. Skip if TP1 < 1.5R away.
EXITS = half at TP1 (limit), then stop to breakeven; rest on a weekly close below the 30-week SMA. Stops daily.
SIZE  = 1% of equity at risk, capped at 10% of equity, max 20 positions. Long only.
"""
import argparse
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

import weinstein as ws

LOOKBACK, BINS, NODE_SHARE = 365, 50, 0.03
ZONE_ATR, MA_TOL, TP_GAP = 0.5, 0.97, 1.02
MAX_STOP_PCT, MIN_RR, OPEN_AIR_R = 0.12, 1.5, 2.0
TRIGGER_DAYS, TP1_FRACTION = 3, 0.5
RISK_PCT, MAX_POS_PCT, MAX_POSITIONS = 0.01, 0.10, 20
DISCOVERY_END = date(2021, 12, 31)


# ---------------- zones from 365 daily candles ----------------
def profile_nodes(high, low, close, volume, bins=BINS, share=NODE_SHARE):
    """Volume-at-price nodes (price levels) from a window of daily candles."""
    lo, hi = np.nanmin(low), np.nanmax(high)
    if not np.isfinite(lo) or hi <= lo:
        return np.array([])
    tp = (high + low + close) / 3
    ok = np.isfinite(tp) & np.isfinite(volume)
    hist, edges = np.histogram(tp[ok], bins=bins, range=(lo, hi), weights=volume[ok])
    if hist.sum() <= 0:
        return np.array([])
    sm = np.convolve(hist, np.ones(3) / 3, mode="same")
    frac = sm / sm.sum()
    centers = (edges[:-1] + edges[1:]) / 2
    peak = (frac >= np.roll(frac, 1)) & (frac >= np.roll(frac, -1)) & (frac >= share)
    peak[0] = peak[0] and frac[0] >= frac[1]
    peak[-1] = peak[-1] and frac[-1] >= frac[-2]
    return centers[peak]


def zones_at(d, i, ma30, atr):
    """Zones using the 365 candles ending at row i (inclusive). Returns dict or None."""
    j0 = max(0, i - LOOKBACK + 1)
    h, l, c, v = (d[k].to_numpy()[j0:i + 1] for k in ("high", "low", "close", "volume"))
    if len(c) < 200:
        return None
    px = c[-1]
    nodes = profile_nodes(h, l, c, v)
    below = nodes[(nodes < px) & (nodes >= MA_TOL * ma30)]
    support = below.max() if len(below) else ma30
    above = nodes[nodes > TP_GAP * px]
    return dict(support=support, zone_lo=support - ZONE_ATR * atr, zone_hi=support + ZONE_ATR * atr,
                tp1=(above.min() if len(above) else np.nan), n_nodes=len(nodes),
                p10=np.percentile(c, 10), p50=np.percentile(c, 50), p90=np.percentile(c, 90),
                hi365=h.max(), lo365=l.min())


def _atr(d, n=14):
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _pattern(d, i, body_avg):
    """Velez entry bar on daily candle i: ('EB'|'TB'|'180', side) or (None, None)."""
    import velez
    a = {"open": d["open"].to_numpy(), "high": d["high"].to_numpy(), "low": d["low"].to_numpy(),
         "close": d["close"].to_numpy(), "vz_body_avg": body_avg}
    return velez.pattern(a, i)


def weekly_state(d, spy_w):
    """Weinstein stage per completed week + zones computed at each Stage-2 week's last session."""
    w = ws.weekly(d)
    f = ws.stage_frame(w, spy_w)
    f["stage2"] = (f["close"] > f["ma30"]) & f["slope_ok"] & (f["mansfield"] > 0)
    per = d.index.to_period("W-FRI")
    last_row = pd.Series(np.arange(len(d)), index=per).groupby(level=0).max()
    atr = _atr(d).to_numpy()
    z = {}
    for wk in f.index[f["stage2"].fillna(False).astype(bool)]:
        i = int(last_row.get(wk, -1))
        if i >= 0 and np.isfinite(atr[i]):
            zz = zones_at(d, i, float(f.at[wk, "ma30"]), float(atr[i]))
            if zz:
                z[wk] = zz
    return f, z


# ---------------- daily engine ----------------
def run(panel, spy_daily, start, end, cost_mult=1.0, equity0=100_000.0, universe="all", market_filter=False):
    import swing
    spy_w = ws.weekly(pd.DataFrame({"open": spy_daily, "high": spy_daily, "low": spy_daily,
                                    "close": spy_daily, "volume": 0.0}))["close"]
    mkt = ws.spy_stage2(spy_daily)
    syms = sorted(s for s, d in panel.items() if len(d) >= 260)
    O, H, L, C = (pd.DataFrame({s: panel[s][k] for s in syms}).sort_index() for k in ("open", "high", "low", "close"))
    adv, close = ws._liquid({s: panel[s] for s in syms})
    member = swing.membership(adv, close, large_rank=(swing.LARGE_CAP_RANK if universe == "midsmall" else 0),
                              min_adv=ws.MIN_ADV, min_price=ws.MIN_PRICE)
    dates = C.index
    per = dates.to_period("W-FRI")
    o, h, l, c = O.to_numpy(), H.to_numpy(), L.to_numpy(), C.to_numpy()
    a = adv.reindex(index=dates, columns=syms).to_numpy()
    mem = member.reindex(index=dates, columns=syms).fillna(False).to_numpy(bool)
    di0 = int(np.searchsorted(dates, pd.Timestamp(start)))
    di1 = int(np.searchsorted(dates, pd.Timestamp(end), side="right"))
    # per-symbol state, aligned to the global calendar
    import velez
    st, zn, pat_arr = {}, {}, {}
    for j, s in enumerate(syms):
        f, z = weekly_state(panel[s], spy_w)
        st[j], zn[j] = f, z
        pat_arr[j] = {"open": o[:, j], "high": h[:, j], "low": l[:, j], "close": c[:, j],
                      "vz_body_avg": pd.Series(np.abs(c[:, j] - o[:, j])).rolling(20).mean().shift(1).to_numpy()}

    cash, pos, pending, trades, curve, last = equity0, {}, {}, [], [], {}

    def sell(j, qty, px, di, why):
        nonlocal cash
        p = pos[j]
        cash += qty * px
        p["qty"] -= qty
        p["legs"].append((qty, px))
        if p["qty"] <= 0:
            pos.pop(j)
            tot = sum(q for q, _ in p["legs"])
            avg = sum(q * x for q, x in p["legs"]) / tot
            trades.append(dict(symbol=syms[j], entry_date=dates[p["di"]].date(), exit_date=dates[di].date(),
                               trigger=p["trigger"], stop0=p["stop0"], tp1=round(p["tp1"], 4),
                               entry=round(p["px"], 4), avg_exit=round(avg, 4), ret_pct=round((avg / p["px"] - 1) * 100, 3),
                               r=round((avg - p["px"]) / p["risk"], 3), days=di - p["di"], tp1_hit=p["tp1_done"],
                               open_air=p["open_air"], pattern=p["pat"], reason=why, cost_bps=p["bps"],
                               pnl=round(tot * (avg - p["px"]), 2)))

    for di in range(max(di0, 1), di1):
        prev_wk = per[di] - 1
        # 1) weekly-exit orders queued last session -> today's open
        for j in [k for k, p in pos.items() if p.get("exit_next")]:
            if np.isfinite(o[di, j]):
                sell(j, pos[j]["qty"], o[di, j] * (1 - pos[j]["bps"] / 1e4), di, "weekly_close_below_ma30")
        # 2) stop-entry orders
        eq = curve[-1][1] if curve else equity0
        for j in list(pending):
            sig = pending[j]
            if j in pos or di > sig["expire"] or len(pos) >= MAX_POSITIONS:
                pending.pop(j)
                continue
            if not (np.isfinite(h[di, j]) and h[di, j] >= sig["trigger"]):
                continue
            pending.pop(j)
            bps = ws.slip_bps(a[di, j], cost_mult)
            px = max(o[di, j], sig["trigger"]) * (1 + bps / 1e4)
            if px <= sig["stop"]:
                continue
            risk = px - sig["stop"]
            qty = int(min(RISK_PCT * eq / risk, MAX_POS_PCT * eq / px))
            if qty < 2 or qty * px > cash:
                continue
            tp1 = sig["tp1"] if np.isfinite(sig["tp1"]) else px + OPEN_AIR_R * risk
            cash -= qty * px
            pos[j] = dict(px=px, qty=qty, di=di, stop=sig["stop"], risk=risk, tp1=tp1, tp1_done=False,
                          open_air=not np.isfinite(sig["tp1"]), pat=sig["pat"], bps=bps, legs=[],
                          trigger=sig["trigger"], stop0=sig["stop"])
        # 3) intraday: stop first (pessimistic), then TP1
        for j in list(pos):
            p = pos[j]
            if not np.isfinite(l[di, j]):
                continue
            if l[di, j] <= p["stop"]:
                fill = p["stop"] if di == p["di"] else min(o[di, j], p["stop"])
                sell(j, p["qty"], fill * (1 - p["bps"] / 1e4), di, "breakeven_stop" if p["tp1_done"] else "stop")
                continue
            if not p["tp1_done"] and h[di, j] >= p["tp1"]:
                q = max(1, int(p["qty"] * TP1_FRACTION))
                fill = max(o[di, j], p["tp1"]) if di > p["di"] else p["tp1"]
                p["tp1_done"], p["stop"] = True, max(p["stop"], p["px"])
                sell(j, min(q, p["qty"]), fill, di, "tp1_then_rest")
        for j in pos:
            if np.isfinite(c[di, j]):
                last[j] = c[di, j]
        # 4) end of week: weekly close below the 30-week SMA -> sell the rest next open
        is_week_end = di + 1 >= len(dates) or per[di + 1] != per[di]
        if is_week_end:
            wk = per[di]
            for j, p in pos.items():
                f = st[j]
                if wk in f.index and bool(f.at[wk, "exit"]):
                    p["exit_next"] = True
        # 5) new Velez triggers inside the buy zone (Stage 2 from the last completed week)
        if len(pos) + len(pending) < MAX_POSITIONS and not (market_filter and not bool(mkt.get(prev_wk, False))):
            for j in range(len(syms)):
                if j in pos or j in pending or not mem[di, j]:
                    continue
                z = zn[j].get(prev_wk)
                if z is None or not np.isfinite(c[di, j]):
                    continue
                if not (l[di, j] <= z["zone_hi"] and c[di, j] >= z["zone_lo"]):
                    continue
                kind, side = velez.pattern(pat_arr[j], di)
                if side != 1:
                    continue
                trig = round(h[di, j] + 0.01, 2)
                stop = round(min(l[di, j], z["zone_lo"]) - 0.01, 2)
                if stop <= 0 or (trig - stop) / trig > MAX_STOP_PCT:
                    continue
                if np.isfinite(z["tp1"]) and (z["tp1"] - trig) < MIN_RR * (trig - stop):
                    continue
                pending[j] = dict(trigger=trig, stop=stop, tp1=z["tp1"], expire=di + TRIGGER_DAYS, pat=kind)
                if len(pos) + len(pending) >= MAX_POSITIONS:
                    break
        curve.append((dates[di], cash + sum(float(p["qty"]) * float(last.get(j, p["px"])) for j, p in pos.items())))
    for j in list(pos):
        sell(j, pos[j]["qty"], last.get(j, pos[j]["px"]) * (1 - pos[j]["bps"] / 1e4), di1 - 1, "period_end")
    return pd.DataFrame(trades), pd.Series(dict(curve), dtype=float)


def report(trades, eq, bench, out="zones_results"):
    import swing
    os.makedirs(out, exist_ok=True)
    print("\n================ WEINSTEIN / VELEZ 365-DAY ZONES ================")
    print("NOTE: universe = stocks listed TODAY (delisted names missing) -> survivorship bias flatters results.")
    if trades.empty:
        print("No trades.")
        return
    trades.to_csv(os.path.join(out, "trades.csv"), index=False)
    eq.to_csv(os.path.join(out, "equity.csv"))
    r, R = trades["ret_pct"], trades["r"]
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else float("inf")
    print(f"trades {len(trades)} | win {(r > 0).mean() * 100:.1f}% | avg {r.mean():+.2f}% | avg {R.mean():+.2f}R | "
          f"PF {pf:.2f} | TP1 hit {trades['tp1_hit'].mean() * 100:.0f}% | hold {trades['days'].mean():.0f}d | "
          f"t-stat {r.mean() / (r.std() / np.sqrt(len(r))):.2f}")
    rows = {"strategy": swing.perf(eq)}
    for name, s in bench.items():
        s = s.reindex(eq.index).ffill()
        rows[f"buy & hold {name}"] = swing.perf(s / s.iloc[0] * eq.iloc[0])
    print(pd.DataFrame(rows).T.to_string())
    y = pd.to_datetime(trades["entry_date"]).dt.year
    yrs = sorted(y.unique())
    cut = yrs[int(len(yrs) * 2 / 3)] if len(yrs) >= 3 else yrs[-1]
    for label, m in (("first 2/3 (pick)", y < cut), (f"from {cut} (check)", y >= cut)):
        if m.any():
            print(f"  {label:<18} trades {m.sum():>5}  avg {r[m].mean():+.2f}%  avg {R[m].mean():+.2f}R")
    for col in ("reason", "pattern", "open_air"):
        print(f"\n--- by {col} ---")
        print(trades.groupby(col)[["ret_pct", "r"]].agg(["count", "mean"]).round(3).to_string())
    print(f"\nFiles written to {out}/")


def show_zones(panel, spy_daily, symbols=None, only_setups=False, top=30):
    spy_w = ws.weekly(pd.DataFrame({"open": spy_daily, "high": spy_daily, "low": spy_daily,
                                    "close": spy_daily, "volume": 0.0}))["close"]
    rows = []
    for s in (symbols or sorted(panel)):
        d = panel.get(s)
        if d is None or len(d) < 260:
            continue
        w = ws.weekly(d)
        f = ws.stage_frame(w, spy_w)
        today_wk = d.index[-1].to_period("W-FRI")
        done = f.index[f.index < today_wk]
        if not len(done):
            continue
        wk = done[-1]
        stage2 = bool((f.at[wk, "close"] > f.at[wk, "ma30"]) and f.at[wk, "slope_ok"] and f.at[wk, "mansfield"] > 0)
        i_wk = int(np.flatnonzero(d.index.to_period("W-FRI") == wk)[-1])     # zones as of the completed week
        atr = float(_atr(d).iloc[i_wk])
        z = zones_at(d, i_wk, float(f.at[wk, "ma30"]), atr)
        if z is None:
            continue
        body = (d["close"] - d["open"]).abs().rolling(20).mean().shift(1).to_numpy()
        kind, side = _pattern(d, len(d) - 1, body)
        px = float(d["close"].iloc[-1])
        in_zone = d["low"].iloc[-1] <= z["zone_hi"] and px >= z["zone_lo"]
        setup = stage2 and in_zone and side == 1
        if only_setups and not setup:
            continue
        rows.append(dict(symbol=s, close=round(px, 2), stage2=stage2, ma30w=round(float(f.at[wk, "ma30"]), 2),
                         buy_zone=f"{z['zone_lo']:.2f}-{z['zone_hi']:.2f}",
                         tp1=("open air (2R)" if not np.isfinite(z["tp1"]) else round(z["tp1"], 2)),
                         p10_p50_p90=f"{z['p10']:.2f}/{z['p50']:.2f}/{z['p90']:.2f}",
                         hi365=round(z["hi365"], 2), in_zone=in_zone, bar=(kind if side == 1 else "-"),
                         SETUP="YES" if setup else ""))
    print(f"\n=== 365-DAY ZONES as of {max(d.index[-1] for d in panel.values()).date()} (research output, "
          f"not a recommendation) ===")
    if not rows:
        print("nothing to show" + (" - no setups today" if only_setups else ""))
        return
    print(pd.DataFrame(rows).head(top).to_string(index=False))


def main():
    import swing
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--zones", nargs="+")
    ap.add_argument("--universe", choices=["all", "midsmall"], default="all")
    ap.add_argument("--universe-file")
    ap.add_argument("--cost-mult", type=float, default=1.0)
    ap.add_argument("--use-holdout", action="store_true")
    ap.add_argument("--market-filter", action="store_true", help="no new longs unless SPY is in Stage 2")
    a = ap.parse_args()
    today = date.today()
    if a.zones or a.scan:
        syms = a.zones or swing._symbols(a.universe_file)
        panel = swing.load_panel(sorted(syms), today - timedelta(days=800), today)
        spy = swing.load_panel(["SPY"], today - timedelta(days=800), today)["SPY"]["close"]
        show_zones(panel, spy, symbols=a.zones, only_setups=a.scan)
        return
    if not (a.start and a.end):
        raise SystemExit("give --start and --end, --scan, or --zones SYMBOLS")
    if pd.Timestamp(a.end).date() > DISCOVERY_END and not a.use_holdout:
        raise SystemExit(f"--end is after {DISCOVERY_END}: that data is HELD OUT.")
    start, end = pd.Timestamp(a.start).date(), pd.Timestamp(a.end).date()
    syms = swing._symbols(a.universe_file)
    panel = swing.load_panel(syms, start - timedelta(days=420), end + timedelta(days=1))
    bench = {}
    for b in ("SPY", "IWM", "IJH"):
        p = swing.load_panel([b], start - timedelta(days=420), end + timedelta(days=1))
        if b in p:
            bench[b] = p[b]["close"]
    trades, eq = run(panel, bench["SPY"], start, end, a.cost_mult, universe=a.universe, market_filter=a.market_filter)
    if a.market_filter:
        print("VARIANT: market filter ON (new longs only while SPY is in Stage 2)")
    report(trades, eq, {k: v.loc[pd.Timestamp(start):] for k, v in bench.items()},
           out="zones_results_mf" if a.market_filter else "zones_results")


if __name__ == "__main__":
    main()
