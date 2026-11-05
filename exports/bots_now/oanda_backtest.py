import os
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# Import core components from your bot file
from oanda_zone_bot import (
    get_client, fetch_candles, add_atr, add_bollinger, detect_swings,
    get_last_swing_leg, fib_zones, score_zone, Config, EntryConfig, ExitConfig, RiskConfig, pip_size
)

def run_backtest(instrument="EUR_USD", initial_nav=100000.0, count=3000):
    print(f"Fetching up to {count} historical candles for {instrument}...")
    client = get_client()
    # Fetch a larger chunk of historical candles for backtesting
    df = fetch_candles(client, instrument, granularity="H1", count=count)
    
    if df.empty or len(df) < 100:
        print("Not enough historical data retrieved.")
        return

    df = add_atr(df)
    df = add_bollinger(df)
    
    nav = initial_nav
    cash = initial_nav
    positions = {} # instrument -> position state
    trade_log = []
    
    print(f"Starting backtest simulation on {len(df)} bars...")
    
    # Iterate chronologically through historical candles (start after warmup period)
    warmup = 50
    for i in range(warmup, len(df)):
        window = df.iloc[:i+1]
        current_bar = window.iloc[-1]
        price = current_bar["close"]
        atr_now = current_bar["atr"]
        timestamp = window.index[-1]
        
        if pd.isna(atr_now) or atr_now == 0:
            continue
            
        # --- 1. Check exits/stops for active positions ---
        if instrument in positions and positions[instrument]["held_units"] > 0:
            pos = positions[instrument]
            side = pos["side"]
            entry = pos["avg_entry_price"]
            direction = 1 if side == "long" else -1
            
            # Protective stop check
            stop_price = entry - direction * atr_now * RiskConfig.STOP_ATR_MULT
            stop_hit = (price <= stop_price) if side == "long" else (price >= stop_price)
            
            if stop_hit:
                units = pos["held_units"]
                pnl = direction * (price - entry) * units
                nav += pnl
                trade_log.append({"time": timestamp, "type": "STOP_LOSS", "side": side, "price": price, "pnl": pnl})
                positions[instrument] = {"side": None, "held_units": 0.0, "avg_entry_price": 0.0, "tier_idx": -1}
                continue
                
            # Profit target scale-outs
            # (Simplified check for backtest loop evaluation)
            for idx, (atr_mult, pct) in enumerate(ExitConfig.SCALE_OUT_LEVELS):
                if idx in pos.get("exit_tiers_fired", []):
                    continue
                target_price = entry + direction * atr_now * atr_mult
                target_hit = (price >= target_price) if side == "long" else (price <= target_price)
                if target_hit:
                    units_to_close = min(pos["held_units"], round(pos["exit_plan_base_units"] * pct))
                    if units_to_close > 0:
                        pnl = direction * (price - entry) * units_to_close
                        nav += pnl
                        pos["held_units"] -= units_to_close
                        pos.setdefault("exit_tiers_fired", []).append(idx)
                        trade_log.append({"time": timestamp, "type": f"SCALE_OUT_{idx}", "side": side, "price": price, "pnl": pnl})

        # --- 2. Evaluate new entries ---
        result = score_zone(window, instrument)
        if result["zone_type"] and result["score"] >= Config.MIN_SCORE_TO_FLAG:
            zone_side = result["zone_type"] # "buy" or "sell"
            pos_side = "long" if zone_side == "buy" else "short"
            
            pos = positions.setdefault(instrument, {"side": None, "held_units": 0.0, "avg_entry_price": 0.0, "tier_idx": -1, "exit_plan_base_units": 0.0})
            
            if pos["side"] is None or pos["side"] == pos_side:
                score = result["score"]
                target_idx = -1
                for idx, (min_score, _) in enumerate(EntryConfig.SCALE_TIERS):
                    if score >= min_score:
                        target_idx = idx
                        
                if target_idx > pos["tier_idx"]:
                    prev_pct = EntryConfig.SCALE_TIERS[pos["tier_idx"]][1] if pos["tier_idx"] >= 0 else 0.0
                    target_pct = EntryConfig.SCALE_TIERS[target_idx][1]
                    incremental_equity = nav * (target_pct - prev_pct) / 100.0
                    
                    if incremental_equity >= 1.0:
                        units_mag = incremental_equity / price
                        signed_units = units_mag if pos_side == "long" else -units_mag
                        
                        new_total = pos["held_units"] + units_mag
                        if new_total > 0:
                            pos["avg_entry_price"] = (pos["avg_entry_price"] * pos["held_units"] + price * units_mag) / new_total
                        pos["held_units"] = new_total
                        pos["side"] = pos_side
                        pos["tier_idx"] = target_idx
                        pos["exit_plan_base_units"] = new_total
                        
                        trade_log.append({"time": timestamp, "type": f"ENTRY_TIER_{target_idx}", "side": pos_side, "price": price, "units": units_mag})

    print("\n=== BACKTEST RESULTS ===")
    print(f"Instrument: {instrument}")
    print(f"Start NAV: ${initial_nav:,.2f}")
    print(f"End NAV (Approx): ${nav:,.2f}")
    print(f"Total Trades / Actions Logged: {len(trade_log)}")
    return trade_log

if __name__ == "__main__":
    run_backtest("EUR_USD")


