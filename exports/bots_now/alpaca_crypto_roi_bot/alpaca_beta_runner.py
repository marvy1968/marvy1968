#!/usr/bin/env python3
"""
================================================================================
BETA CORE_N27 PRODUCTION ALPACA EXECUTION RUNNER
================================================================================
Certified Architecture:
  • Universe: CORE_N27 (Excludes young unseasoned tokens VVV, HYPE, PUMP)
  • Regime Filter: BTC/USD Close(t-1) > EMA20 > SMA50
  • Liquidity Floor: 30-day Median Daily Dollar Volume >= $250,000
  • Breakout Rule: Close(t-1) > Donchian High(30)
  • Risk Governor: 0.50% NAV Risk per trade | Max 3 Positions | 40% NAV Cap
  • Stop Logic: Initial 2.0x ATR(14) | Trailing Stop Ratchet Close(t-1) - 2.0x ATR
  • Execution Priority: Gap-Stop Liquidation > Macro Exit > New Breakout Entries
================================================================================
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

# --- Setup Logging ---
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"beta_runner_{datetime.now(timezone.utc).strftime('%Y%m%d')}.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("BetaRunner")

# --- Constants & Universe ---
CORE_N27 = [
    "AAVE/USD", "ADA/USD", "AVAX/USD", "BAT/USD", "BCH/USD", "BNB/USD", "BTC/USD",
    "CRV/USD", "DOGE/USD", "DOT/USD", "ENA/USD", "ETH/USD", "FET/USD", "GRT/USD",
    "LINK/USD", "LTC/USD", "NEAR/USD", "ONDO/USD", "PEPE/USD", "POL/USD", "SHIB/USD",
    "SOL/USD", "SUI/USD", "TAO/USD", "TRX/USD", "UNI/USD", "XRP/USD"
]

RISK_PER_TRADE = 0.0050  # 0.50% Certified Risk Governor
MAX_POSITIONS = 3
MAX_NAV_PCT_PER_TRADE = 0.40
LIQUIDITY_FLOOR = 250_000.0
LOOKBACK_DONCHIAN = 30
ATR_MULT = 2.0
STATE_FILE = STATE_DIR / "beta_positions.json"


def load_environment():
    load_dotenv(override=True)
    api_key = os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("APCA_API_SECRET_KEY")
    paper = os.getenv("ALPACA_PAPER", "true").lower() in ("true", "1", "yes")

    if not api_key or not secret_key:
        logger.error("Missing APCA_API_KEY_ID or APCA_API_SECRET_KEY in environment.")
        sys.exit(1)

    trading_client = TradingClient(api_key, secret_key, paper=paper)
    data_client = CryptoHistoricalDataClient(api_key, secret_key)
    logger.info(f"Connected to Alpaca (Paper={paper})")
    return trading_client, data_client


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading state file {STATE_FILE}: {e}")
    return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving state file {STATE_FILE}: {e}")


def fetch_historical_bars(data_client, symbols, days=60):
    """Fetches daily bars for all candidate symbols + BTC/USD."""
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    
    req = CryptoBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start_dt,
        end=end_dt
    )
    
    try:
        bars = data_client.get_crypto_bars(req)
        df = bars.df
        if df.empty:
            logger.error("Received empty bars dataframe from Alpaca Data API.")
            return {}
        
        # Parse multi-index dataframe into dict of DataFrames
        dfs = {}
        for sym in symbols:
            # Handle standard slash format and Alpaca symbol formatting
            raw_sym = sym.replace("/", "")
            if sym in df.index.levels[0]:
                sub_df = df.loc[sym].copy()
            elif raw_sym in df.index.levels[0]:
                sub_df = df.loc[raw_sym].copy()
            else:
                continue
            
            sub_df.sort_index(inplace=True)
            # True Range & ATR14
            sub_df["tr"] = np.maximum(
                sub_df["high"] - sub_df["low"],
                np.maximum((sub_df["high"] - sub_df["close"].shift(1)).abs(),
                           (sub_df["low"] - sub_df["close"].shift(1)).abs())
            )
            sub_df["atr14"] = sub_df["tr"].rolling(14).mean()
            sub_df["ema20"] = sub_df["close"].ewm(span=20, adjust=False).mean()
            sub_df["sma50"] = sub_df["close"].rolling(50).mean()
            sub_df[f"donch_high_{LOOKBACK_DONCHIAN}"] = sub_df["high"].shift(1).rolling(LOOKBACK_DONCHIAN).max()
            sub_df["quote_volume"] = sub_df["close"] * sub_df["volume"]
            sub_df["med_vol_30d"] = sub_df["quote_volume"].rolling(30, min_periods=30).median()
            dfs[sym] = sub_df
            
        return dfs
    except Exception as e:
        logger.error(f"Failed to fetch market data: {e}")
        return {}


def run_strategy_cycle(dry_run=False):
    trading_client, data_client = load_environment()
    
    # 1. Audit Live Account
    account = trading_client.get_account()
    nav = float(account.portfolio_value)
    cash = float(account.cash)
    logger.info(f"Account NAV: ${nav:,.2f} | Available Cash: ${cash:,.2f}")
    
    # 2. Fetch Data
    symbols_to_fetch = list(set(CORE_N27 + ["BTC/USD"]))
    dfs = fetch_historical_bars(data_client, symbols_to_fetch, days=75)
    
    if "BTC/USD" not in dfs or len(dfs["BTC/USD"]) < 50:
        logger.error("Insufficient BTC/USD historical data for Macro Bull regime filter.")
        return
    
    # 3. Macro Regime Filter
    btc_df = dfs["BTC/USD"]
    # t-1 closed candle
    btc_prev = btc_df.iloc[-2] if len(btc_df) >= 2 else btc_df.iloc[-1]
    macro_bull = bool(btc_prev["close"] > btc_prev["ema20"] > btc_prev["sma50"])
    logger.info(f"BTC Macro Filter: {'BULLISH' if macro_bull else 'BEARISH/DEFENSIVE'} "
                f"(Close: ${btc_prev['close']:,.2f}, EMA20: ${btc_prev['ema20']:,.2f}, SMA50: ${btc_prev['sma50']:,.2f})")
    
    # 4. Load Saved Positions State
    saved_state = load_state()
    
    # Audit Broker Open Positions
    alpaca_positions = {p.symbol: p for p in trading_client.get_all_positions()}
    
    # --- PHASE 1: EVALUATE EXITS (GAP-STOP & TRAILING STOP & MACRO EXIT) ---
    for sym in list(saved_state.keys()):
        pos_info = saved_state[sym]
        raw_sym = sym.replace("/", "")
        
        if sym not in dfs or len(dfs[sym]) < 2:
            continue
            
        df = dfs[sym]
        today_bar = df.iloc[-1]
        prev_bar = df.iloc[-2]
        current_open = float(today_bar["open"])
        current_low = float(today_bar["low"])
        current_close = float(today_bar["close"])
        stop_px = float(pos_info["stop_px"])
        
        exit_reason = None
        # Gap-Stop Priority
        if current_open <= stop_px:
            exit_reason = "gap_stop"
        elif current_low <= stop_px:
            exit_reason = "atr_stop"
        elif not macro_bull:
            exit_reason = "macro_exit"
            
        if exit_reason:
            logger.info(f"🚨 EXIT TRIGGERED for {sym} | Reason: {exit_reason} | Stop: ${stop_px:.4f} | Open: ${current_open:.4f}")
            if not dry_run and (sym in alpaca_positions or raw_sym in alpaca_positions):
                qty_to_close = float(alpaca_positions.get(sym, alpaca_positions.get(raw_sym)).qty)
                order_req = MarketOrderRequest(
                    symbol=raw_sym,
                    qty=qty_to_close,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.GTC
                )
                trading_client.submit_order(order_req)
                logger.info(f"Submitted SELL order for {qty_to_close} {sym}")
            del saved_state[sym]
        else:
            # Phase 3: Trailing Stop Ratchet
            atr_now = float(prev_bar["atr14"]) if pd.notna(prev_bar["atr14"]) else None
            if atr_now:
                cand_stop = float(prev_bar["close"]) - (ATR_MULT * atr_now)
                if cand_stop > stop_px:
                    logger.info(f"📈 Ratchet Trailing Stop for {sym}: ${stop_px:.4f} -> ${cand_stop:.4f}")
                    saved_state[sym]["stop_px"] = cand_stop
                    
    save_state(saved_state)
    
    # --- PHASE 2: EVALUATE BREAKOUT ENTRIES ---
    active_count = len(saved_state)
    slots_available = MAX_POSITIONS - active_count
    logger.info(f"Active Positions: {active_count}/{MAX_POSITIONS} | Slots Available: {slots_available}")
    
    if macro_bull and slots_available > 0:
        candidates = []
        for sym in CORE_N27:
            if sym in saved_state or sym == "BTC/USD":
                continue
            if sym not in dfs or len(dfs[sym]) < 35:
                continue
            
            df = dfs[sym]
            prev = df.iloc[-2]
            today = df.iloc[-1]
            
            med_vol = prev.get("med_vol_30d")
            donch = prev.get(f"donch_high_{LOOKBACK_DONCHIAN}")
            atr = prev.get("atr14")
            
            if any(pd.isna(x) for x in [med_vol, donch, atr]) or float(med_vol) < LIQUIDITY_FLOOR:
                continue
                
            prev_close = float(prev["close"])
            breakout_lvl = float(donch)
            
            if prev_close > breakout_lvl:
                strength = (prev_close / breakout_lvl) - 1.0
                candidates.append((strength, sym, prev, today))
                
        # Rank by breakout strength
        candidates.sort(key=lambda x: x[0], reverse=True)
        logger.info(f"Scanned {len(CORE_N27)} assets: Found {len(candidates)} qualified breakout candidates.")
        
        for strength, sym, prev, today in candidates[:slots_available]:
            entry_px = float(today["open"])
            atr_val = float(prev["atr14"])
            init_risk_prs = ATR_MULT * atr_val
            
            if init_risk_prs <= 0 or entry_px <= init_risk_prs:
                continue
                
            stop_px = entry_px - init_risk_prs
            dollar_risk = nav * RISK_PER_TRADE
            
            # Sizing Governor: Risk-based vs 40% NAV ceiling vs available cash
            qty_risk = dollar_risk / init_risk_prs
            qty_nav_cap = (nav * MAX_NAV_PCT_PER_TRADE) / entry_px
            qty_cash = (cash * 0.95) / entry_px  # 5% buffer for fees
            
            qty = min(qty_risk, qty_nav_cap, qty_cash)
            notional = qty * entry_px
            
            if qty <= 0 or notional > cash:
                logger.warning(f"Skipping {sym}: Insufficient cash (${cash:,.2f}) for notional ${notional:,.2f}")
                continue
                
            logger.info(f"🚀 ENTRY SIGNAL: {sym} | Strength: +{strength*100:.2f}% | Entry: ${entry_px:.4f} | Stop: ${stop_px:.4f} | Qty: {qty:.4f} (${notional:,.2f})")
            
            if not dry_run:
                raw_sym = sym.replace("/", "")
                order_req = MarketOrderRequest(
                    symbol=raw_sym,
                    qty=round(qty, 6),
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.GTC
                )
                try:
                    trading_client.submit_order(order_req)
                    cash -= notional
                    saved_state[sym] = {
                        "entry_dt": datetime.now(timezone.utc).isoformat(),
                        "entry_px": entry_px,
                        "qty": qty,
                        "stop_px": stop_px,
                        "initial_risk": dollar_risk,
                        "initial_risk_prs": init_risk_prs
                    }
                    save_state(saved_state)
                    logger.info(f"Order submitted and state saved for {sym}")
                except Exception as e:
                    logger.error(f"Failed to submit BUY order for {sym}: {e}")
            else:
                logger.info(f"[DRY RUN] Would execute BUY for {qty:.4f} {sym} @ ${entry_px:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BETA CORE_N27 Alpaca Execution Runner")
    parser.add_argument("--dry-run", action="store_true", help="Scan and audit signals without submitting live orders.")
    args = parser.parse_args()
    
    logger.info("=" * 80)
    logger.info(f"Starting Beta Execution Cycle (Mode: {'DRY RUN' if args.dry_run else 'LIVE PAPER EXECUTION'})")
    logger.info("=" * 80)
    run_strategy_cycle(dry_run=args.dry_run)
    logger.info("Cycle complete.")
