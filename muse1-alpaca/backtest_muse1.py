#!/usr/bin/env python3
"""Pre-registered out-of-sample backtest of the ONE log-only strategy
(raschke_soup) for muse1/alpaca v0.3.0 — Claude fix list, round two, item 6.

PRE-REGISTRATION (frozen BEFORE running — do not change after seeing results):
  Strategy: raschke_soup ONLY (failed 20-bar breakout, trail to prior
    extreme, flat after 6 bars, no fixed TP in the lock — here the bot's
    sleeve TP applies, see below). Day sleeve only.
  Symbols: the 33-stock watchlist in config.yaml + SPY (filter symbol).
  Data: Alpaca IEX 5-min bars, regular session only (09:30-15:59 ET).
  Split: Train 2026-01-01 to 2026-06-30, Test 2026-07-01 to 2026-11-04.
  Costs (realistic): 3 bps/side commission + 0.1 bps SEC fee + 0.05%
    slippage round trip (0.025% per side). All costs charged on entry
    and exit notionals.
  Frozen config (from config.yaml): SPY filter ON (vs_open, flat
    threshold 0.15% -> range experts only, and raschke_soup is a breakout
    expert so it does NOT trade on flat days), 0.5% risk per trade,
    1R stop (signal stop distance, re-anchored at the fill), 2.5R day
    target, breakeven after +1R, stair trail (peak - 0.5R once +0.5R),
    signal decay (no progress past +0.5R in 12 x 5-min bars -> close),
    flatten 15:50 ET, max 5 open, max 5 new/day, max 2 per sector,
    60% net-exposure cap, 2% daily loss cap, 25% notional cap, sizing
    on available equity, window 10:00-15:45 ET for new entries.
  Fills: signal at bar i fills at the NEXT bar's open (same day only).
  Exits are checked on the fill bar onwards (open first, then high/low).
  Starting equity: $50,000 (round number; results are reported in R,
    which is scale-invariant except for the notional cap).
  Random control: same engine, same per-symbol-day signal counts,
    random bars and random sides (SPY filter and all caps still apply),
    20 seeded draws (seeds 1-20). Median PF / avg R reported.
  PASS BAR: PF >= 1.0 AND avg R > 0 on BOTH train and test, soup PF >
    random median PF in both periods, >= 20 trades total. Otherwise
    the result is a FAIL, reported as such. NO re-tuning.
  The backtest result changes NO trading code in this round — report only.

Vectorized soup signals are verified against experts.raschke_soup on a
sample before the run; the script aborts if they disagree.
Data is cached locally in backtest_muse1_bars.csv (gitignored, never
pushed). Results + trade list are written to backtest_muse1_results.json
and backtest_muse1_trades.csv next to this script.
"""
import json
import math
import os
import random
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np
import pandas as pd
import yaml

from bot import load_dotenv  # noqa: E402  (keys via .env, never printed)
import experts  # noqa: E402

ET = ZoneInfo("America/New_York")
TRAIN_END = date(2026, 6, 30)
TEST_END = date(2026, 11, 4)
START = date(2026, 1, 1)
CACHE = HERE / "backtest_muse1_bars.csv"
RESULTS = HERE / "backtest_muse1_results.json"
TRADES_CSV = HERE / "backtest_muse1_trades.csv"

START_EQUITY = 50000.0
RISK_PCT = 0.005
TP_R = 2.5
MAX_OPEN = 5
MAX_NEW_PER_DAY = 5
MAX_PER_SECTOR = 2
MAX_NET_EXPOSURE = 0.60
DAILY_LOSS_CAP_PCT = 0.02
MAX_NOTIONAL_PCT = 0.25
FLAT_THRESHOLD_PCT = 0.15
COST_RATE_PER_SIDE = 0.0003 + 0.00001 + 0.00025  # 3bps + 0.1bps fee + 0.025% slip
WINDOW_START = dtime(10, 0)
WINDOW_END = dtime(15, 45)
FLATTEN = dtime(15, 50)


# ---------------------------------------------------------------- data
def fetch_bars(symbols, refetch=False):
    """Alpaca IEX 5-min bars, monthly chunks, cached to CSV. Returns a
    DataFrame with columns symbol, t (UTC), o, h, l, c, v."""
    if CACHE.exists() and not refetch:
        df = pd.read_csv(CACHE, parse_dates=["t"])
        df["t"] = pd.to_datetime(df["t"], utc=True)
        have = set(df["symbol"].unique())
        if set(symbols) <= have and len(df) > 0:
            print(f"using cached bars: {len(df):,} rows, {len(have)} symbols")
            return df
    load_dotenv()
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.data.enums import DataFeed
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("no Alpaca keys (env/.env) — cannot fetch bars")
    client = StockHistoricalDataClient(key, secret)
    frames = []
    month = date(START.year, START.month, 1)
    while month <= TEST_END:
        nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        start = datetime(month.year, month.month, 1, tzinfo=timezone.utc)
        end = datetime(nxt.year, nxt.month, 1, tzinfo=timezone.utc)
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=start, end=end, limit=1000000, feed=DataFeed.IEX)
        df = client.get_stock_bars(req).df
        if df is not None and len(df):
            df = df.reset_index().rename(columns={"timestamp": "t"})
            frames.append(df[["symbol", "t", "open", "high", "low", "close", "volume"]])
            print(f"fetched {month:%Y-%m}: {len(df):,} rows")
        month = nxt
    all_df = pd.concat(frames, ignore_index=True)
    all_df.columns = ["symbol", "t", "o", "h", "l", "c", "v"]
    all_df["t"] = pd.to_datetime(all_df["t"], utc=True)
    all_df.to_csv(CACHE, index=False)
    print(f"cached {len(all_df):,} rows -> {CACHE.name}")
    return all_df


def prep(df):
    """Regular session only (09:30-15:59 ET), sorted, with ET date/time."""
    df = df.copy()
    df["t_et"] = df["t"].dt.tz_convert(ET)
    df["day"] = df["t_et"].dt.date
    df["tod"] = df["t_et"].dt.time
    df = df[(df["tod"] >= dtime(9, 30)) & (df["tod"] <= dtime(15, 59))]
    df = df.sort_values(["symbol", "t"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------- signals (vectorized soup)
def soup_signals(g):
    """Vectorized raschke_soup for one symbol's bars (regular session,
    sorted by time). Returns arrays: side (0/+-1), entry, stop.
    Mirrors experts.raschke_soup exactly: 20-bar extremes over the 20
    bars BEFORE the current bar; a poke beyond the extreme on the prior
    bar and a close back inside on the current bar; stop = extreme +/-
    0.5 ATR(14)."""
    h = g["h"].to_numpy(dtype=float)
    l = g["l"].to_numpy(dtype=float)
    c = g["c"].to_numpy(dtype=float)
    n = len(g)
    side = np.zeros(n, dtype=int)
    entry = np.full(n, np.nan)
    stop = np.full(n, np.nan)
    if n < 22:
        return side, entry, stop
    # True range + ATR(14), same definition as experts.atr().
    pc = np.empty(n)
    pc[0] = np.nan
    pc[1:] = c[:-1]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
    hi20 = pd.Series(h).rolling(20).max().shift(1).to_numpy()
    lo20 = pd.Series(l).rolling(20).min().shift(1).to_numpy()
    prev_h = np.empty(n); prev_h[0] = np.nan; prev_h[1:] = h[:-1]
    prev_l = np.empty(n); prev_l[0] = np.nan; prev_l[1:] = l[:-1]
    short = (prev_h >= hi20) & (c < hi20)
    long_ = (prev_l <= lo20) & (c > lo20)
    # experts.raschke_soup checks the short branch first, then long.
    side[short] = -1
    side[long_ & ~short] = 1
    m = side != 0
    entry[m] = c[m]
    stop[m & (side == -1)] = hi20[m & (side == -1)] + 0.5 * atr[m & (side == -1)]
    stop[m & (side == 1)] = lo20[m & (side == 1)] - 0.5 * atr[m & (side == 1)]
    return side, entry, stop


def verify_vectorized(df, symbol="AAPL", n_bars=500):
    """Compare vectorized soup against experts.raschke_soup on a sample.
    Aborts the run if they disagree."""
    g = df[df["symbol"] == symbol].head(n_bars)
    bars = [{"t": r.t, "o": r.o, "h": r.h, "l": r.l, "c": r.c, "v": r.v}
            for r in g.itertuples(index=False)]
    side, entry, stop = soup_signals(g.reset_index(drop=True))
    mismatches = 0
    for i in range(21, len(bars)):
        exp = experts.raschke_soup(bars[:i + 1])
        got_side = int(side[i])
        exp_side = 0 if exp is None else exp["side"]
        if got_side != exp_side:
            mismatches += 1
            continue
        if exp is not None:
            if abs(entry[i] - exp["entry"]) > 1e-9 or abs(stop[i] - exp["stop"]) > 1e-6:
                mismatches += 1
    print(f"vectorized soup vs experts.raschke_soup ({symbol}, {len(bars)} bars): "
          f"{mismatches} mismatches")
    if mismatches:
        raise RuntimeError("vectorized soup disagrees with experts.raschke_soup — aborting")

# ---------------------------------------------------------------- simulation
def build_frames(df, sectors):
    """Per-symbol frames with soup signals, ATR, and next-bar opens.
    Returns {symbol: frame} for non-SPY symbols, plus SPY lookup maps."""
    frames = {}
    spy = df[df["symbol"] == "SPY"].copy()
    spy_open = {}
    spy_close = {}
    for day, g in spy.groupby("day"):
        spy_open[day] = float(g.iloc[0]["o"])
        for r in g.itertuples(index=False):
            spy_close[(day, r.tod)] = float(r.c)
    for symbol, g in df.groupby("symbol"):
        if symbol == "SPY":
            continue
        g = g.reset_index(drop=True)
        side, entry, stop = soup_signals(g)
        # ATR(14) again for the random control's stops.
        h = g["h"].to_numpy(dtype=float)
        l = g["l"].to_numpy(dtype=float)
        c = g["c"].to_numpy(dtype=float)
        pc = np.empty(len(g)); pc[0] = np.nan; pc[1:] = c[:-1]
        tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
        atr14 = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
        nxt_open = g["o"].shift(-1).to_numpy()
        same_day = (g["day"].shift(-1) == g["day"]).to_numpy()
        g = g.assign(side=side, sig_entry=entry, sig_stop=stop, atr14=atr14,
                     next_open=nxt_open, has_next=same_day)
        frames[symbol] = g
    return frames, spy_open, spy_close


def _spy_side(day, tod, spy_open, spy_close):
    """(side, flat) at a bar: side +1 long-only / -1 short-only / 0 both;
    flat True when SPY is within the flat threshold of today's open."""
    op = spy_open.get(day)
    cl = spy_close.get((day, tod))
    if op is None or cl is None:
        return 0, False
    flat = abs(cl - op) / op * 100.0 < FLAT_THRESHOLD_PCT
    if flat:
        return 0, True
    if cl > op:
        return 1, False
    if cl < op:
        return -1, False
    return 0, False


def run_walk(frames, spy_open, spy_close, sectors):
    """One full simulation pass over all bars. Returns (trades, equity_curve).
    Positions are day trades: every position exits by the 15:50 flatten."""
    # One event stream, time-ordered across symbols (caps are global).
    rows = []
    for symbol, g in frames.items():
        for r in g.itertuples(index=False):
            rows.append((r.t, symbol, r))
    rows.sort(key=lambda x: (x[0], x[1]))
    equity = START_EQUITY
    day_start_equity = equity
    day_pnl = 0.0
    cur_day = None
    new_today = 0
    positions = {}
    trades = []
    equity_curve = [(None, equity)]

    def committed_notional():
        return sum(p["qty"] * p["entry_fill"] for p in positions.values())

    def net_exposure_after(new_side, new_notional):
        longs = sum(p["qty"] * p["entry_fill"] for p in positions.values()
                    if p["side"] == 1)
        shorts = sum(p["qty"] * p["entry_fill"] for p in positions.values()
                     if p["side"] == -1)
        if new_side == 1:
            longs += new_notional
        else:
            shorts += new_notional
        return abs(longs - shorts) / max(equity, 1.0)

    def sector_count(sector):
        return sum(1 for p in positions.values()
                   if sectors.get(p["symbol"], "unknown") == sector)

    def record_trade(p, exit_price, exit_reason, exit_time):
        nonlocal equity, day_pnl
        gross = (exit_price - p["entry_fill"]) * p["side"] * p["qty"]
        entry_cost = p["qty"] * p["entry_fill"] * COST_RATE_PER_SIDE
        exit_cost = p["qty"] * exit_price * COST_RATE_PER_SIDE
        pnl = gross - entry_cost - exit_cost
        pnl_r = pnl / p["risk_dollars"] if p["risk_dollars"] > 0 else 0.0
        equity += pnl
        day_pnl += pnl
        trades.append({
            "symbol": p["symbol"], "side": p["side"],
            "entry_day": str(p["entry_day"]), "entry_time": str(p["entry_time"]),
            "exit_time": str(exit_time), "entry_fill": round(p["entry_fill"], 2),
            "exit_price": round(exit_price, 2), "exit_reason": exit_reason,
            "qty": p["qty"], "risk_dollars": round(p["risk_dollars"], 2),
            "pnl_dollars": round(pnl, 2), "pnl_r": round(pnl_r, 3),
            "peak_r": round(p["peak_r"], 3), "bars_held": p["bars_held"],
        })
        equity_curve.append((exit_time, equity))

    for t, symbol, r in rows:
        day = r.day
        if day != cur_day:
            cur_day = day
            new_today = 0
            day_start_equity = equity
            day_pnl = 0.0
        p = positions.get(symbol)
        if p is not None:
            # --- exits on this bar.
            exited = None
            if p.get("fill_pending"):
                p["fill_pending"] = False
                if p["side"] == 1 and r.o <= p["stop"]:
                    exited = (r.o, "stop_at_open")
                elif p["side"] == 1 and r.o >= p["tp"]:
                    exited = (r.o, "tp_at_open")
                elif p["side"] == -1 and r.o >= p["stop"]:
                    exited = (r.o, "stop_at_open")
                elif p["side"] == -1 and r.o <= p["tp"]:
                    exited = (r.o, "tp_at_open")
            if exited is None:
                if p["side"] == 1 and r.l <= p["stop"]:
                    exited = (p["stop"], "stop")
                elif p["side"] == -1 and r.h >= p["stop"]:
                    exited = (p["stop"], "stop")
                elif p["side"] == 1 and r.h >= p["tp"]:
                    exited = (p["tp"], "tp")
                elif p["side"] == -1 and r.l <= p["tp"]:
                    exited = (p["tp"], "tp")
            if exited is None:
                # Peak / breakeven / stair trail (same math as bot.py).
                if p["side"] == 1:
                    p["peak"] = max(p["peak"], r.h)
                    p["peak_r"] = (p["peak"] - p["entry_fill"]) / p["one_r"]
                else:
                    p["peak"] = min(p["peak"], r.l)
                    p["peak_r"] = (p["entry_fill"] - p["peak"]) / p["one_r"]
                if p["peak_r"] >= 1.0 and not p["be_moved"] and p["stop"] != p["entry_fill"]:
                    p["stop"] = p["entry_fill"]
                    p["be_moved"] = True
                if p["peak_r"] >= 0.5:
                    if p["side"] == 1:
                        trail = p["peak"] - 0.5 * p["one_r"]
                        if trail > p["stop"]:
                            p["stop"] = trail
                    else:
                        trail = p["peak"] + 0.5 * p["one_r"]
                        if trail < p["stop"]:
                            p["stop"] = trail
                    # Did this bar's adverse extreme cross the new trail?
                    if p["side"] == 1 and r.l <= p["stop"]:
                        exited = (p["stop"], "trail")
                    elif p["side"] == -1 and r.h >= p["stop"]:
                        exited = (p["stop"], "trail")
            if exited is None and p["bars_held"] >= 12 and p["peak_r"] < 0.5:
                exited = (r.c, "decay")
            if exited is None and r.tod >= FLATTEN:
                exited = (r.c, "flatten")
            if exited is not None:
                record_trade(p, exited[0], exited[1], r.t)
                del positions[symbol]
                p = None
            else:
                p["bars_held"] += 1
        # --- new signal on this bar.
        if p is None and r.side != 0 and bool(r.has_next):
            if not (WINDOW_START <= r.tod <= WINDOW_END):
                continue
            spy_side, flat = _spy_side(day, r.tod, spy_open, spy_close)
            if flat:
                continue  # raschke_soup is breakout, not a range expert
            if spy_side != 0 and int(r.side) != spy_side:
                continue
            if len(positions) >= MAX_OPEN or new_today >= MAX_NEW_PER_DAY:
                continue
            sector = sectors.get(symbol, "unknown")
            if sector_count(sector) >= MAX_PER_SECTOR:
                continue
            if day_pnl <= -DAILY_LOSS_CAP_PCT * day_start_equity:
                continue
            fill = float(r.next_open)
            sig_stop = float(r.sig_stop)
            stop_dist = abs(float(r.sig_entry) - sig_stop)
            if stop_dist <= 0:
                continue
            avail = max(equity - committed_notional(), 0.0)
            qty = int((RISK_PCT * avail) // stop_dist)
            notional = qty * fill
            if qty <= 0 or notional > MAX_NOTIONAL_PCT * avail:
                qty = int((MAX_NOTIONAL_PCT * avail) // fill) if avail > 0 else 0
                notional = qty * fill
            if qty <= 0:
                continue
            one_r = abs(fill - sig_stop)
            risk_dollars = qty * one_r
            round_trip = 2 * notional * COST_RATE_PER_SIDE
            if round_trip >= risk_dollars:
                continue
            if TP_R * risk_dollars - round_trip <= 0:
                continue
            if net_exposure_after(int(r.side), notional) > MAX_NET_EXPOSURE:
                continue
            tp = fill + int(r.side) * TP_R * one_r
            positions[symbol] = {
                "symbol": symbol, "side": int(r.side), "qty": qty,
                "entry_fill": fill, "stop": sig_stop, "tp": tp,
                "one_r": one_r, "risk_dollars": risk_dollars,
                "entry_day": day, "entry_time": r.t, "peak": fill,
                "peak_r": 0.0, "bars_held": 0, "be_moved": False,
                "fill_pending": True,
            }
            new_today += 1
    if positions:
        raise RuntimeError(f"positions still open at end of data: {sorted(positions)}")
    return trades, equity_curve


def random_frames(frames, seed):
    """Same engine, random entries: per symbol-day, the same count of
    signals as soup produced (window-filtered), at random eligible bars
    with random sides. Entry = bar close, stop = entry -/+ 1 ATR."""
    rng = random.Random(seed)
    out = {}
    for symbol, g in frames.items():
        g = g.copy()
        side = np.zeros(len(g), dtype=int)
        entry = np.full(len(g), np.nan)
        stop = np.full(len(g), np.nan)
        for day, idx in g.groupby("day").groups.items():
            idx = np.array(sorted(idx))
            sig_idx = [i for i in idx
                       if g.at[i, "side"] != 0
                       and WINDOW_START <= g.at[i, "tod"] <= WINDOW_END]
            if not sig_idx:
                continue
            eligible = [i for i in idx
                        if i >= 21 and bool(g.at[i, "has_next"])
                        and WINDOW_START <= g.at[i, "tod"] <= WINDOW_END]
            if not eligible:
                continue
            for _ in sig_idx:
                i = rng.choice(eligible)
                s = rng.choice([-1, 1])
                atr = g.at[i, "atr14"]
                if atr is None or (isinstance(atr, float) and math.isnan(atr)):
                    continue
                side[i] = s
                entry[i] = float(g.at[i, "c"])
                stop[i] = entry[i] - s * float(atr)
        g = g.assign(side=side, sig_entry=entry, sig_stop=stop)
        out[symbol] = g
    return out


def stats(trades, label):
    """Per-period stats from a trade list."""
    if not trades:
        return {"label": label, "trades": 0, "wins": 0, "win_rate": None,
                "profit_factor": None, "avg_r": None, "net_r": None,
                "net_dollars": 0.0}
    wins = [t for t in trades if t["pnl_dollars"] > 0]
    losses = [t for t in trades if t["pnl_dollars"] <= 0]
    gross_win = sum(t["pnl_dollars"] for t in wins)
    gross_loss = abs(sum(t["pnl_dollars"] for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_win > 0 else None)
    return {"label": label, "trades": len(trades), "wins": len(wins),
            "win_rate": round(len(wins) / len(trades), 3),
            "profit_factor": (round(pf, 3) if pf is not None and pf != float("inf")
                              else ("inf" if pf == float("inf") else None)),
            "avg_r": round(sum(t["pnl_r"] for t in trades) / len(trades), 3),
            "net_r": round(sum(t["pnl_r"] for t in trades), 2),
            "net_dollars": round(sum(t["pnl_dollars"] for t in trades), 2),
            "best_r": max(t["pnl_r"] for t in trades),
            "worst_r": min(t["pnl_r"] for t in trades),
            "exits": {r: sum(1 for t in trades if t["exit_reason"] == r)
                      for r in sorted({t["exit_reason"] for t in trades})}}


def max_drawdown(equity_curve):
    peak = -float("inf")
    worst = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        worst = min(worst, eq - peak)
    return round(worst, 2)


def main():
    refetch = "--refetch" in sys.argv
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    symbols = list(cfg["watchlist"])
    sectors = cfg.get("sectors", {})
    df = prep(fetch_bars(symbols, refetch=refetch))
    print(f"bars: {len(df):,} rows, {df['symbol'].nunique()} symbols, "
          f"{df['day'].min()} to {df['day'].max()}")
    verify_vectorized(df)
    frames, spy_open, spy_close = build_frames(df, sectors)
    n_signals = sum(int((g["side"] != 0).sum()) for g in frames.values())
    print(f"soup signals (all bars): {n_signals}")

    print("running soup walk ...")
    trades, curve = run_walk(frames, spy_open, spy_close, sectors)
    train = [t for t in trades if date.fromisoformat(t["entry_day"]) <= TRAIN_END]
    test = [t for t in trades
            if TRAIN_END < date.fromisoformat(t["entry_day"]) <= TEST_END]
    print(f"soup trades: {len(trades)} (train {len(train)}, test {len(test)})")

    print("running random control (20 draws) ...")
    rand_train_pf, rand_test_pf, rand_train_r, rand_test_r = [], [], [], []
    for seed in range(1, 21):
        rf = random_frames(frames, seed)
        rt, _ = run_walk(rf, spy_open, spy_close, sectors)
        rtrain = [t for t in rt if date.fromisoformat(t["entry_day"]) <= TRAIN_END]
        rtest = [t for t in rt
                 if TRAIN_END < date.fromisoformat(t["entry_day"]) <= TEST_END]
        st, sx = stats(rtrain, "rand-train"), stats(rtest, "rand-test")
        if st["profit_factor"] is not None and st["profit_factor"] != "inf":
            rand_train_pf.append(st["profit_factor"])
        if sx["profit_factor"] is not None and sx["profit_factor"] != "inf":
            rand_test_pf.append(sx["profit_factor"])
        if st["avg_r"] is not None:
            rand_train_r.append(st["avg_r"])
        if sx["avg_r"] is not None:
            rand_test_r.append(sx["avg_r"])
        print(f"  random draw {seed}: {len(rt)} trades, "
              f"train PF {st['profit_factor']} / test PF {sx['profit_factor']}")
    def median(xs):
        xs = sorted(xs)
        return round(xs[len(xs) // 2], 3) if xs else None

    full = stats(trades, "full")
    tr_s, te_s = stats(train, "train"), stats(test, "test")
    result = {
        "pre_registration": {
            "strategy": "raschke_soup only, day sleeve",
            "symbols": symbols,
            "data": "Alpaca IEX 5-min bars, regular session 09:30-15:59 ET",
            "train": "2026-01-01 to 2026-06-30",
            "test": "2026-07-01 to 2026-11-04",
            "costs": "3 bps/side + 0.1 bps fee + 0.05% slippage round trip",
            "config": "SPY filter on (vs_open, flat 0.15% -> no breakout trades), "
                      "0.5% risk, 1R stop, 2.5R day target, BE after +1R, "
                      "stair trail peak-0.5R, decay 12 bars, flatten 15:50 ET, "
                      "max 5 open, max 5 new/day, max 2/sector, 60% net exposure, "
                      "2% daily loss cap, 25% notional cap, window 10:00-15:45 ET",
            "fills": "signal at bar i fills at next bar open (same day only)",
            "start_equity": START_EQUITY,
            "pass_bar": "PF >= 1.0 AND avg R > 0 on BOTH train and test, "
                        "soup PF > random median PF in both periods, "
                        ">= 20 trades total",
            "random_control": "20 seeded draws (seeds 1-20), same per-symbol-day "
                              "signal counts, random bars/sides, SPY filter + caps apply",
        },
        "full": full,
        "train": tr_s,
        "test": te_s,
        "random_control_median": {
            "train_pf": median(rand_train_pf), "test_pf": median(rand_test_pf),
            "train_avg_r": median(rand_train_r), "test_avg_r": median(rand_test_r),
            "draws": 20,
        },
        "max_drawdown_dollars": max_drawdown(curve),
        "final_equity": round(curve[-1][1], 2),
        "trades": trades,
    }
    def pf_ok(s):
        pf = s["profit_factor"]
        return pf == "inf" or (pf is not None and pf >= 1.0)
    passed = (
        len(trades) >= 20
        and pf_ok(tr_s) and pf_ok(te_s)
        and tr_s["avg_r"] is not None and tr_s["avg_r"] > 0
        and te_s["avg_r"] is not None and te_s["avg_r"] > 0
        and (result["random_control_median"]["train_pf"] is None
             or (tr_s["profit_factor"] != "inf"
                 and tr_s["profit_factor"] > result["random_control_median"]["train_pf"])
             or tr_s["profit_factor"] == "inf")
        and (result["random_control_median"]["test_pf"] is None
             or (te_s["profit_factor"] != "inf"
                 and te_s["profit_factor"] > result["random_control_median"]["test_pf"])
             or te_s["profit_factor"] == "inf"))
    result["pass_bar_result"] = "PASS" if passed else "FAIL"
    RESULTS.write_text(json.dumps(result, indent=1))
    pd.DataFrame(trades).to_csv(TRADES_CSV, index=False)
    print()
    print(f"FULL:  {full['trades']} trades, PF {full['profit_factor']}, "
          f"avg R {full['avg_r']}, net ${full['net_dollars']}")
    print(f"TRAIN: {tr_s['trades']} trades, PF {tr_s['profit_factor']}, "
          f"avg R {tr_s['avg_r']}, net ${tr_s['net_dollars']}")
    print(f"TEST:  {te_s['trades']} trades, PF {te_s['profit_factor']}, "
          f"avg R {te_s['avg_r']}, net ${te_s['net_dollars']}")
    print(f"RANDOM median: train PF {result['random_control_median']['train_pf']}, "
          f"test PF {result['random_control_median']['test_pf']}")
    print(f"PASS BAR: {result['pass_bar_result']}")
    print(f"results -> {RESULTS.name}, trades -> {TRADES_CSV.name}")


if __name__ == "__main__":
    main()
