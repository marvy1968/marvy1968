"""Event-driven backtester that runs the SAME expert/regime/management code as the live bot.

python backtest.py --start 2026-06-01 --end 2026-08-31 --equity 100000 --slippage-bps 2

Fill model: signal on bar close -> market fill at NEXT bar open +/- slippage.
Stops/targets checked intrabar (if both hit in one bar, the stop is assumed first).
Expert/time/EOD exits decided on bar close -> filled at next bar open.
"""
import argparse
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

import config
from common import Position, allowed_dirs, check_management, confirm_dirs, session_limits, vol_filter_ok, zone_ok
from experts import build_experts
from indicators import NY, prepare_intraday, to_arrays
from regime import apply_intraday_overlay, build_heatmap, daily_frame, select_universe


def mod_of(t):
    tt = t.tz_convert(NY)
    return tt.hour * 60 + tt.minute - 570


def rotate(lst, k):
    k %= len(lst)
    return lst[k:] + lst[:k]


def bracket_hit(pos, o, h, l, slip):
    """Intrabar stop/target. Gaps fill at the open. If both are touched in one bar, assume the stop."""
    tag = "trail_stop" if pos.trailing else ("breakeven_stop" if pos.be_moved else "stop")
    if pos.side > 0:
        if l <= pos.stop:
            return min(o, pos.stop) * (1 - slip), tag
        if h >= pos.target:
            return max(o, pos.target), "target"
    else:
        if h >= pos.stop:
            return max(o, pos.stop) * (1 + slip), tag
        if l <= pos.target:
            return min(o, pos.target), "target"
    return None


def mark_to_market(equity, open_pos, last_px):
    """[FIX] Realized equity + unrealized P&L (what Alpaca's account equity reports live)."""
    return equity + sum(p.side * (last_px.get(s, p.entry) - p.entry) * p.qty for s, p in open_pos.items())


class Sim:
    def __init__(self, equity, slip_bps, fee_ps):
        self.equity, self.slip, self.fee = equity, slip_bps / 1e4, fee_ps
        self.trades, self.curve = [], []

    def close(self, pos, px, t, reason):
        pnl = pos.side * (px - pos.entry) * pos.qty - 2 * self.fee * pos.qty
        self.equity += pnl
        opened = pd.Timestamp(pos.opened_at)
        self.trades.append(dict(
            date=t.tz_convert(NY).date(), opened=opened.tz_convert(NY), closed=t.tz_convert(NY),
            expert=pos.expert, symbol=pos.symbol, sector=pos.sector, regime=pos.regime,
            side="LONG" if pos.side > 0 else "SHORT", qty=pos.qty, entry=round(pos.entry, 4),
            exit=round(px, 4), r=round(pos.side * (px - pos.entry) / pos.risk, 3) if pos.risk else 0,
            pnl=round(pnl, 2), hold_min=round((t - opened).total_seconds() / 60, 1),
            entry_reason=pos.reason, exit_reason=reason))
        self.curve.append((t, self.equity))


def run_backtest(arrays, sym_sector, frames, start, end, equity=100_000, slip_bps=2.0, fee_ps=0.0,
                 overlay=True, verbose=True, experts=None):
    etfs = {sec: spec["etf"] for sec, spec in config.SECTORS.items()}
    start_i, end_i = int(start.strftime("%Y%m%d")), int(end.strftime("%Y%m%d"))
    days = sorted({int(x) for s in sym_sector if s in arrays for x in np.unique(arrays[s]["session"])
                   if start_i <= x <= end_i})
    experts = experts or build_experts()
    by_id = {e.id: e for e in experts}
    sim = Sim(equity, slip_bps, fee_ps)
    regime_log = []
    symbols = [s for s in sym_sector if s in arrays]
    step = 0

    for day in days:
        ddate = date(day // 10000, day // 100 % 100, day % 100)
        for e in experts:
            e.new_day()
        heat = build_heatmap(frames, ddate)
        base = heat["regime"].to_dict()
        regimes = dict(base)
        for sec, r in heat.iterrows():
            regime_log.append(dict(date=ddate, sector=sec, etf=r["etf"], regime=r["regime"], score=r["score"]))

        idx, tl = {}, set()
        for s, a in arrays.items():
            w = np.flatnonzero(a["session"] == day)
            if len(w) == 0:
                continue
            ts = a["ts"][w]
            idx[s] = dict(zip(ts, w))
            if s in sym_sector:
                tl.update(ts)
        timeline = sorted(tl)
        close_mod = max(int(arrays[s]["mod"][max(m.values())]) for s, m in idx.items()) + 1
        last_entry_mod, flatten_mod = session_limits(close_mod)   # [FIX] half days
        last_px = {}

        open_pos, pending, exit_q = {}, {}, {}
        day_start_eq, halted = sim.equity, False

        for t in timeline:
            step += 1
            m = mod_of(t)
            if overlay and m % config.REGIME_REFRESH_MIN == 0:
                for sec, etf in etfs.items():
                    i = idx.get(etf, {}).get(t)
                    if i is not None:
                        regimes[sec] = apply_intraday_overlay(base[sec], arrays[etf], i)

            # 1) queued exits fill at this bar's open
            for sym in list(exit_q):
                i = idx.get(sym, {}).get(t)
                if i is None:
                    continue
                pos = open_pos.pop(sym)
                px = arrays[sym]["open"][i] * (1 - pos.side * sim.slip)
                sim.close(pos, px, t, exit_q.pop(sym))

            # 2) pending entries fill at this bar's open
            for sym in list(pending):
                i = idx.get(sym, {}).get(t)
                if i is None:
                    continue
                sig, sector, reg, t_sig = pending.pop(sym)
                if (t - t_sig).total_seconds() > config.PENDING_EXPIRY_S:
                    continue
                a = arrays[sym]
                if sig.trigger:   # stop-entry: fills only if this bar trades through the trigger
                    hit = a["high"][i] >= sig.trigger if sig.side > 0 else a["low"][i] <= sig.trigger
                    if not hit:
                        pending[sym] = (sig, sector, reg, t_sig)
                        continue
                    o_ = a["open"][i]
                    px0 = max(o_, sig.trigger) if sig.side > 0 else min(o_, sig.trigger)
                    fill = px0 * (1 + sig.side * sim.slip)
                else:
                    fill = a["open"][i] * (1 + sig.side * sim.slip)
                if sig.side * (fill - sig.stop) <= 0 or sig.side * (sig.target - fill) <= 0:
                    continue   # gapped through stop/target: skip
                qty = int(config.POSITION_PCT * sim.equity // fill)
                if qty < 1:
                    continue
                tgt = sig.target if not config.RUNNER_MODE else (1e12 if sig.side > 0 else 0.0)   # v6: no target
                open_pos[sym] = Position(sym, sig.expert, sector, sig.side, qty, fill, sig.stop, tgt,
                                         abs(fill - sig.stop), t.isoformat(), regime=reg, reason=sig.reason)
                by_id[sig.expert].on_open(sym)

            # 3) intrabar bracket legs, then close-of-bar management
            for sym, pos in list(open_pos.items()):
                if sym in exit_q:
                    continue
                i = idx.get(sym, {}).get(t)
                if i is None:
                    continue
                a = arrays[sym]
                last_px[sym] = a["close"][i]
                hit = bracket_hit(pos, a["open"][i], a["high"][i], a["low"][i], sim.slip)
                if hit:
                    open_pos.pop(sym)
                    sim.close(pos, hit[0], t, hit[1])
                    continue
                act = check_management(pos, by_id[pos.expert], a, i, flatten_mod)
                if act and act[0] == "exit":
                    exit_q[sym] = act[1]
                elif act and act[0] == "breakeven":
                    pos.stop, pos.be_moved = act[1], True
                elif act and act[0] == "trail":
                    pos.stop, pos.trailing = act[1], True

            # 4) daily loss limit
            if mark_to_market(sim.equity, open_pos, last_px) <= day_start_eq * (1 - config.DAILY_LOSS_LIMIT_PCT):
                halted = True

            # 5) new signals (fill next bar)
            if halted or not (config.FIRST_ENTRY_MOD <= m <= last_entry_mod):
                continue
            locked = set(open_pos) | set(pending)
            for e in rotate(experts, step):
                n = sum(1 for p in open_pos.values() if p.expert == e.id) + \
                    sum(1 for p in pending.values() if p[0].expert == e.id)
                for sym in rotate(symbols, step):
                    if n >= config.MAX_POSITIONS_PER_EXPERT:
                        break
                    if sym in locked:
                        continue
                    i = idx.get(sym, {}).get(t)
                    if i is None or i < 60:
                        continue
                    sector = sym_sector[sym]
                    dirs = allowed_dirs(e.style, regimes[sector])
                    if dirs and config.DIRECTION_CONFIRM:
                        dirs = dirs & confirm_dirs(e.style, arrays[sym], i)
                    if not dirs:
                        continue
                    if config.VOL_FILTER and not vol_filter_ok(arrays[sym], i):
                        continue
                    if config.ZONE_FILTER:
                        dirs = {d for d in dirs if zone_ok(d, arrays[sym], i)}
                        if not dirs:
                            continue
                    sig = e.entry(sym, arrays[sym], i, dirs)
                    if sig:
                        pending[sym] = (sig, sector, regimes[sector], t)
                        locked.add(sym)
                        n += 1

        # end of day: anything left closes at its last bar close
        for sym, pos in list(open_pos.items()):
            w = list(idx[sym].values())
            i = w[-1]
            sim.close(pos, arrays[sym]["close"][i] * (1 - pos.side * sim.slip), arrays[sym]["ts"][i], "session_end")
        if verbose:
            print(f"{ddate}  equity {sim.equity:,.2f}  trades {len(sim.trades)}")

    return sim, pd.DataFrame(regime_log)


def summarize(trades, by):
    def pf(x):
        loss = -x[x < 0].sum()
        return round(x[x > 0].sum() / loss, 2) if loss > 0 else float("inf")
    g = trades.groupby(by)
    return pd.DataFrame({
        "trades": g.size(),
        "win_rate": g["pnl"].apply(lambda x: round((x > 0).mean() * 100, 1)),
        "avg_R": g["r"].mean().round(3),
        "profit_factor": g["pnl"].apply(pf),
        "net_pnl": g["pnl"].sum().round(2),
        "avg_hold_min": g["hold_min"].mean().round(1),
    })


def report(sim, regime_df, start_equity, out):
    os.makedirs(out, exist_ok=True)
    tr = pd.DataFrame(sim.trades)
    regime_df.to_csv(os.path.join(out, "regime_heatmap_history.csv"), index=False)
    if tr.empty:
        print("No trades.")
        return
    tr.to_csv(os.path.join(out, "trades.csv"), index=False)
    curve = pd.DataFrame(sim.curve, columns=["time", "equity"])
    curve.to_csv(os.path.join(out, "equity_curve.csv"), index=False)
    eq = pd.concat([pd.Series([start_equity]), curve["equity"]])
    mdd = (eq / eq.cummax() - 1).min() * 100
    print("\n================ BACKTEST SUMMARY ================")
    print(f"Start {start_equity:,.2f} -> End {sim.equity:,.2f}  "
          f"({(sim.equity / start_equity - 1) * 100:.2f}%)   trades {len(tr)}   max DD {mdd:.2f}%")
    pd.set_option("display.width", 140)
    for by in ["expert", ["expert", "side"], "regime", "sector", "exit_reason"]:
        print(f"\n--- by {by} ---")
        print(summarize(tr, by).to_string())
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 4))
        for eid, g in tr.sort_values("closed").groupby("expert"):
            ax.plot(g["closed"], g["pnl"].cumsum(), label=eid)
        ax.plot(tr.sort_values("closed")["closed"], tr.sort_values("closed")["pnl"].cumsum(), "k", lw=2, label="ALL")
        ax.set_title("Cumulative P&L by expert")
        ax.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(out, "pnl_by_expert.png"), dpi=120)
    except ImportError:
        pass
    print(f"\nFiles written to {out}/")


def load_data(start, end, feed=config.HIST_FEED, verbose=True, transform=None, warmup_days=6):
    """Universe selection + daily/minute downloads (cached in ./data_cache). Shared with robustness.py."""
    from alpaca.data.timeframe import TimeFrame
    from data import cached_bars, make_data_client

    def utc(d):
        return pd.Timestamp(d, tz=NY).tz_convert("UTC").to_pydatetime()

    dc = make_data_client()
    cands = [s for spec in config.SECTORS.values() for s in spec["candidates"]]
    daily_c = cached_bars(dc, cands, TimeFrame.Day, utc(start - timedelta(days=45)), utc(start), feed, "all")
    universe = select_universe(daily_c, start, config.STOCKS_PER_SECTOR)
    if verbose:
        print("Universe (top 5 per sector by 20-day dollar volume before start):")
        for sec, syms in universe.items():
            print(f"  {sec:<17} {', '.join(syms)}")
    sym_sector = {s: sec for sec, syms in universe.items() for s in syms}
    etfs = [spec["etf"] for spec in config.SECTORS.values()]
    etf_daily = cached_bars(dc, etfs, TimeFrame.Day, utc(start - timedelta(days=400)),
                            utc(end + timedelta(days=1)), feed, "all")
    frames = {k: daily_frame(v) for k, v in etf_daily.items()}
    if verbose:
        print("Loading 1-min bars (downloaded once, then cached)...")
    minute = cached_bars(dc, list(sym_sector) + etfs, TimeFrame.Minute, utc(start - timedelta(days=warmup_days)),
                         utc(end + timedelta(days=1)), feed, "raw")
    prep = transform or prepare_intraday
    arrays = {s: to_arrays(prep(df)) for s, df in minute.items() if len(df)}
    return arrays, frames, sym_sector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--equity", type=float, default=100_000)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--fee-per-share", type=float, default=0.0)
    ap.add_argument("--feed", default=config.HIST_FEED)
    ap.add_argument("--no-overlay", action="store_true")
    ap.add_argument("--out", default="bt_results")
    ap.add_argument("--velez", action="store_true", help="Velez 2-min study (separate study)")
    ap.add_argument("--costcheck", action="store_true", help="Velez: stop-width / cost check only")
    ap.add_argument("--stopscan", action="store_true", help="Velez: run with no stop, then find the best stop")
    ap.add_argument("--tf", type=int, default=2, help="Velez bar size in minutes (2, 5, 15, 30 ...)")
    ap.add_argument("--horizon", type=int, default=90, help="stop scan: minutes to hold without a stop")
    ap.add_argument("--zones", action="store_true", help="5 experts only inside 365-day buy/sell zones")
    ap.add_argument("--runner", action="store_true",
                    help="-1R stop, no target, trailing stop that tightens above +1R")
    ap.add_argument("--volfilter", action="store_true",
                    help="only enter while volatility contracts and volume rises")
    ap.add_argument("--confirm", action="store_true",
                    help="require 60m trend + 15m momentum + 5m flow to agree on direction")
    ap.add_argument("--stop-frac", type=float, default=config.STOP_FRACTION,
                    help="stop at this fraction of each expert's stop distance (v1 = 1.0)")
    ap.add_argument("--target-r", type=float, default=config.TARGET_R,
                    help="target at this multiple of the ORIGINAL stop distance (v1 = expert default)")
    args = ap.parse_args()
    config.STOP_FRACTION, config.TARGET_R = args.stop_frac, args.target_r
    config.DIRECTION_CONFIRM = args.confirm
    config.VOL_FILTER = args.volfilter
    config.RUNNER_MODE = args.runner
    if args.runner:
        print("VARIANT: runner exits ON (-1R stop, no target, trail tightens above +1R)")
    if args.volfilter:
        print(f"VARIANT: vol filter ON (ATR5 < {config.VX_ATR_RATIO} x ATR30, "
              f"volume5 > {config.VX_VOL_RATIO} x prior volume30)")
    if args.confirm:
        print("VARIANT: direction confirmation ON (60m trend + 15m MACD + 5m flow)")
    if args.stop_frac != 1.0 or args.target_r is not None:
        print(f"VARIANT: stop at {args.stop_frac} x stop distance, target at {args.target_r} x stop distance")

    start, end = pd.Timestamp(args.start).date(), pd.Timestamp(args.end).date()
    experts, transform = None, None
    warm = 6
    if args.velez or args.costcheck or args.stopscan:
        import velez
        velez.VZ_TF = args.tf
        experts, transform, warm = velez.activate(), velez.prepare_2min, velez.warmup_days()
    arrays, frames, sym_sector = load_data(start, end, args.feed, transform=transform, warmup_days=warm)
    if args.zones:
        import zone_filter
        print("VARIANT: 365-day zones ON (longs only in buy zones, shorts only in sell zones)")
        zone_filter.attach(arrays, zone_filter.build_tables(list(sym_sector), start, end, args.feed))
        config.ZONE_FILTER = True
    if args.costcheck:
        velez.cost_check(arrays, sym_sector, start, end, args.slippage_bps)
        return
    if args.stopscan:
        velez.stop_scan(arrays, sym_sector, frames, start, end, args.slippage_bps, args.horizon)
        return
    sim, regimes = run_backtest(arrays, sym_sector, frames, start, end, args.equity, args.slippage_bps,
                                args.fee_per_share, overlay=not args.no_overlay, experts=experts)
    report(sim, regimes, args.equity, args.out)


if __name__ == "__main__":
    main()
