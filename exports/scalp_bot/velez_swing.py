"""Velez SWING method (Pristine Buy Setup, daily) and a Weinstein-hybrid version. Research only.

    python velez_swing.py --start 2017-01-01 --end 2021-12-31     # both arms, discovery years
    python velez_swing.py --scan                                   # today's setups (both arms)

ARM A  Velez swing (Pristine Buy Setup, per his swing course):
  trend   rising 20-day SMA, rising 40-day SMA, 20 above 40   ("rising" = higher than 3 sessions ago)
  setup   3 consecutive lower highs AND lower lows ending on the signal day (a pullback in the uptrend)
  entry   buy stop 5 cents above the signal day's high, valid the next session only
  stop    5 cents below the signal day's low
  target  the swing high the pullback started from (sell-limit); none if price is already above it
  time    sell at the close of the 5th session held (Velez's 2-5 day swing)
ARM B  Weinstein hybrid: Arm A + the last completed week in Weinstein Stage 2
        (weekly close > rising 30-week SMA, Mansfield RS > 0)
SIZE 1% of equity at risk, capped at 10% of equity, max 20 positions. Long only. Costs by liquidity.
Liquidity: price >= $5, 20-day dollar volume >= $5M. Two arms -> each needs t >= 2.5 to count.
"""
import argparse
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

import weinstein as ws

TICK = 0.05
HOLD_DAYS = 5
SLOPE_DAYS = 3
RISK_PCT, MAX_POS_PCT, MAX_POSITIONS = 0.01, 0.10, 20
FILL_RULE = "pessimistic"   # same-bar ambiguity: "pessimistic" (stop assumed first) or "candle" (OHLC path rule)
DISCOVERY_END = date(2021, 12, 31)


def pbs_frame(d):
    """Signal-day features, all from the signal day and earlier."""
    c, h, l = d["close"], d["high"], d["low"]
    out = pd.DataFrame(index=d.index)
    s20, s40 = c.rolling(20).mean(), c.rolling(40).mean()
    out["trend"] = (s20 > s20.shift(SLOPE_DAYS)) & (s40 > s40.shift(SLOPE_DAYS)) & (s20 > s40)
    lh = (h < h.shift(1)) & (h.shift(1) < h.shift(2)) & (h.shift(2) < h.shift(3))
    ll = (l < l.shift(1)) & (l.shift(1) < l.shift(2)) & (l.shift(2) < l.shift(3))
    out["pbs"] = lh & ll
    out["setup"] = out["trend"] & out["pbs"]
    # swing high the pullback started from: highest high of the last 10 sessions before the signal day
    out["swing_high"] = h.shift(1).rolling(10).max()
    out["trigger"] = (h + TICK).round(2)
    out["stop"] = (l - TICK).round(2)
    return out


def stage2_daily(d, spy_w):
    """Weinstein Stage 2 of the last COMPLETED week, mapped onto each day (causal)."""
    f = ws.stage_frame(ws.weekly(d), spy_w)
    st2 = ((f["close"] > f["ma30"]) & f["slope_ok"] & (f["mansfield"] > 0)).fillna(False)
    prev = d.index.to_period("W-FRI") - 1
    return pd.Series(st2.reindex(prev).fillna(False).to_numpy(bool), index=d.index)


def run(panel, spy_daily, start, end, hybrid=False, cost_mult=1.0, equity0=100_000.0, market_filter=False):
    import swing
    spy_w = ws.weekly(pd.DataFrame({"open": spy_daily, "high": spy_daily, "low": spy_daily,
                                    "close": spy_daily, "volume": 0.0}))["close"]
    mkt = ws.spy_stage2(spy_daily)
    syms = sorted(s for s, d in panel.items() if len(d) >= 60)
    O, H, L, C = (pd.DataFrame({s: panel[s][k] for s in syms}).sort_index() for k in ("open", "high", "low", "close"))
    adv, close = ws._liquid({s: panel[s] for s in syms})
    member = swing.membership(adv, close, large_rank=0, min_adv=ws.MIN_ADV, min_price=ws.MIN_PRICE)
    dates = C.index
    per = dates.to_period("W-FRI")
    o, h, l, c = O.to_numpy(), H.to_numpy(), L.to_numpy(), C.to_numpy()
    a = adv.reindex(index=dates, columns=syms).to_numpy()
    mem = member.reindex(index=dates, columns=syms).fillna(False).to_numpy(bool)
    F = {s: pbs_frame(panel[s]).reindex(dates) for s in syms}
    setup = np.column_stack([F[s]["setup"].fillna(False).to_numpy(bool) for s in syms])
    trig = np.column_stack([F[s]["trigger"].to_numpy() for s in syms])
    stp = np.column_stack([F[s]["stop"].to_numpy() for s in syms])
    swh = np.column_stack([F[s]["swing_high"].to_numpy() for s in syms])
    if hybrid:
        st2 = np.column_stack([stage2_daily(panel[s], spy_w).reindex(dates).fillna(False).to_numpy(bool) for s in syms])
        setup &= st2
    di0 = int(np.searchsorted(dates, pd.Timestamp(start)))
    di1 = int(np.searchsorted(dates, pd.Timestamp(end), side="right"))
    cash, pos, pend, trades, curve, last = equity0, {}, {}, [], [], {}

    def close_pos(j, px, di, why):
        nonlocal cash
        p = pos.pop(j)
        cash += p["qty"] * px
        trades.append(dict(symbol=syms[j], signal_date=dates[p["sig"]].date(), entry_date=dates[p["di"]].date(),
                           exit_date=dates[di].date(), trigger=p["trigger"], stop=p["stop"],
                           target=(p["target"] if np.isfinite(p["target"]) else None),
                           entry=round(p["px"], 4), exit=round(px, 4), ret_pct=round((px / p["px"] - 1) * 100, 3),
                           r=round((px - p["px"]) / p["risk"], 3), days=di - p["di"] + 1, reason=why,
                           cost_bps=p["bps"], pnl=round(p["qty"] * (px - p["px"]), 2)))

    for di in range(max(di0, 1), di1):
        eq = curve[-1][1] if curve else equity0
        # 1) yesterday's setups: buy stop above the signal day's high, today only
        for j, sg in list(pend.items()):
            pend.pop(j)
            if j in pos or len(pos) >= MAX_POSITIONS or not (np.isfinite(h[di, j]) and h[di, j] >= sg["trigger"]):
                continue
            bps = ws.slip_bps(a[di, j], cost_mult)
            px = max(o[di, j], sg["trigger"]) * (1 + bps / 1e4)
            if px <= sg["stop"]:
                continue
            risk = px - sg["stop"]
            qty = int(min(RISK_PCT * eq / risk, MAX_POS_PCT * eq / px))
            if qty < 1 or qty * px > cash:
                continue
            cash -= qty * px
            tgt = round(float(sg["target"]), 2) if np.isfinite(sg["target"]) and sg["target"] > px else np.nan   # limit in cents
            pos[j] = dict(px=px, qty=qty, di=di, sig=sg["sig"], stop=sg["stop"], trigger=sg["trigger"],
                          target=tgt, risk=risk, bps=bps)
        # 2) intraday, using the candle to infer the order of the day's moves (standard OHLC path rule):
        #    up day (close > open): open -> low -> high -> close;  down day: open -> high -> low -> close.
        for j in list(pos):
            p = pos[j]
            if not np.isfinite(l[di, j]):
                continue
            up_day = (c[di, j] > o[di, j]) if FILL_RULE == "candle" else False   # pessimistic: treat as stop-first
            hit_stop = l[di, j] <= p["stop"]
            hit_tgt = np.isfinite(p["target"]) and h[di, j] >= p["target"]
            if di == p["di"]:
                # entry day: we bought on the way UP through the trigger. On an up day the low came first,
                # so the stop only counts if price fell back below it after the high - which an up-day
                # close above the open rules out for a stop below the trigger. On a down day the high
                # (our entry) came first, so a low below the stop does count.
                if hit_stop and not up_day:
                    close_pos(j, p["stop"] * (1 - p["bps"] / 1e4), di, "stop")
                elif hit_tgt:
                    close_pos(j, p["target"], di, "target")
                continue
            if hit_stop and hit_tgt:
                if up_day or FILL_RULE == "pessimistic":        # low first (or unknown) -> stopped
                    close_pos(j, min(o[di, j], p["stop"]) * (1 - p["bps"] / 1e4), di, "stop")
                else:             # high first -> target
                    close_pos(j, max(o[di, j], p["target"]), di, "target")
            elif hit_stop:
                close_pos(j, min(o[di, j], p["stop"]) * (1 - p["bps"] / 1e4), di, "stop")
            elif hit_tgt:
                close_pos(j, max(o[di, j], p["target"]), di, "target")
        # 3) time exit at the close of the 5th session held
        for j in list(pos):
            if np.isfinite(c[di, j]):
                last[j] = c[di, j]
                if di - pos[j]["di"] + 1 >= HOLD_DAYS:
                    close_pos(j, c[di, j] * (1 - pos[j]["bps"] / 1e4), di, "day5_close")
        # 4) today's setups -> tomorrow's buy stops
        if not (market_filter and not bool(mkt.get(per[di] - 1, False))):
            cand = [j for j in np.flatnonzero(setup[di] & mem[di]) if j not in pos]
            room = MAX_POSITIONS - len(pos)
            for j in cand[:max(room, 0)]:
                pend[j] = dict(trigger=trig[di, j], stop=stp[di, j], target=swh[di, j], sig=di)
        curve.append((dates[di], cash + sum(p["qty"] * last.get(j, p["px"]) for j, p in pos.items())))
    for j in list(pos):
        close_pos(j, last.get(j, pos[j]["px"]) * (1 - pos[j]["bps"] / 1e4), di1 - 1, "period_end")
    return pd.DataFrame(trades), pd.Series(dict(curve), dtype=float)


def _row(name, tr, eq):
    import swing
    if tr.empty:
        return dict(arm=name, trades=0)
    r, R = tr["ret_pct"], tr["r"]
    y = pd.to_datetime(tr["entry_date"]).dt.year
    yrs = sorted(y.unique())
    cut = yrs[int(len(yrs) * 2 / 3)] if len(yrs) >= 3 else yrs[-1]
    p = swing.perf(eq)
    return dict(arm=name, trades=len(tr), win_pct=round((r > 0).mean() * 100, 1), avg_pct=round(r.mean(), 3),
                avg_R=round(R.mean(), 3), t_stat=round(r.mean() / (r.std() / np.sqrt(len(r))), 2),
                pick_avg_R=round(R[y < cut].mean(), 3), check_avg_R=round(R[y >= cut].mean(), 3),
                cagr_pct=p["cagr_pct"], max_dd_pct=p["max_dd_pct"], sharpe=p["sharpe"])


def report(results, bench, start, out="velez_swing_results"):
    import swing
    os.makedirs(out, exist_ok=True)
    print("\n================ VELEZ SWING (Pristine Buy Setup) vs WEINSTEIN HYBRID ================")
    print("NOTE: universe = stocks listed TODAY (delisted names missing) -> survivorship bias flatters results.")
    rows = []
    for name, (tr, eq) in results.items():
        rows.append(_row(name, tr, eq))
        if not tr.empty:
            tr.to_csv(os.path.join(out, f"trades_{name.split()[0]}.csv"), index=False)
    for b, s in bench.items():
        p = swing.perf(s.loc[pd.Timestamp(start):])
        rows.append(dict(arm=f"buy & hold {b}", cagr_pct=p["cagr_pct"], max_dd_pct=p["max_dd_pct"], sharpe=p["sharpe"]))
    t = pd.DataFrame(rows).set_index("arm")
    print(t.to_string())
    t.to_csv(os.path.join(out, "arms.csv"))
    for name, (tr, _) in results.items():
        if not tr.empty:
            print(f"\n--- {name}: by exit reason ---")
            print(tr.groupby("reason")[["ret_pct", "r"]].agg(["count", "mean"]).round(3).to_string())
    for name, (tr, _) in results.items():
        if tr.empty:
            continue
        r = tr["ret_pct"]
        y = pd.to_datetime(tr["entry_date"]).dt.year
        yrs = sorted(y.unique())
        cut = yrs[int(len(yrs) * 2 / 3)] if len(yrs) >= 3 else yrs[-1]
        tstat = r.mean() / (r.std() / np.sqrt(len(r)))
        ok = tstat >= 2.5 and tr.loc[y < cut, "r"].mean() > 0 and tr.loc[y >= cut, "r"].mean() > 0
        print(f"\nVERDICT {name}: {'PASS' if ok else 'FAIL'} (needs t >= 2.5 and both halves positive; t = {tstat:.2f})")
    print(f"\nFiles written to {out}/")


def scan(panel, spy_daily, top=30):
    spy_w = ws.weekly(pd.DataFrame({"open": spy_daily, "high": spy_daily, "low": spy_daily,
                                    "close": spy_daily, "volume": 0.0}))["close"]
    rows = []
    for s, d in panel.items():
        if len(d) < 260:
            continue
        f = pbs_frame(d).iloc[-1]
        adv = float((d["close"] * d["volume"]).rolling(20).mean().iloc[-1])
        if not (bool(f["setup"]) and adv >= ws.MIN_ADV and d["close"].iloc[-1] >= ws.MIN_PRICE):
            continue
        st2 = bool(stage2_daily(d, spy_w).iloc[-1])
        rows.append(dict(symbol=s, close=round(d["close"].iloc[-1], 2), buy_above=f["trigger"], stop=f["stop"],
                         target=round(f["swing_high"], 2), risk_pct=round((f["trigger"] - f["stop"]) / f["trigger"] * 100, 1),
                         weinstein_stage2=st2))
    asof = max(d.index[-1] for d in panel.values())
    print(f"\n=== VELEZ SWING SETUPS as of {asof.date()} - buy stop valid next session only (research output) ===")
    if not rows:
        print("no setups")
        return
    print(pd.DataFrame(rows).sort_values(["weinstein_stage2", "risk_pct"], ascending=[False, True]).head(top)
          .to_string(index=False))


def main():
    import swing
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--universe-file")
    ap.add_argument("--cost-mult", type=float, default=1.0)
    ap.add_argument("--market-filter", action="store_true")
    ap.add_argument("--use-holdout", action="store_true")
    a = ap.parse_args()
    today = date.today()
    if a.scan:
        syms = swing._symbols(a.universe_file)
        panel = swing.load_panel(syms, today - timedelta(days=800), today)
        spy = swing.load_panel(["SPY"], today - timedelta(days=800), today)["SPY"]["close"]
        scan(panel, spy)
        return
    if not (a.start and a.end):
        raise SystemExit("give --start and --end, or --scan")
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
    res = {}
    for name, hyb in (("A velez_swing", False), ("B weinstein_hybrid", True)):
        print(f"running {name} ...")
        res[name] = run(panel, bench["SPY"], start, end, hybrid=hyb, cost_mult=a.cost_mult,
                        market_filter=a.market_filter)
    report(res, bench, start)


if __name__ == "__main__":
    main()
