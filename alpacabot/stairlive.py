"""Stair-Step variant A as a SMALL PAPER sleeve of the swing runner (owner 2026-11-02: "small paper trade", after the backtest FAILED:
holdout PF 1.04 long-only on 18 trades, 0.61 both sides - see alpacabot.stairstep). Long only, 0.25% risk per trade, zones.json swing.stairstep.

Same code as the backtest (alpacabot.stairstep): weekly stage, sector RS (63 days, top 2), watchlist rebuilt at each week's last close, entry A at
the close (pullback to SMA20 + stochastic turn up from < 30), market buy at the next open, stop by the spec rule. Exits:
  - intraday (minute loop, SwingRunner.stair_partials): 1/3 sold at +1R (stop -> entry + 0.24% costs), 1/3 at +1.5R;
  - at each close (here): runner trail = max(trail, higher swing low - 0.1 ATR, SMA20 - 0.25 ATR; a TP signal tightens it to the day's low),
    trend-break (3 closes under weekly VWAP and SMA20) and 60 sessions max -> sold at the next open.
Differences from the backtest: partial fills are checked once a minute from 10:05 (not at the exact level); the stop is set from the signal
close (the backtest measures it from the open).
"""

from datetime import date

import numpy as np
import pandas as pd

from . import stairstep as ss
from .anglepacer import stock_daily, stock_weekly

LOOK, TOPN, VWT = 63, 2, "weekly"          # frozen train-chosen params of variant A long (state/reports/stair_step/report.md)
BE_COST = 0.0024


def build(bars: dict, sector_of: dict, etfs: list):
    """bars: symbol -> daily OHLCV (stocks, sector ETFs, SPY). Returns (U, etf closes, spy closes, calendar)."""
    dd = {s: stock_daily(d) for s, d in bars.items() if d is not None and len(d)}
    cal = dd["SPY"].index
    spy = dd["SPY"]["close"].to_numpy(float)
    etf = {e: dd[e]["close"].reindex(cal).to_numpy(float) for e in etfs if e in dd}
    U = {}
    for s in sector_of:
        if s in dd and len(dd[s]) > 200:
            U[s] = ss.align(ss.prep(dd[s], stock_weekly(dd[s])), cal)
    return U, etf, spy, cal


def week_end_index(cal: pd.DatetimeIndex, t: int) -> int:
    """Last week-end bar at or before t (a Friday close counts as the end of its week)."""
    weeks = cal.to_period("W-FRI")
    for i in range(t, -1, -1):
        if (i == t and cal[i].weekday() == 4) or (i < t and weeks[i] != weeks[i + 1]):
            return i
    return t


def plan(bars: dict, today: date, held: dict, sector_of: dict, etfs: list, cfg: dict) -> dict:
    """At the close of `today`: new entries (watchlist names with entry A), exits for held stair-step positions and runner trail moves."""
    U, etf, spy, cal = build(bars, sector_of, etfs)
    t = len(cal) - 1
    out = {"entries": [], "exits": [], "trail": {}, "below": {}, "watchlist": [], "signals": []}
    if cal[t].date() != today:
        out["skipped"] = f"last bar {cal[t].date()} is not today"
        return out
    tw = week_end_index(cal, t)
    ranks = ss.sector_ranks(etf, spy, tw, LOOK)
    wl = ss.watchlist(U, lambda s, i: sector_of.get(s), ranks, tw, 1, TOPN, "full")
    out["watchlist"] = sorted(wl, key=wl.get, reverse=True)
    mine = {s: p for s, p in held.items() if p.get("strategy") == "stairstep"}
    for sym, p in mine.items():
        A = U.get(sym)
        if A is None:
            continue
        c, sma, atr, vw = A["c"][t], A["sma"][t], A["atr"][t], A[f"vw_{VWT}_1"][t]
        below = int(p.get("below", 0))
        if p.get("below_day") != today.isoformat():
            below = below + 1 if (np.isfinite(vw) and c < vw and c < sma) else 0
        out["below"][sym] = below
        held_n = int(np.sum(cal.date >= date.fromisoformat(p["entry_date"])))
        if below >= 3:
            out["exits"].append((sym, "trendbreak"))
            continue
        if held_n >= 60:
            out["exits"].append((sym, "time"))
            continue
        if int(p.get("parts", 0)) >= 2:
            hsl, prev = A["sl"][t], A["sl_prev"][t]
            cands = [sma - 0.25 * atr]
            if np.isfinite(hsl) and np.isfinite(prev) and hsl > prev:
                cands.append(hsl - 0.1 * atr)
            if ss._tp_signal(A, t, 1, False):
                cands.append(A["l"][t])
            new = max([x for x in cands if np.isfinite(x)] + [float(p.get("trail") or -np.inf)])
            if np.isfinite(new) and new > float(p["stop"]) + 0.005 and new < c:
                out["trail"][sym] = round(float(new), 2)
    if not cfg.get("new_entries", True):
        return out
    n_open = len(mine) - len(out["exits"])
    per_sec = {}
    for s, p in mine.items():
        per_sec[sector_of.get(s)] = per_sec.get(sector_of.get(s), 0) + 1
    for s in out["watchlist"]:
        if s in held or n_open >= int(cfg.get("max_open", 5)) or per_sec.get(sector_of.get(s), 0) >= 2:
            continue
        A = U[s]
        if not ss.entry_signal(A, t, 1, "A", A[f"vw_{VWT}_1"]):
            continue
        close = float(A["c"][t])
        stop = ss.stop_for(A, t, 1, close)
        if stop is None:
            continue
        out["signals"].append(s)
        out["entries"].append({"symbol": s, "stop": round(float(stop), 2), "signal_close": close})   # no "atr": the open's chase rule is not in the spec
        n_open += 1
        per_sec[sector_of.get(s)] = per_sec.get(sector_of.get(s), 0) + 1
    return out
