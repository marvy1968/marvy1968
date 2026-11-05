"""Weinstein Stage 2 breakout module: backtest + scanner + paper swing bot.

    python weinstein.py --start 2017-01-01 --end 2021-12-31      # discovery backtest
    python weinstein.py --bot                                      # plan only (dry run, no orders)
    python weinstein.py --bot --submit                             # paper orders (paper account only)

RULES (weekly bars, decided at the week's last close, acted on at the next open)
  BUY a Stage 2 breakout when ALL hold:
    - weekly close above the 30-week SMA, and the SMA is not falling (>= its value 4 weeks ago)
    - weekly close above the highest high of the prior 10 weeks (breakout from a base)
    - weekly volume >= 2x the average of the prior 10 weeks
    - Mansfield relative strength vs SPY > 0 (stock/SPY ratio above its 52-week average)
    - price >= $5, 20-day average dollar volume >= $5M
  INITIAL STOP 1% below the 10-week base low (skip the trade if that is more than 20% away).
  SELL when a weekly close is below the 30-week SMA (next open), or the stop is hit (checked daily).
  SIZE: risk 1% of equity per trade (entry - stop), capped at 10% of equity per position, max 20 positions.
"""
import argparse
import json
import os
import time
import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd

MA_WEEKS, SLOPE_WEEKS, BASE_WEEKS, VOL_MULT, RS_WEEKS = 30, 4, 10, 2.0, 52
STOP_BUFFER, MAX_STOP_PCT = 0.01, 0.20
RISK_PCT, MAX_POS_PCT, MAX_POSITIONS = 0.01, 0.10, 20
MIN_PRICE, MIN_ADV = 5.0, 5e6
DISCOVERY_END = date(2021, 12, 31)
COST_TIERS = [(50e6, 5.0), (20e6, 10.0), (0.0, 20.0)]
STATE_FILE = "weinstein_state.json"
STOP_RENEW_DAYS = 80
PREFIX = "WS"


# ---------------- weekly bars and signals (causal: a week is used only after it closes) ----------------
def weekly(d):
    """Daily OHLCV (date index) -> weekly bars indexed by week period (W-FRI)."""
    p = d.index.to_period("W-FRI")
    g = d.groupby(p)
    return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                         "close": g["close"].last(), "volume": g["volume"].sum()})


def stage_frame(w, bench_close_w):
    c, h, l, v = w["close"], w["high"], w["low"], w["volume"]
    out = pd.DataFrame(index=w.index)
    ma = c.rolling(MA_WEEKS).mean()
    out["ma30"] = ma
    out["slope_ok"] = ma >= ma.shift(SLOPE_WEEKS)
    out["base_high"] = h.shift(1).rolling(BASE_WEEKS).max()
    out["base_low"] = l.shift(1).rolling(BASE_WEEKS).min()
    out["vol_ratio"] = v / v.shift(1).rolling(BASE_WEEKS).mean()
    rs = c / bench_close_w.reindex(w.index)
    out["mansfield"] = (rs / rs.rolling(RS_WEEKS).mean() - 1) * 100
    out["stop"] = out["base_low"] * (1 - STOP_BUFFER)
    out["stop_pct"] = 1 - out["stop"] / c
    out["breakout"] = ((c > ma) & out["slope_ok"] & (c > out["base_high"]) & (out["vol_ratio"] >= VOL_MULT)
                       & (out["mansfield"] > 0) & (out["stop_pct"] > 0) & (out["stop_pct"] <= MAX_STOP_PCT))
    out["exit"] = c < ma
    out["close"] = c
    return out


def spy_stage2(bench_daily):
    """Weinstein's market check: is SPY itself in Stage 2 (above a rising 30-week SMA)?"""
    bw = weekly(pd.DataFrame({"open": bench_daily, "high": bench_daily, "low": bench_daily,
                              "close": bench_daily, "volume": 0.0}))["close"]
    ma = bw.rolling(MA_WEEKS).mean()
    return ((bw > ma) & (ma >= ma.shift(SLOPE_WEEKS))).fillna(False)


def slip_bps(adv, mult=1.0):
    for floor, bps in COST_TIERS:
        if adv >= floor:
            return bps * mult
    return COST_TIERS[-1][1] * mult


def _liquid(panel):
    adv = pd.DataFrame({s: (d["close"] * d["volume"]).rolling(20).mean() for s, d in panel.items()}).sort_index()
    close = pd.DataFrame({s: d["close"] for s, d in panel.items()}).sort_index()
    return adv, close


def build_signals(panel, bench_daily):
    bw = weekly(pd.DataFrame({"open": bench_daily, "high": bench_daily, "low": bench_daily,
                              "close": bench_daily, "volume": 0.0}))["close"]
    frames = {s: stage_frame(weekly(d), bw) for s, d in panel.items() if len(d) >= 60}
    wide = {k: pd.DataFrame({s: f[k] for s, f in frames.items()}).sort_index()
            for k in ("breakout", "exit", "stop", "mansfield", "close", "ma30", "vol_ratio")}
    return frames, wide


# ---------------- backtest ----------------
def run(panel, bench_daily, start, end, cost_mult=1.0, equity0=100_000.0, universe="all", market_filter=False):
    import swing
    _, wide = build_signals(panel, bench_daily)
    mkt = spy_stage2(bench_daily)
    syms = sorted(wide["breakout"].columns)
    O, H, L, C = (pd.DataFrame({s: panel[s][k] for s in syms}).sort_index() for k in ("open", "high", "low", "close"))
    adv, close = _liquid({s: panel[s] for s in syms})
    member = swing.membership(adv, close, large_rank=(swing.LARGE_CAP_RANK if universe == "midsmall" else 0),
                              min_adv=MIN_ADV, min_price=MIN_PRICE)
    dates = C.index
    per = dates.to_period("W-FRI")
    week_end = pd.Series(dates, index=dates).groupby(per).transform("max").to_numpy() == dates.to_numpy()
    o, h, l, c = O.to_numpy(), H.to_numpy(), L.to_numpy(), C.to_numpy()
    a = adv.reindex(index=dates, columns=syms).to_numpy()
    mem = member.reindex(index=dates, columns=syms).fillna(False).to_numpy(bool)
    W = {k: v.reindex(columns=syms) for k, v in wide.items()}
    for k in ("breakout", "exit"):
        W[k] = W[k].astype("boolean").fillna(False).astype(bool)
    di0 = int(np.searchsorted(dates, pd.Timestamp(start)))
    di1 = int(np.searchsorted(dates, pd.Timestamp(end), side="right"))
    cash, pos, pend_in, pend_out, trades, curve, last = equity0, {}, [], {}, [], [], {}

    def close_pos(j, px, di, why):
        nonlocal cash
        p = pos.pop(j)
        cash += p["qty"] * px
        trades.append(dict(symbol=syms[j], signal_week=str(p["week"]), entry_date=dates[p["di"]].date(),
                           exit_date=dates[di].date(), entry=round(p["px"], 4), exit=round(px, 4),
                           ret_pct=round((px / p["px"] - 1) * 100, 3), r=round((px - p["px"]) / p["risk"], 3),
                           days=di - p["di"], reason=why, cost_bps=p["bps"], pnl=round(p["qty"] * (px - p["px"]), 2)))

    for di in range(max(di0, 1), di1):
        for j in list(pend_out):
            if j not in pos:
                pend_out.pop(j)
            elif np.isfinite(o[di, j]):
                close_pos(j, o[di, j] * (1 - pos[j]["bps"] / 1e4), di, pend_out.pop(j))
        eq = curve[-1][1] if curve else equity0
        for j, stop, wk, sadv in pend_in:
            if j in pos or len(pos) >= MAX_POSITIONS or not np.isfinite(o[di, j]):
                continue
            bps = slip_bps(sadv, cost_mult)
            px = o[di, j] * (1 + bps / 1e4)
            if px <= stop:
                continue                                   # gapped below the stop: skip
            qty = int(min(RISK_PCT * eq / (px - stop), MAX_POS_PCT * eq / px))
            if qty < 1 or qty * px > cash:
                continue
            cash -= qty * px
            pos[j] = dict(px=px, qty=qty, di=di, stop=stop, risk=px - stop, bps=bps, week=wk)
        pend_in = []
        for j in list(pos):
            if np.isfinite(l[di, j]) and l[di, j] <= pos[j]["stop"]:
                fill = pos[j]["stop"] if di == pos[j]["di"] else min(o[di, j], pos[j]["stop"])
                close_pos(j, fill * (1 - pos[j]["bps"] / 1e4), di, "stop")
        for j, p in pos.items():
            if np.isfinite(c[di, j]):
                last[j] = c[di, j]
        if week_end[di]:
            wk = per[di]
            if wk in W["exit"].index:
                ex = W["exit"].loc[wk].to_numpy()
                for j in pos:
                    if ex[j]:
                        pend_out[j] = "weekly_close_below_ma30"
                br = W["breakout"].loc[wk].to_numpy()
                st, ms = W["stop"].loc[wk].to_numpy(), W["mansfield"].loc[wk].to_numpy()
                cand = [j for j in range(len(syms)) if br[j] and mem[di, j] and j not in pos]
                if market_filter and not bool(mkt.get(wk, False)):
                    cand = []                                  # market not in Stage 2: no new longs
                cand.sort(key=lambda j: -ms[j])
                slots = MAX_POSITIONS - len(pos) + len(pend_out)
                pend_in = [(j, st[j], wk, a[di, j]) for j in cand[:max(slots, 0)]]
        curve.append((dates[di], cash + sum(p["qty"] * last.get(j, p["px"]) for j, p in pos.items())))
    for j in list(pos):
        close_pos(j, last.get(j, pos[j]["px"]) * (1 - pos[j]["bps"] / 1e4), di1 - 1, "period_end")
    return pd.DataFrame(trades), pd.Series(dict(curve), dtype=float)


def report(trades, eq, bench, out="weinstein_results"):
    import swing
    os.makedirs(out, exist_ok=True)
    print("\n================ WEINSTEIN STAGE 2 BREAKOUTS ================")
    print("NOTE: universe = stocks listed TODAY (delisted names missing) -> survivorship bias flatters results.")
    if trades.empty:
        print("No trades.")
        return
    trades.to_csv(os.path.join(out, "trades.csv"), index=False)
    eq.to_csv(os.path.join(out, "equity.csv"))
    r, R = trades["ret_pct"], trades["r"]
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else float("inf")
    print(f"trades {len(trades)} | win {(r > 0).mean() * 100:.1f}% | avg {r.mean():+.2f}% | avg {R.mean():+.2f}R "
          f"| profit factor {pf:.2f} | avg hold {trades['days'].mean():.0f} days | t-stat "
          f"{r.mean() / (r.std() / np.sqrt(len(r))):.2f} | best {R.max():+.1f}R")
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
    print("\n--- by exit reason ---")
    print(trades.groupby("reason")[["ret_pct", "r"]].agg(["count", "mean"]).round(3).to_string())
    print("\n--- by year ---")
    print(trades.groupby(y)["ret_pct"].agg(["count", "mean"]).round(3).to_string())
    print(f"\nFiles written to {out}/")


# ---------------- paper bot ----------------
def _last_completed_week(bench_daily, today):
    """Latest week whose final trading day is before today -> (period, last_day)."""
    d = bench_daily.index[bench_daily.index < pd.Timestamp(today)]
    per = d.to_period("W-FRI")
    cur = pd.Timestamp(today).to_period("W-FRI")
    done = per[per < cur]
    if not len(done):
        return None, None
    wk = done[-1]
    return wk, d[per == wk].max()


def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE))
        except Exception:   # noqa: BLE001
            os.replace(STATE_FILE, STATE_FILE + ".corrupt")
    return {"positions": {}, "attempted": {}}


def _save_state(st):
    tmp = STATE_FILE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE_FILE)


def plan(panel, bench_daily, today, equity, held, market_filter=False):
    """Decisions from the last COMPLETED week. Returns (week, fresh, exits, entries)."""
    frames, wide = build_signals(panel, bench_daily)
    wk, last_day = _last_completed_week(bench_daily, today)
    if wk is None:
        return None, False, [], []
    days_after = bench_daily.index[(bench_daily.index > last_day) & (bench_daily.index < pd.Timestamp(today))]
    fresh = len(days_after) == 0                     # today is the first session after that week
    adv, close = _liquid(panel)
    ex = [s for s in held if s in frames and wk in frames[s].index and frames[s].at[wk, "exit"] is not pd.NA
          and bool(frames[s].at[wk, "exit"])]
    rows = []
    for s, f in frames.items():
        if s in held or wk not in f.index or not (f.at[wk, "breakout"] is True or f.at[wk, "breakout"] == 1):
            continue
        px, a = float(f.at[wk, "close"]), float(adv[s].get(last_day, np.nan))
        if px < MIN_PRICE or not a >= MIN_ADV:
            continue
        stop = float(f.at[wk, "stop"])
        qty = int(min(RISK_PCT * equity / (px - stop), MAX_POS_PCT * equity / px))
        if qty >= 1:
            rows.append(dict(symbol=s, close=round(px, 2), stop=round(stop, 2),
                             stop_pct=round((1 - stop / px) * 100, 1), vol_x=round(float(f.at[wk, "vol_ratio"]), 1),
                             mansfield=round(float(f.at[wk, "mansfield"]), 1), qty=qty))
    rows.sort(key=lambda r: -r["mansfield"])
    if market_filter and not bool(spy_stage2(bench_daily).get(wk, False)):
        print("market filter: SPY is not in Stage 2 - no new entries this week")
        rows = []
    return wk, fresh, ex, rows[:max(MAX_POSITIONS - len(held) + len(ex), 0)]


def run_bot(tc, panel, bench_daily, submit=False, today=None, wait_fill_s=60, market_filter=False):
    import config
    from broker import call
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
    from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest, GetOrdersRequest
    if submit and not config.PAPER:
        raise SystemExit("weinstein bot is paper-only: APCA_PAPER must be true")
    today = today or date.today()
    st = _load_state()
    clock = call(tc.get_clock, what="clock")
    if submit and not clock.is_open:
        print("market closed - nothing submitted (run during market hours, e.g. 09:35 ET)")
        submit = False
    acct = call(tc.get_account, what="account")
    equity = float(acct.equity)
    broker_pos = {p.symbol: abs(float(p.qty)) for p in call(tc.get_all_positions, what="positions")}
    held = {s for s in st["positions"] if s in broker_pos}
    for s in list(st["positions"]):
        if s not in broker_pos:
            print(f"{s}: no longer held (stopped out or closed) - removed from state")
            st["positions"].pop(s)
    open_orders = call(tc.get_orders, filter=GetOrdersRequest(status=QueryOrderStatus.OPEN), what="orders")
    open_by_sym, mine = {}, {}
    for od in open_orders:
        open_by_sym.setdefault(od.symbol, []).append(od)
        if (od.client_order_id or "").startswith(PREFIX + "_"):
            mine.setdefault(od.symbol, []).append(od)          # only this bot's orders are ever cancelled

    wk, fresh, exits, entries = plan(panel, bench_daily, today, equity, held, market_filter)
    print(f"\n=== WEINSTEIN BOT {'(SUBMITTING - PAPER)' if submit else '(DRY RUN - no orders)'} ===")
    print(f"equity ${equity:,.0f} | held {sorted(held)} | last completed week {wk} | fresh signals today: {fresh}")

    # 1) exits: weekly close below the 30-week MA
    for s in exits:
        qty = min(broker_pos[s], float(st["positions"][s].get("qty", broker_pos[s])))
        print(f"EXIT  {s}: weekly close below 30-week MA -> sell {qty:g}")
        if submit:
            for od in mine.get(s, []):
                call(tc.cancel_order_by_id, od.id, what=f"cancel {s}")
            time.sleep(1)
            call(tc.submit_order, order_data=MarketOrderRequest(
                symbol=s, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                client_order_id=f"{PREFIX}_X_{s}_{uuid.uuid4().hex[:6]}"), what=f"exit {s}")
            st["positions"].pop(s, None)

    # 2) protective stops: every held position must have a live GTC stop.
    #    Alpaca cancels GTC orders 90 days after creation, so stops older than 80 days are renewed.
    now = pd.Timestamp.now(tz="UTC")
    for s in held - set(exits):
        for od in list(mine.get(s, [])):
            made = getattr(od, "created_at", None)
            if getattr(od, "stop_price", None) and made is not None and \
                    (now - pd.Timestamp(made).tz_convert("UTC")).days >= STOP_RENEW_DAYS:
                print(f"RENEW {s}: stop is {(now - pd.Timestamp(made).tz_convert('UTC')).days} days old "
                      f"(Alpaca cancels GTC at 90) -> re-placing")
                if submit:
                    call(tc.cancel_order_by_id, od.id, what=f"cancel old stop {s}")
                mine[s].remove(od)
        has_stop = any(getattr(od, "stop_price", None) for od in mine.get(s, []))
        if not has_stop:
            stp = st["positions"][s]["stop"]
            qty = min(broker_pos[s], float(st["positions"][s].get("qty", broker_pos[s])))
            print(f"STOP  {s}: no live stop found -> place GTC stop {stp}")
            if submit:
                call(tc.submit_order, order_data=StopOrderRequest(
                    symbol=s, qty=qty, side=OrderSide.SELL, stop_price=stp,
                    time_in_force=TimeInForce.GTC, client_order_id=f"{PREFIX}_S_{s}_{uuid.uuid4().hex[:6]}"),
                    what=f"stop {s}")

    # 3) entries: only on the first session after the signal week, once per symbol per week
    tried = set(st["attempted"].get(str(wk), []))
    if not fresh:
        print("no new entries today: this week's signals are no longer fresh (run on the first session of the week)")
        entries = []
    for e in entries:
        s = e["symbol"]
        if s in tried or s in broker_pos or s in open_by_sym:
            continue
        print(f"BUY   {s}: {e['qty']} sh ~${e['close']} stop {e['stop']} ({e['stop_pct']}% risk) "
              f"vol {e['vol_x']}x RS {e['mansfield']}")
        if not submit:
            continue
        st["attempted"].setdefault(str(wk), []).append(s)
        od = call(tc.submit_order, order_data=MarketOrderRequest(
            symbol=s, qty=e["qty"], side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            client_order_id=f"{PREFIX}_B_{s}_{uuid.uuid4().hex[:6]}"), what=f"buy {s}")
        filled, t0 = 0.0, time.time()
        while time.time() - t0 < wait_fill_s:
            o2 = call(tc.get_order_by_id, od.id, what=f"order {s}")
            filled = float(o2.filled_qty or 0)
            if str(getattr(o2.status, "value", o2.status)).lower() == "filled":
                break
            time.sleep(2)
        if filled > 0:
            call(tc.submit_order, order_data=StopOrderRequest(
                symbol=s, qty=filled, side=OrderSide.SELL, stop_price=e["stop"], time_in_force=TimeInForce.GTC,
                client_order_id=f"{PREFIX}_S_{s}_{uuid.uuid4().hex[:6]}"), what=f"stop {s}")
        st["positions"][s] = {"stop": e["stop"], "week": str(wk), "entry_ref": e["close"], "qty": filled or e["qty"]}
        print(f"      filled {filled:g} - GTC stop placed" if filled else "      not filled yet - stop added on next run")
    if submit:
        _save_state(st)
    return wk, fresh, exits, entries


def main():
    import swing
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--universe", choices=["all", "midsmall"], default="all")
    ap.add_argument("--universe-file")
    ap.add_argument("--cost-mult", type=float, default=1.0)
    ap.add_argument("--use-holdout", action="store_true")
    ap.add_argument("--bot", action="store_true", help="plan (dry run) today's actions")
    ap.add_argument("--submit", action="store_true", help="with --bot: place PAPER orders")
    ap.add_argument("--market-filter", action="store_true", help="no new longs unless SPY is in Stage 2")
    a = ap.parse_args()
    if not a.bot:
        if not (a.start and a.end):
            raise SystemExit("give --start and --end, or --bot")
        if pd.Timestamp(a.end).date() > DISCOVERY_END and not a.use_holdout:
            raise SystemExit(f"--end is after {DISCOVERY_END}: that data is HELD OUT.")
    syms = swing._symbols(a.universe_file)
    if a.bot:
        import config
        from alpaca.trading.client import TradingClient
        today = date.today()
        panel = swing.load_panel(syms, today - timedelta(days=800), today)
        spy = swing.load_panel(["SPY"], today - timedelta(days=800), today)["SPY"]["close"]
        run_bot(TradingClient(config.API_KEY, config.API_SECRET, paper=True), panel, spy, submit=a.submit,
                market_filter=a.market_filter)
        return
    start, end = pd.Timestamp(a.start).date(), pd.Timestamp(a.end).date()
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
           out="weinstein_results_mf" if a.market_filter else "weinstein_results")


if __name__ == "__main__":
    main()
