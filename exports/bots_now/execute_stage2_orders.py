#!/usr/bin/env python3
"""
STAGE 2 PRODUCTION EXECUTION ENGINE
Handles:
  1. Position Auditing: Updates trailing 20-day SMA stops on open holdings.
  2. Portfolio Guardrails: Max 2 positions per sector, max 5 total holdings.
  3. Half-Kelly Sizing: 0.75% NAV risk per trade against empirical win-rate.
  4. Bracket Order Submission: Day T+1 Market-On-Open with attached initial stop-loss.
"""

import os
import sys
import json
import argparse
from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
STATE_FILE = Path("/home/moonrider1968/bots/stage2_portfolio_state.json")

LARGE_DIR = Path("/home/moonrider1968/bots/data_alpaca_sip_1m")
MID_DIR = Path("/home/moonrider1968/bots/data_midcap_sip_1m")

UNIVERSE_SECTORS = {
    "AAPL": "TECH", "MSFT": "TECH", "NVDA": "TECH", "AVGO": "TECH",
    "GOOGL": "TECH", "META": "TECH", "AMZN": "TECH", "TSLA": "TECH",
    "JPM": "FIN", "GS": "FIN",
    "XOM": "ENERGY", "CVX": "ENERGY",
    "WMT": "STAPLES", "COST": "STAPLES",
    "CAT": "IND", "GE": "IND",
    "SMCI": "TECH", "CELH": "STAPLES", "DUOL": "TECH", "AFRM": "FIN",
    "UPST": "FIN", "MARA": "CRYPTO", "RIOT": "CRYPTO", "SYM": "IND",
    "APP": "TECH", "IOT": "TECH"
}

if not API_KEY or not SECRET_KEY:
    print("[ERROR] ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")
    sys.exit(1)

client = TradingClient(API_KEY, SECRET_KEY, paper=True)

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"positions": {}}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def load_daily_series(sym: str) -> pd.DataFrame:
    p_large = LARGE_DIR / f"{sym}_1MIN_SIP_2017_2026.parquet"
    p_mid = MID_DIR / f"{sym}_1MIN_SIP.parquet"
    p = p_large if p_large.exists() else p_mid
    if not p.exists():
        return pd.DataFrame()

    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["time_et"] = df["time"].dt.tz_convert("America/New_York")
    df["date"] = df["time_et"].dt.date

    daily = df.groupby("date").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).reset_index().sort_values("date").reset_index(drop=True)

    return daily

def run_portfolio_cycle(live_execute: bool = False):
    print("=" * 80)
    print(f"STAGE 2 PORTFOLIO CYCLE — [{'LIVE PAPER TRADING' if live_execute else 'DRY RUN'}]")
    print("=" * 80)

    acct = client.get_account()
    nav = float(acct.portfolio_value)
    cash = float(acct.cash)
    print(f"Account NAV: ${nav:,.2f} | Buying Power: ${float(acct.buying_power):,.2f}")

    state = load_state()
    open_positions = state.get("positions", {})

    # -------------------------------------------------------------
    # 1. Audit Existing Holdings & Update Trailing Stops
    # -------------------------------------------------------------
    print("\n[1] AUDITING OPEN POSITIONS & TRAILING 20-DAY SMA STOPS:")
    sector_counts = {}

    for sym, pos in list(open_positions.items()):
        sec = UNIVERSE_SECTORS.get(sym, "OTHER")
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

        df = load_daily_series(sym)
        if df.empty:
            continue

        df["sma20"] = df["close"].rolling(20).mean()
        last_bar = df.iloc[-1]
        sma20_val = round(last_bar["sma20"], 2)

        # Update trailing stop upward only
        if last_bar["close"] > pos["entry_px"]:
            new_stop = max(pos["current_stop"], sma20_val)
            if new_stop > pos["current_stop"]:
                print(f"  {sym:<6} | Ratchet Stop: ${pos['current_stop']:.2f} -> ${new_stop:.2f} (20-day SMA)")
                pos["current_stop"] = new_stop

        # Exit trigger: Closed below 20-day SMA after profit
        if last_bar["close"] < sma20_val and last_bar["close"] > pos["entry_px"]:
            print(f"  [EXIT SIGNAL] {sym:<6} closed (${last_bar['close']:.2f}) below 20-SMA (${sma20_val:.2f})")
            if live_execute:
                client.close_position(sym)
                del open_positions[sym]
                continue

    save_state(state)

    # -------------------------------------------------------------
    # 2. Capacity Guardrails Check
    # -------------------------------------------------------------
    max_portfolio_positions = 5
    open_slots = max_portfolio_positions - len(open_positions)
    print(f"\n[2] CAPACITY STATUS: {len(open_positions)}/5 Active | Open Slots: {open_slots}")

    if open_slots <= 0:
        print(">> Portfolio fully allocated. No new positions permitted today.")
        print("=" * 80)
        return

    # -------------------------------------------------------------
    # 3. Scan for Validated Day T Breakouts
    # -------------------------------------------------------------
    print("\n[3] SCANNING UNIVERSE FOR STAGE 2 BREAKOUTS:")
    candidates = []

    for sym, sec in UNIVERSE_SECTORS.items():
        if sym in open_positions:
            continue
        if sector_counts.get(sec, 0) >= 2:
            continue  # Max 2 per sector firewall

        df = load_daily_series(sym)
        if len(df) < 160:
            continue

        df["sma150"] = df["close"].rolling(150).mean()
        df["sma150_slope"] = df["sma150"] - df["sma150"].shift(10)
        df["vol_sma50"] = df["volume"].rolling(50).mean()
        df["high_20d"] = df["high"].rolling(20).max().shift(1)
        df["low_20d"] = df["low"].rolling(20).min().shift(1)

        c = df["close"].values
        h = df["high"].values
        l = df["low"].values
        tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
        tr[0] = h[0] - l[0]
        df["atr"] = pd.Series(tr).rolling(14).mean()

        last_bar = df.iloc[-1]
        is_stage2 = (last_bar["close"] > last_bar["sma150"]) and (last_bar["sma150_slope"] > 0)
        vol_burst = last_bar["volume"] > (last_bar["vol_sma50"] * 1.3)
        breakout = last_bar["close"] > last_bar["high_20d"]

        if is_stage2 and vol_burst and breakout:
            risk_unit = max(last_bar["atr"] * 1.8, last_bar["close"] - last_bar["low_20d"])
            stop_px = round(last_bar["close"] - risk_unit, 2)
            target_px = round(last_bar["close"] + (3.0 * risk_unit), 2)

            candidates.append({
                "symbol": sym,
                "sector": sec,
                "close": round(last_bar["close"], 2),
                "stop_px": stop_px,
                "target_px": target_px,
                "risk_unit": round(risk_unit, 2),
                "volume_ratio": round(last_bar["volume"] / last_bar["vol_sma50"], 2)
            })

    candidates.sort(key=lambda x: x["volume_ratio"], reverse=True)
    admitted = candidates[:open_slots]

    if not admitted:
        print(">> Zero valid Stage 2 breakout setups found on latest bar.")
        print("=" * 80)
        return

    # -------------------------------------------------------------
    # 4. Sizing & Order Placement (Half-Kelly: 0.75% NAV Risk)
    # -------------------------------------------------------------
    print(f"\n[4] ADMITTING {len(admitted)} CANDIDATES FOR EXECUTION:")
    risk_budget_per_trade = nav * 0.0075

    for cand in admitted:
        sym = cand["symbol"]
        shares = int(risk_budget_per_trade / cand["risk_unit"])
        if shares < 1:
            continue

        est_capital = shares * cand["close"]
        print(f"  {sym:<6} ({cand['sector']}) | Qty: {shares} | Close: ${cand['close']:.2f} | Stop: ${cand['stop_px']:.2f} | Target: ${cand['target_px']:.2f} | Notional: ${est_capital:,.2f}")

        if live_execute:
            try:
                # Bracket Order: Buy at Market Open, Stop-Loss + Take-Profit pre-attached
                req = MarketOrderRequest(
                    symbol=sym,
                    qty=shares,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.GTC,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=cand["target_px"]),
                    stop_loss=StopLossRequest(stop_price=cand["stop_px"])
                )
                order = client.submit_order(req)
                print(f"    [ORDER SUBMITTED] ID: {order.id}")

                # Register in local state
                open_positions[sym] = {
                    "shares": shares,
                    "entry_px": cand["close"],
                    "current_stop": cand["stop_px"],
                    "target_px": cand["target_px"],
                    "sector": cand["sector"],
                    "opened_date": str(dt.date.today())
                }
            except Exception as e:
                print(f"    [ORDER REJECTED] {sym}: {e}")

    save_state(state)
    print("=" * 80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Execute orders via Alpaca REST API")
    args = parser.parse_args()

    run_portfolio_cycle(live_execute=args.live)
