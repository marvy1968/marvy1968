"""
Backtest Engine for forex_zone_bot.py
======================================

Walks forward through historical OANDA candle data bar-by-bar, generating
a signal at each bar using ONLY the data available up to that point (the
existing swing-detection logic already refuses to confirm a swing until
`SWING_N` bars later, so this naturally avoids lookahead leakage).

For every flagged signal, simulates a trade:
  - Entry at the NEXT bar's open (you can't fill on the signal bar's own close)
  - Stop-loss / take-profit set via ATR multiples
  - Exit on whichever level is hit first, walking forward bar-by-bar
  - Time-stop: force-close after MAX_HOLD_BARS if neither level is hit

Reports: win rate, average R, profit factor, max drawdown, equity curve.

pip install oandapyV20 pandas numpy
"""

import numpy as np
import pandas as pd

from forex_zone_bot import (
    Config, fetch_candles, pip_size,
    detect_swings, add_bollinger, get_last_swing_leg, fib_zones,
    detect_candle_patterns, _body, _lower_wick, _upper_wick,
)


# ======================================================================
# BACKTEST CONFIG
# ======================================================================
class BTConfig:
    INSTRUMENT = "EUR_USD"
    GRANULARITY = "H1"
    HISTORY_COUNT = 5000          # OANDA max per request is 5000
    ATR_PERIOD = 14
    STOP_ATR_MULT = 1.5
    TARGET_ATR_MULT = 2.5         # >1.5 stop mult -> positive expected R:R by design
    MAX_HOLD_BARS = 48            # force-exit after this many bars if no TP/SL hit
    MIN_LOOKBACK = 60             # bars needed before signals are considered valid
    SPREAD_PIPS = 1.0             # simple cost model, subtracted from every trade
    MIN_SCORE = Config.MIN_SCORE_TO_FLAG


# ======================================================================
# ATR
# ======================================================================
def add_atr(df, period=BTConfig.ATR_PERIOD):
    df = df.copy()
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(period).mean()
    return df


# ======================================================================
# SIGNAL GENERATION FOR A SLICE (no lookahead beyond the slice's last bar)
# ======================================================================
def evaluate_slice(df_slice, instrument):
    """Same scoring logic as forex_zone_bot.evaluate_instrument, but operating
    on a fixed historical slice instead of a fresh live fetch."""
    df_slice = detect_swings(df_slice)
    df_slice = add_bollinger(df_slice)
    leg = get_last_swing_leg(df_slice)
    zones = fib_zones(leg)
    patterns = detect_candle_patterns(df_slice)

    latest = df_slice.iloc[-1]
    price = latest["close"]
    pip = pip_size(instrument)
    tolerance = Config.FIB_TOLERANCE_PIPS * pip

    votes = {"buy": 0.0, "sell": 0.0}
    confluence = []

    for level, fib_price in zones.items():
        if abs(price - fib_price) <= tolerance:
            side = "buy" if leg["direction"] == "up" else "sell"
            confluence.append(f"fib_{level}_{side}")
            votes[side] += 1.0

    if latest["bb_walk_down"]:
        confluence.append("bb_lower_touch"); votes["buy"] += 1.0
    if latest["bb_walk_up"]:
        confluence.append("bb_upper_touch"); votes["sell"] += 1.0
    if latest["bb_squeeze"]:
        votes["buy"] += 0.25; votes["sell"] += 0.25

    for name, side in patterns:
        confluence.append(f"pattern_{name}")
        if side == "buy":
            votes["buy"] += 1.5
        elif side == "sell":
            votes["sell"] += 1.5

    body = _body(latest)
    if body > 0:
        if _lower_wick(latest) > body * 2:
            confluence.append("strong_lower_wick"); votes["buy"] += 1.0
        if _upper_wick(latest) > body * 2:
            confluence.append("strong_upper_wick"); votes["sell"] += 1.0

    if leg is not None:
        if leg["direction"] == "up" and votes["buy"] > votes["sell"]:
            votes["buy"] += 0.5
        elif leg["direction"] == "down" and votes["sell"] > votes["buy"]:
            votes["sell"] += 0.5

    if votes["buy"] > votes["sell"]:
        return "buy", round(votes["buy"], 2), confluence
    elif votes["sell"] > votes["buy"]:
        return "sell", round(votes["sell"], 2), confluence
    return None, 0.0, confluence


# ======================================================================
# TRADE SIMULATION
# ======================================================================
def simulate_trade(df, entry_idx, side, atr_at_entry, instrument):
    pip = pip_size(instrument)
    entry_price = df.iloc[entry_idx]["open"]
    spread_cost = BTConfig.SPREAD_PIPS * pip

    if side == "buy":
        entry_price += spread_cost / 2
        stop = entry_price - atr_at_entry * BTConfig.STOP_ATR_MULT
        target = entry_price + atr_at_entry * BTConfig.TARGET_ATR_MULT
    else:
        entry_price -= spread_cost / 2
        stop = entry_price + atr_at_entry * BTConfig.STOP_ATR_MULT
        target = entry_price - atr_at_entry * BTConfig.TARGET_ATR_MULT

    end_idx = min(entry_idx + BTConfig.MAX_HOLD_BARS, len(df) - 1)

    for i in range(entry_idx, end_idx + 1):
        bar = df.iloc[i]
        if side == "buy":
            if bar["low"] <= stop:
                return _result(entry_price, stop, side, "stop", df.index[i])
            if bar["high"] >= target:
                return _result(entry_price, target, side, "target", df.index[i])
        else:
            if bar["high"] >= stop:
                return _result(entry_price, stop, side, "stop", df.index[i])
            if bar["low"] <= target:
                return _result(entry_price, target, side, "target", df.index[i])

    # time-stop exit at final bar's close
    exit_price = df.iloc[end_idx]["close"]
    return _result(entry_price, exit_price, side, "time_stop", df.index[end_idx])


def _result(entry, exit_, side, reason, exit_time):
    pnl = (exit_ - entry) if side == "buy" else (entry - exit_)
    return {"entry": entry, "exit": exit_, "side": side, "reason": reason,
            "pnl": pnl, "exit_time": exit_time}


# ======================================================================
# MAIN BACKTEST LOOP
# ======================================================================
def run_backtest(instrument=BTConfig.INSTRUMENT):
    print(f"Fetching {BTConfig.HISTORY_COUNT} {BTConfig.GRANULARITY} candles for {instrument}...")
    df = fetch_candles(instrument, granularity=BTConfig.GRANULARITY, count=BTConfig.HISTORY_COUNT)
    df = add_atr(df)

    trades = []
    i = BTConfig.MIN_LOOKBACK
    n = len(df)

    while i < n - 1:
        slice_df = df.iloc[: i + 1]
        atr_now = df.iloc[i]["atr"]
        if pd.isna(atr_now) or atr_now == 0:
            i += 1
            continue

        side, score, _ = evaluate_slice(slice_df, instrument)

        if side and score >= BTConfig.MIN_SCORE:
            trade = simulate_trade(df, entry_idx=i + 1, side=side,
                                    atr_at_entry=atr_now, instrument=instrument)
            trade["entry_idx"] = i + 1
            trade["score"] = score
            trades.append(trade)
            # skip ahead past this trade's exit to avoid overlapping positions
            exit_idx = df.index.get_loc(trade["exit_time"])
            i = exit_idx + 1
        else:
            i += 1

    return trades, df


# ======================================================================
# STATS / REPORT
# ======================================================================
def report(trades, instrument):
    if not trades:
        print("No trades were generated over this period.")
        return

    pip = pip_size(instrument)
    pnl_pips = [t["pnl"] / pip for t in trades]
    wins = [p for p in pnl_pips if p > 0]
    losses = [p for p in pnl_pips if p <= 0]

    equity = np.cumsum(pnl_pips)
    running_max = np.maximum.accumulate(equity)
    drawdown = equity - running_max
    max_dd = drawdown.min()

    gross_win = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 1e-9
    profit_factor = gross_win / gross_loss

    print(f"\n{'='*60}\nBACKTEST REPORT — {instrument} ({BTConfig.GRANULARITY})\n{'='*60}")
    print(f"Total trades:      {len(trades)}")
    print(f"Win rate:          {len(wins) / len(trades) * 100:.1f}%")
    print(f"Avg win (pips):    {np.mean(wins):.1f}" if wins else "Avg win: n/a")
    print(f"Avg loss (pips):   {np.mean(losses):.1f}" if losses else "Avg loss: n/a")
    print(f"Total pips:        {sum(pnl_pips):.1f}")
    print(f"Profit factor:     {profit_factor:.2f}")
    print(f"Max drawdown:      {max_dd:.1f} pips")

    reasons = pd.Series([t["reason"] for t in trades]).value_counts()
    print(f"\nExit reasons:\n{reasons.to_string()}")


if __name__ == "__main__":
    trades, df = run_backtest(BTConfig.INSTRUMENT)
    report(trades, BTConfig.INSTRUMENT)

