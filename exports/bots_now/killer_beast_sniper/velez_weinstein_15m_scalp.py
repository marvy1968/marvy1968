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
import logging
import asyncio
import math
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta, time as dtime
from typing import Dict, Optional, Tuple

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("VELEZ_INTRADAY_ENGINE")

class Config:
    BENCHMARK             = "SPY"
    RISK_PCT              = 0.0100    # 1.0% Total Equity Risk per setup
    DAILY_DRAWDOWN_CAP    = 0.0200    # 2.0% Daily Circuit Breaker
    MAX_OPEN_POSITIONS    = 10        # Up to 10 max concurrent open trades
    POLLING_DELAY         = 15
    REENTRY_COOLDOWN_MINS = 30
    BATCH_CHUNK_SIZE      = 25        # Small batch size to avoid SSL EOF timeouts

    # ADX Thresholds
    ADX_STRONG_THRESHOLD  = 25.0

    MARKET_START_SAFE     = dtime(9, 35)
    EOD_EXIT_TIME         = dtime(15, 50)
    MARKET_CLOSE_TIME     = dtime(16, 0)

    API_KEY = "REDACTED"
    SECRET_KEY = "REDACTED"
    PAPER                 = True

    STATE_FILE            = "velez_scalp_state.json"
    HOTLIST_FILE          = "stage2_hotlist.json"

active_positions: Dict[str, dict] = {}
reentry_cooldowns: Dict[str, datetime] = {}
daily_start_equity = None

trade_client = TradingClient(Config.API_KEY, Config.SECRET_KEY, paper=Config.PAPER)
hist_client  = StockHistoricalDataClient(Config.API_KEY, Config.SECRET_KEY)

def load_state():
    global active_positions
    if os.path.exists(Config.STATE_FILE):
        try:
            with open(Config.STATE_FILE, "r") as f:
                active_positions = json.load(f)
                log.info(f"💾 PERSISTENCE LOADED: Restored {len(active_positions)} active position trackers.")
        except Exception as e:
            log.error(f"State load error: {e}")

def save_state():
    try:
        with open(Config.STATE_FILE, "w") as f:
            json.dump(active_positions, f, indent=4)
    except Exception as e:
        log.error(f"State save error: {e}")

def load_hotlist():
    if os.path.exists(Config.HOTLIST_FILE):
        try:
            with open(Config.HOTLIST_FILE, "r") as f:
                data = json.load(f)
                return data.get("hotlist", [])
        except Exception: pass
    return ["AAPL", "MSFT", "NVDA", "AVGO", "AMZN", "META", "GOOGL", "TSLA"]

def calculate_adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculates ADX, +DI, and -DI using Wilder's Smoothing method."""
    high, low, close = df["high"], df["low"], df["close"]
    
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9))
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9))

    dx = 100 * ((plus_di - minus_di).abs() / ((plus_di + minus_di) + 1e-9))
    adx = dx.ewm(alpha=1/period, adjust=False).mean()

    return adx, plus_di, minus_di

def compute_technical_indicators(df_stock: pd.DataFrame, df_spy: pd.DataFrame) -> Optional[dict]:
    if df_stock is None or len(df_stock) < 200: return None
    try:
        c = df_stock["close"]
        o = df_stock["open"]
        h = df_stock["high"]
        l = df_stock["low"]

        # 1. Moving Averages
        sma20 = c.rolling(20).mean()
        sma200 = c.rolling(200).mean()

        # 2. 20 SMA Slope (5-bar lookback)
        sma20_curr = sma20.iloc[-1]
        sma20_prev = sma20.iloc[-6]
        sma20_slope_pct = ((sma20_curr - sma20_prev) / (sma20_prev + 1e-9)) * 100

        # 3. Mansfield Relative Strength vs SPY
        common_idx = c.index.intersection(df_spy.index)
        if len(common_idx) < 50: return None
        raw_rs = (c.loc[common_idx] / df_spy["close"].loc[common_idx]) * 100
        mansfield_rs = ((raw_rs / (raw_rs.rolling(50).mean() + 1e-9)) - 1) * 100
        curr_mrs = mansfield_rs.iloc[-1]

        # 4. ADX Calculation
        adx_series, plus_di_series, minus_di_series = calculate_adx(df_stock, period=14)
        curr_adx = float(adx_series.iloc[-1])
        curr_plus_di = float(plus_di_series.iloc[-1])
        curr_minus_di = float(minus_di_series.iloc[-1])

        # ATR Calculation
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean().iloc[-1]

        curr_close = float(c.iloc[-1])
        curr_open = float(o.iloc[-1])
        curr_sma200 = float(sma200.iloc[-1])

        # --- 4-INDICATOR CONFLUENCE CHECK ---
        macro_ok = (curr_close > sma20_curr) and (sma20_curr > curr_sma200) # Price > 20 MA > 200 MA
        slope_ok = sma20_slope_pct > 0.10                                   # 20 MA curving up
        mrs_ok   = curr_mrs > 0.0                                          # Outperforming SPY
        adx_ok   = (curr_adx >= Config.ADX_STRONG_THRESHOLD) and (curr_plus_di > curr_minus_di) # ADX Trend Power

        is_strong_trend = macro_ok and slope_ok and mrs_ok and adx_ok

        return {
            "close": curr_close,
            "open": curr_open,
            "sma20": float(sma20_curr),
            "sma200": curr_sma200,
            "sma20_slope": sma20_slope_pct,
            "mansfield_rs": float(curr_mrs),
            "adx": curr_adx,
            "plus_di": curr_plus_di,
            "minus_di": curr_minus_di,
            "atr": float(atr14),
            "is_green_bar": curr_close > curr_open,
            "is_strong_trend": is_strong_trend  # 🎯 All 4 indicators aligned
        }
    except Exception as e:
        log.error(f"Indicator calculation error: {e}")
        return None

def manage_stair_step_exits(symbol: str, stats: dict, now_dt: datetime, current_time: dtime):
    if symbol not in active_positions: return
    pos = active_positions[symbol]
    
    current_price = stats["close"]
    is_green = stats["is_green_bar"]
    total_shares = pos["total_shares"]
    active_shares = pos["active_shares"]
    sma20 = stats["sma20"]

    if current_price <= pos["virtual_stop"]:
        log.critical(f"🛑 VIRTUAL STOP BREACHED | Exiting {symbol} @ ${current_price:.2f}")
        try:
            trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=active_shares, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
        except Exception as e: log.error(f"Stop liquidation failed: {e}")
        active_positions.pop(symbol)
        reentry_cooldowns[symbol] = now_dt + timedelta(minutes=Config.REENTRY_COOLDOWN_MINS)
        save_state()
        return

    if is_green and not pos.get("bar_processed", False):
        pos["up_candles"] += 1
        pos["bar_processed"] = True
        up_count = pos["up_candles"]

        tranche_qty = math.ceil(total_shares / 3.0)
        qty_to_sell = min(tranche_qty, active_shares)

        if up_count == 1 and qty_to_sell > 0:
            log.info(f"🎯 UP-CANDLE #1 | Selling 1/3 ({qty_to_sell} sh) of {symbol} @ ${current_price:.2f}")
            try:
                trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty_to_sell, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                pos["active_shares"] -= qty_to_sell
                pos["virtual_stop"] = pos["avg_entry"]
                log.info(f"🛡️ BREAK-EVEN LOCKED on remaining {pos['active_shares']} shares.")
            except Exception as e: log.error(f"Exit 1 error: {e}")

        elif up_count == 2 and qty_to_sell > 0:
            log.info(f"🎯 UP-CANDLE #2 | Selling 2nd 1/3 ({qty_to_sell} sh) of {symbol} @ ${current_price:.2f}")
            try:
                trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty_to_sell, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                pos["active_shares"] -= qty_to_sell
            except Exception as e: log.error(f"Exit 2 error: {e}")

        elif up_count >= 3:
            # 🚀 VELEZ POWER RUNNER CHECK USING ADX
            if stats["is_strong_trend"]:
                new_trail_stop = max(pos["virtual_stop"], sma20)
                pos["virtual_stop"] = new_trail_stop
                log.info(f"🔥 VELEZ POWER RUNNER (ADX {stats['adx']:.1f}) | Holding {active_shares} sh of {symbol} | Trailing Stop at 20 MA (${new_trail_stop:.2f})")
            else:
                log.info(f"🎯 UP-CANDLE #3 | Liquidating final shares ({active_shares} sh) on {symbol} @ ${current_price:.2f}")
                try:
                    trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=active_shares, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
                except Exception as e: log.error(f"Exit 3 error: {e}")
                active_positions.pop(symbol)
                reentry_cooldowns[symbol] = now_dt + timedelta(minutes=Config.REENTRY_COOLDOWN_MINS)
                save_state()
                return

        save_state()

    if not is_green:
        pos["bar_processed"] = False
        if pos.get("up_candles", 0) >= 3 and stats["is_strong_trend"]:
            pos["virtual_stop"] = max(pos["virtual_stop"], sma20)
            save_state()

async def core_loop():
    global daily_start_equity
    load_state()
    log.info("🚀 TIER 2 INTRADAY ENGINE ONLINE (4-Indicator System with ADX)")

    while True:
        try:
            now_dt = datetime.now(timezone.utc)
            now_est = now_dt.astimezone(timezone(timedelta(hours=-5)))
            current_time = now_est.time()

            acct = trade_client.get_account()
            equity = float(acct.equity)
            if daily_start_equity is None or now_est.hour == 0:
                daily_start_equity = equity

            if (daily_start_equity - equity) / daily_start_equity > Config.DAILY_DRAWDOWN_CAP:
                log.critical("🚨 HARD DAILY DRAWDOWN CAP HIT | Operations Halted.")
                await asyncio.sleep(3600); continue

            watchlist = load_hotlist()
            if not watchlist:
                await asyncio.sleep(15); continue

            # Fetch SPY benchmark bars first
            spy_req = StockBarsRequest(
                symbol_or_symbols=[Config.BENCHMARK],
                timeframe=TimeFrame(15, TimeFrameUnit.Minute),
                limit=200,
                feed=DataFeed.IEX
            )
            spy_res = hist_client.get_stock_bars(spy_req)
            df_spy = spy_res.df.xs(Config.BENCHMARK, level="symbol") if isinstance(spy_res.df.index, pd.MultiIndex) else spy_res.df

            chunks = [watchlist[i:i + Config.BATCH_CHUNK_SIZE] for i in range(0, len(watchlist), Config.BATCH_CHUNK_SIZE)]

            for batch in chunks:
                res = None
                for attempt in range(3):
                    try:
                        req = StockBarsRequest(
                            symbol_or_symbols=batch,
                            timeframe=TimeFrame(15, TimeFrameUnit.Minute),
                            limit=200,
                            feed=DataFeed.IEX
                        )
                        res = hist_client.get_stock_bars(req)
                        break
                    except Exception as net_err:
                        log.warning(f"⚠️ Network warning on batch ({net_err}). Retry {attempt+1}/3...")
                        await asyncio.sleep(2)

                if res is None or res.df is None or res.df.empty: continue

                for symbol in batch:
                    if current_time < Config.MARKET_START_SAFE or current_time >= Config.EOD_EXIT_TIME:
                        break

                    if symbol in reentry_cooldowns and now_dt < reentry_cooldowns[symbol]:
                        continue

                    try:
                        df_stock = res.df.xs(symbol, level="symbol") if isinstance(res.df.index, pd.MultiIndex) else res.df
                        stats = compute_technical_indicators(df_stock, df_spy)
                        if not stats: continue

                        close, sma20, atr = stats["close"], stats["sma20"], stats["atr"]

                        z1 = sma20 - (0.50 * atr)
                        z2 = sma20 - (1.00 * atr)
                        z3 = sma20 - (1.50 * atr)

                        if symbol not in active_positions and close > z1:
                            continue

                        stop_price = close - (1.50 * atr)
                        total_target_shares = int(math.floor((equity * Config.RISK_PCT) / (close - stop_price + 1e-9)))
                        tranche_shares = max(1, int(total_target_shares / 3.0))

                        # Buy Tranche 1 only if in discount zone AND trend passes confluence
                        if symbol not in active_positions and close <= z1 and len(active_positions) < Config.MAX_OPEN_POSITIONS:
                            trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=tranche_shares, side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                            active_positions[symbol] = {
                                "avg_entry": close,
                                "total_shares": tranche_shares,
                                "active_shares": tranche_shares,
                                "tranches_bought": 1,
                                "virtual_stop": stop_price,
                                "up_candles": 0,
                                "bar_processed": False
                            }
                            log.info(f"🟢 STAGE 2 DIP BOUGHT (Tranche 1/3) | {symbol} @ ${close:.2f} | ADX: {stats['adx']:.1f}")
                            save_state()

                        elif symbol in active_positions:
                            pos = active_positions[symbol]
                            if pos["tranches_bought"] == 1 and close <= z2:
                                trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=tranche_shares, side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                                new_total = pos["active_shares"] + tranche_shares
                                pos["avg_entry"] = ((pos["avg_entry"] * pos["active_shares"]) + (close * tranche_shares)) / new_total
                                pos["total_shares"] += tranche_shares
                                pos["active_shares"] = new_total
                                pos["tranches_bought"] = 2
                                log.info(f"🟢 SCALE-IN (Tranche 2/3) | {symbol} @ ${close:.2f} | New Avg: ${pos['avg_entry']:.2f}")
                                save_state()

                            elif pos["tranches_bought"] == 2 and close <= z3:
                                trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=tranche_shares, side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                                new_total = pos["active_shares"] + tranche_shares
                                pos["avg_entry"] = ((pos["avg_entry"] * pos["active_shares"]) + (close * tranche_shares)) / new_total
                                pos["total_shares"] += tranche_shares
                                pos["active_shares"] = new_total
                                pos["tranches_bought"] = 3
                                log.info(f"🟢 SCALE-IN (Tranche 3/3) | {symbol} @ ${close:.2f} | Final Avg: ${pos['avg_entry']:.2f}")
                                save_state()

                            manage_stair_step_exits(symbol, stats, now_dt, current_time)

                    except Exception: continue

            await asyncio.sleep(Config.POLLING_DELAY)

        except Exception as e:
            log.error(f"Global loop error: {e}")
            await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(core_loop())
