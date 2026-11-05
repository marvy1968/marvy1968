import sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import oanda_zone_bot as bot

# SAFETY: force dry-run for the entire backtest, regardless of .env.
# Without this, a backtest on an account with DRY_RUN=false would attempt
# to transmit REAL ORDERS while replaying history.
bot.Config.DRY_RUN = True

import logging
logging.getLogger("oanda_zone_bot").setLevel(logging.WARNING)

class BTConfig:
    INSTRUMENTS = bot.Config.INSTRUMENTS
    GRANULARITY = bot.Config.GRANULARITY
    HISTORY_COUNT = 5000
    STARTING_CAPITAL = 100000.0
    MIN_LOOKBACK = max(bot.Config.BB_PERIOD, 50) + bot.Config.SWING_N

def fetch_all_history(client):
    data = {}
    for instrument in BTConfig.INSTRUMENTS:
        print(f"Fetching {BTConfig.HISTORY_COUNT} {BTConfig.GRANULARITY} candles for {instrument}...")
        df = bot.fetch_candles(client, instrument, granularity=BTConfig.GRANULARITY, count=BTConfig.HISTORY_COUNT)
        if df.empty:
            print(f"  WARNING: no data for {instrument}, skipping.")
            continue
        data[instrument] = bot.add_atr(df)
    return data

def build_event_timeline(data):
    events = []
    for instrument, df in data.items():
        for i in range(BTConfig.MIN_LOOKBACK, len(df)):
            events.append((df.index[i], instrument, i))
    events.sort(key=lambda e: e[0])
    return events

def run_backtest(data):
    events = build_event_timeline(data)
    print(f"Walking {len(events)} bar-events across {len(data)} instruments...\n")
    state = {"reference_equity": BTConfig.STARTING_CAPITAL, "paused_until": None,
             "trade_log": [], "positions": {}}
    nav = BTConfig.STARTING_CAPITAL
    nav_curve = [(events[0][0] if events else datetime.now(timezone.utc), nav)]
    stats = {"evaluated": 0, "entries_accepted": 0, "blocked_by_breaker": 0,
             "blocked_by_rollover": 0, "blocked_no_signal": 0, "blocked_tier_or_opposite": 0}
    prev_len = 0
    for i, (ts, instrument, bar_idx) in enumerate(events):
        df_slice = data[instrument].iloc[: bar_idx + 1]
        atr_now = df_slice.iloc[-1]["atr"]
        if pd.isna(atr_now) or atr_now == 0:
            continue
        bot.handle_exit_zone(None, state, instrument, df_slice, atr_now, at=ts)
        stats["evaluated"] += 1
        paused = bot.check_circuit_breaker(state, now=ts)
        rollover = bot.is_rollover_window(now=ts)
        if paused:
            stats["blocked_by_breaker"] += 1
        elif rollover:
            stats["blocked_by_rollover"] += 1
        else:
            result = bot.score_zone(df_slice, instrument)
            if not result["zone_type"] or result["score"] < bot.Config.MIN_SCORE_TO_FLAG:
                stats["blocked_no_signal"] += 1
            else:
                before = len(state["trade_log"])
                bot.handle_entry_zone(None, state, instrument, result, nav, atr_now, at=ts)
                if len(state["trade_log"]) > before:
                    stats["entries_accepted"] += 1
                else:
                    stats["blocked_tier_or_opposite"] += 1
        new_realized = sum(t["realized_pnl"] for t in state["trade_log"][prev_len:])
        if new_realized != 0:
            nav += new_realized
            nav_curve.append((ts, nav))
        prev_len = len(state["trade_log"])
        if (i + 1) % 2000 == 0:
            print(f"  ...{i+1}/{len(events)} events, NAV: ${nav:,.2f}")
    return state, nav, nav_curve, stats

def mark_open_positions(state, data, nav):
    total, open_pos = 0.0, []
    for instrument, pos in state["positions"].items():
        if pos["held_units"] <= 0:
            continue
        last = data[instrument].iloc[-1]["close"]
        d = 1 if pos["side"] == "long" else -1
        u = d * (last - pos["avg_entry_price"]) * pos["held_units"]
        total += u
        open_pos.append({"instrument": instrument, "side": pos["side"],
                         "units": round(pos["held_units"], 1),
                         "avg_entry": pos["avg_entry_price"], "last": last,
                         "unrealized": round(u, 2)})
    return nav + total, open_pos

def report(state, nav, nav_curve, stats, data):
    print(f"\n{'='*72}\nOANDA_ZONE_BOT BACKTEST REPORT\n{'='*72}")
    print(f"Instruments: {list(data.keys())}")
    print(f"Starting capital: ${BTConfig.STARTING_CAPITAL:,.2f}")
    print(f"Final NAV (realized): ${nav:,.2f}")
    print(f"Total return: {(nav - BTConfig.STARTING_CAPITAL) / BTConfig.STARTING_CAPITAL * 100:+.2f}%")
    navs = np.array([n for _, n in nav_curve])
    rmax = np.maximum.accumulate(navs)
    print(f"Peak drawdown: {((navs - rmax) / rmax * 100).min():.2f}%")
    mtm, open_pos = mark_open_positions(state, data, nav)
    if open_pos:
        print("\nOpen positions at end (marked to market):")
        for p in open_pos:
            print(f"  {p}")
        print(f"Final NAV incl. unrealized: ${mtm:,.2f} "
              f"({(mtm - BTConfig.STARTING_CAPITAL) / BTConfig.STARTING_CAPITAL * 100:+.2f}%)")
    print("\n--- Event breakdown ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    closes = [t for t in state["trade_log"] if t["side"] == "close"]
    entries = [t for t in state["trade_log"] if t["side"] in ("buy", "sell")]
    print(f"\nEntry orders (incl. pyramided tranches): {len(entries)}")
    print(f"Exit orders (stops + scale-outs): {len(closes)}")
    if closes:
        pnls = [t["realized_pnl"] for t in closes]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        print(f"Win rate on closed tranches: {len(wins)/len(closes)*100:.1f}%")
        print(f"Gross profit: ${sum(wins):,.2f}   Gross loss: ${sum(losses):,.2f}")
    if stats["blocked_by_breaker"] > 0:
        print(f"\nCircuit breaker DID trip ({stats['blocked_by_breaker']} events blocked).")
    else:
        print("\nCircuit breaker never tripped during this test.")

if __name__ == "__main__":
    if not bot.Config.ACCESS_TOKEN or not bot.Config.ACCOUNT_ID:
        print("ERROR: OANDA_API_TOKEN and OANDA_ACCOUNT_ID must be set in .env")
        sys.exit(1)
    client = bot.get_client()
    data = fetch_all_history(client)
    if not data:
        print("No data fetched. Aborting.")
        sys.exit(1)
    s, n, c, st = run_backtest(data)
    report(s, n, c, st, data)
