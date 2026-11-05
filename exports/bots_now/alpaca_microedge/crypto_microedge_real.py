#!/usr/bin/env python3

# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import os
import time
import json
import asyncio
import logging
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Dict

from alpaca.trading.client import TradingClient
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("MICROEDGE_SOVEREIGN")

class Config:
    WATCHLIST = [
        "BTC/USD", "ETH/USD", "AVAX/USD", "SOL/USD", "DOGE/USD", "ADA/USD", "XRP/USD",
        "LINK/USD", "LTC/USD", "BCH/USD", "DOT/USD", "UNI/USD", "AAVE/USD", "SHIB/USD",
        "BTC/USDC", "ETH/USDC", "AVAX/USDC", "SOL/USDC", "DOGE/USDC", "AAVE/USDC",
        "CRV/USDC", "DOT/USDC", "GRT/USDC", "LINK/USDC", "LTC/USDC", "MKR/USDC",
        "SHIB/USDC", "UNI/USDC",
        "BTC/USDT", "ETH/USDT", "BCH/USDT", "DOGE/USDT", "LINK/USDT", "LTC/USDT",
        "ETH/BTC", "LTC/BTC", "BCH/BTC",
        "ARB/USD", "BONK/USD", "FIL/USD", "XTZ/USD", "YFI/USD", "SUSHI/USD", "CRV/USD"
    ]

    RISK_PCT              = 0.0030    
    STOP_PCT              = 0.0045    
    TARGET_PCT            = 0.0135    
    DAILY_DRAWDOWN_CAP    = 0.0150    
    MAX_OPEN_POSITIONS    = 6         
    POLLING_DELAY         = 12        
    COOLDOWN_SECONDS      = 60

    API_KEY = "REDACTED"
    SECRET_KEY = "REDACTED"
    PAPER                 = True      

active_positions: Dict[str, dict] = {}
daily_start_equity = None
last_trade_time = {}
last_midnight_check = -1

trade_client = TradingClient(Config.API_KEY, Config.SECRET_KEY, paper=Config.PAPER)
url_override = "https://data.alpaca.markets" if not Config.PAPER else "https://data.sandbox.alpaca.markets"
hist_client = CryptoHistoricalDataClient(Config.API_KEY, Config.SECRET_KEY, url_override=url_override)

def is_symbol_benched(symbol):
    blacklist_file = "/home/moonrider1968/bots/killer_beast_sniper/brain_blacklist.json"
    if os.path.exists(blacklist_file):
        try:
            with open(blacklist_file, "r") as f:
                data = json.load(f)
                return symbol in data.get("blacklisted_symbols", [])
        except:
            return False
    return False

def log_settled_trade(symbol, engine, side, entry, exit_price, units, r_multiple):
    pnl_file = "fleet_pnl_telemetry.json"
    raw_pnl = (exit_price - entry) * side * units

    trade_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "engine": engine,
        "side": "LONG" if side == 1 else "SHORT",
        "entry_price": float(entry),
        "exit_price": float(exit_price),
        "units": float(units),
        "realized_pnl": round(float(raw_pnl), 2),
        "r_multiple": round(float(r_multiple), 2)
    }

    try:
        history = []
        if os.path.exists(pnl_file):
            with open(pnl_file, "r") as f: history = json.load(f)
        history.append(trade_record)
        if len(history) > 1000: history.pop(0)
        with open(pnl_file, "w") as f: json.dump(history, f, indent=4)
        log.info(f"💾 TELEMETRY SAVED | {symbol} PnL: ${trade_record['realized_pnl']}")
    except Exception as e:
        log.error(f"Telemetry log write failure: {e}")

def compute_technical_analytics(df):
    if df is None or len(df) < 40: return None
    try:
        c = df["close"]
        highs, lows = df["high"], df["low"]
        df["ema9"] = c.ewm(span=9, adjust=False).mean()
        df["ema21"] = c.ewm(span=21, adjust=False).mean()
        box_high, box_low = highs.tail(30).max(), lows.tail(30).min()
        box_position = (c.iloc[-1] - box_low) / ((box_high - box_low) + 1e-9)
        typical = (df["high"] + df["low"] + c) / 3.0
        vwap = ((typical * df["volume"]).cumsum() / (df["volume"].cumsum() + 1e-9)).iloc[-1]
        return {
            "close": c.iloc[-1], "ema9": df["ema9"].iloc[-1], "ema21": df["ema21"].iloc[-1],
            "box_high": box_high, "box_low": box_low, "box_position": box_position, "vwap": vwap
        }
    except: return None

def manage_exits(symbol, current_price):
    if symbol not in active_positions: return
    pos = active_positions[symbol]
    side = pos["side"]
    entry = pos["entry"]
    units = pos["units"]

    r_distance = entry * Config.STOP_PCT
    price_progress = (current_price - entry) if side == 1 else (entry - current_price)
    
    if price_progress >= (r_distance * 1.5) and not pos.get("breach_lock_be", False):
        pos["virtual_stop"] = entry  
        pos["breach_lock_be"] = True
        log.info(f"🛡️ LOCKING BREAKEVEN | {symbol} stop moved to entry.")

    if price_progress >= (r_distance * 2.0) and not pos.get("partial_taken", False):
        partial_qty = round(units * 0.45, 4 if "BTC" in symbol or "ETH" in symbol else 2)
        if partial_qty > 0:
            try:
                exit_side = OrderSide.SELL if side == 1 else OrderSide.BUY
                trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=partial_qty, side=exit_side, time_in_force=TimeInForce.GTC))
                pos["units"] -= partial_qty
                pos["partial_taken"] = True
                log_settled_trade(symbol, "VIRTUAL_SCALP_PARTIAL", side, entry, current_price, partial_qty, 2.0)
            except Exception as e: log.error(f"Failed partial scale out: {e}")

    if (side == 1 and current_price >= pos["target"]) or (side == -1 and current_price <= pos["target"]):
        log.info(f"🎯 CEILING TARGET CAPTURED | Closing remaining units on {symbol}.")
        log_settled_trade(symbol, "VIRTUAL_SCALP_FINAL", side, entry, current_price, pos["units"], 3.0)
        active_positions.pop(symbol)
        return

    is_breached = (side == 1 and current_price <= pos["virtual_stop"]) or (side == -1 and current_price >= pos["virtual_stop"])
    if is_breached:
        if pos.get("breach_count", 0) == 0:
            pos["breach_count"] = 1
        else:
            log.critical(f"🛑 STOP BREACH | Liquidating units on {symbol}.")
            try:
                exit_side = OrderSide.SELL if side == 1 else OrderSide.BUY
                trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=pos["units"], side=exit_side, time_in_force=TimeInForce.GTC))
                log_settled_trade(symbol, "VIRTUAL_SCALP_STOP", side, entry, current_price, pos["units"], -1.0)
            except Exception as e: log.error(f"Liquidation error: {e}")
            active_positions.pop(symbol)
    else:
        if pos.get("breach_count", 0) > 0: pos["breach_count"] = 0

async def core_loop():
    global daily_start_equity, last_midnight_check
    log.info("🚀 CRYPTO SCALPER V26.6.2 | Drawdown Recalibrated & Operational.")

    while True:
        try:
            acct = trade_client.get_account()
            equity = float(acct.equity)
            
            # 🌟 SYNCHRONIZED CALIBRATION GATING: Check and reset daily baseline at midnight
            current_day = datetime.now(timezone.utc).day
            if daily_start_equity is None or current_day != last_midnight_check:
                daily_start_equity = equity
                last_midnight_check = current_day
                log.info(f"📊 RECALIBRATING INTRA-DAY BASELINE | Daily open balance locked: ${daily_start_equity:,.2f}")

            # Safety check: Prevent false triggers on micro-fluctuations at startup
            if equity < daily_start_equity and daily_start_equity > 0:
                drawdown = (daily_start_equity - equity) / daily_start_equity
                if drawdown > Config.DAILY_DRAWDOWN_CAP:
                    log.critical(f"🚨 HARD CIRCUIT BREAKER ACTIVATED | Drawdown: {drawdown*100:.2f}% | Sleeping 60m...")
                    await asyncio.sleep(3600); continue

            for symbol in Config.WATCHLIST:
                if symbol in active_positions or len(active_positions) >= Config.MAX_OPEN_POSITIONS: continue
                if symbol in last_trade_time and time.time() - last_trade_time[symbol] < Config.COOLDOWN_SECONDS: continue
                if is_symbol_benched(symbol): continue

                try:
                    req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(5, TimeFrameUnit.Minute), limit=120)
                    res = hist_client.get_crypto_bars(req)
                    if res.df is None or res.df.empty: continue
                    df = res.df.xs(symbol, level="symbol") if isinstance(res.df.index, pd.MultiIndex) else res.df
                except: continue

                stats = compute_technical_analytics(df)
                if not stats: continue

                current_price = stats["close"]
                box = stats["box_position"]
                side = 1 if box <= 0.15 else -1 if box >= 0.85 else 0

                if side != 0:
                    if (side == 1 and current_price > stats["ema9"] and current_price < stats["vwap"]) or \
                       (side == -1 and current_price < stats["ema9"] and current_price > stats["vwap"]):
                        
                        v_stop = current_price * (1.0 - Config.STOP_PCT) if side == 1 else current_price * (1.0 + Config.STOP_PCT)
                        v_tp = current_price * (1.0 + Config.TARGET_PCT) if side == 1 else current_price * (1.0 - Config.TARGET_PCT)
                        
                        raw_size = (equity * Config.RISK_PCT) / (current_price * Config.STOP_PCT)
                        rounding = 4 if "BTC" in symbol or "ETH" in symbol else 0 if ("SHIB" in symbol or "BONK" in symbol) else 2
                        size = round(raw_size, rounding) if rounding > 0 else int(raw_size)

                        if size > 0:
                            try:
                                trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=size, side=OrderSide.BUY if side == 1 else OrderSide.SELL, time_in_force=TimeInForce.GTC))
                                active_positions[symbol] = {
                                    "side": side, "units": size, "entry": current_price, "virtual_stop": v_stop, "target": v_tp,
                                    "breach_count": 0, "breach_lock_be": False, "partial_taken": False
                                }
                                last_trade_time[symbol] = time.time()
                                log.info(f"🚀 EXECUTED SCALPER ENTRY | {symbol} | Units: {size}")
                            except Exception as e: log.error(f"Order entry rejected: {e}")

            open_symbols = list(active_positions.keys())
            if open_symbols:
                try:
                    exit_req = CryptoBarsRequest(symbol_or_symbols=open_symbols, timeframe=TimeFrame(1, TimeFrameUnit.Minute), limit=1)
                    exit_res = hist_client.get_crypto_bars(exit_req)
                    if exit_res.df is not None and not exit_res.df.empty:
                        for sym in open_symbols:
                            snap_df = exit_res.df.xs(sym, level="symbol") if isinstance(exit_res.df.index, pd.MultiIndex) else exit_res.df
                            if not snap_df.empty: manage_exits(sym, snap_df["close"].iloc[-1])
                except: pass

            await asyncio.sleep(Config.POLLING_DELAY)
        except Exception as e: log.error(f"Global Core Error: {e}"); await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(core_loop())
