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

import os, time, json, logging, asyncio, math
import pandas as pd
from datetime import datetime, timezone, timedelta, time as dtime
from typing import Dict, Optional

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("VELEZ_STANDARD_SCALPER")

class Config:
    RISK_PCT              = 0.0100
    DAILY_DRAWDOWN_CAP    = 0.0200
    MAX_OPEN_POSITIONS    = 10
    POLLING_DELAY         = 15
    REENTRY_COOLDOWN_MINS = 30
    BATCH_CHUNK_SIZE      = 25

    MARKET_START_SAFE     = dtime(9, 35)
    EOD_EXIT_TIME         = dtime(15, 50)
    MARKET_CLOSE_TIME     = dtime(16, 0)

    API_KEY = "REDACTED"
    SECRET_KEY = "REDACTED"
    PAPER                 = True

    STATE_FILE            = "velez_standard_state.json"
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
        except Exception: pass

def save_state():
    try:
        with open(Config.STATE_FILE, "w") as f:
            json.dump(active_positions, f, indent=4)
    except Exception: pass

def load_hotlist():
    if os.path.exists(Config.HOTLIST_FILE):
        try:
            with open(Config.HOTLIST_FILE, "r") as f:
                return json.load(f).get("hotlist", [])
        except Exception: pass
    return ["AAPL", "MSFT", "NVDA", "AVGO", "AMZN"]

def compute_indicators(df_stock: pd.DataFrame) -> Optional[dict]:
    if df_stock is None or len(df_stock) < 40: return None
    try:
        c, o, h, l = df_stock["close"], df_stock["open"], df_stock["high"], df_stock["low"]
        sma20 = c.rolling(20).mean()
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        return {
            "close": float(c.iloc[-1]),
            "sma20": float(sma20.iloc[-1]),
            "atr": float(atr14.iloc[-1]),
            "is_green_bar": float(c.iloc[-1]) > float(o.iloc[-1])
        }
    except Exception: return None

def manage_exits(symbol: str, stats: dict, now_dt: datetime, current_time: dtime):
    if symbol not in active_positions: return
    pos = active_positions[symbol]
    current_price = stats["close"]
    active_shares = pos["active_shares"]

    if current_price <= pos["virtual_stop"]:
        trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=active_shares, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
        active_positions.pop(symbol)
        reentry_cooldowns[symbol] = now_dt + timedelta(minutes=Config.REENTRY_COOLDOWN_MINS)
        save_state(); return

    if stats["is_green_bar"] and not pos.get("bar_processed", False):
        pos["up_candles"] += 1; pos["bar_processed"] = True
        up_count = pos["up_candles"]
        tranche_qty = math.ceil(pos["total_shares"] / 3.0)
        qty_to_sell = min(tranche_qty, active_shares)

        if up_count == 1 and qty_to_sell > 0:
            trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty_to_sell, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
            pos["active_shares"] -= qty_to_sell
            pos["virtual_stop"] = pos["avg_entry"]
        elif up_count == 2 and qty_to_sell > 0:
            trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty_to_sell, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
            pos["active_shares"] -= qty_to_sell
        elif up_count >= 3:
            trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=active_shares, side=OrderSide.SELL, time_in_force=TimeInForce.GTC))
            active_positions.pop(symbol)
            reentry_cooldowns[symbol] = now_dt + timedelta(minutes=Config.REENTRY_COOLDOWN_MINS)
            save_state(); return
        save_state()

    if not stats["is_green_bar"]: pos["bar_processed"] = False

async def core_loop():
    global daily_start_equity
    load_state()
    log.info("🚀 ENGINE 1 ACTIVE: Standard Velez 3-Bar Scalper")
    while True:
        try:
            now_dt = datetime.now(timezone.utc)
            current_time = now_dt.astimezone(timezone(timedelta(hours=-5))).time()
            acct = trade_client.get_account()
            equity = float(acct.equity)
            if daily_start_equity is None: daily_start_equity = equity

            watchlist = load_hotlist()
            chunks = [watchlist[i:i + Config.BATCH_CHUNK_SIZE] for i in range(0, len(watchlist), Config.BATCH_CHUNK_SIZE)]

            for batch in chunks:
                try:
                    req = StockBarsRequest(symbol_or_symbols=batch, timeframe=TimeFrame(15, TimeFrameUnit.Minute), limit=50, feed=DataFeed.IEX)
                    res = hist_client.get_stock_bars(req)
                    if res.df is None or res.df.empty: continue

                    for symbol in batch:
                        if current_time < Config.MARKET_START_SAFE or current_time >= Config.EOD_EXIT_TIME: break
                        if symbol in reentry_cooldowns and now_dt < reentry_cooldowns[symbol]: continue

                        df_stock = res.df.xs(symbol, level="symbol") if isinstance(res.df.index, pd.MultiIndex) else res.df
                        stats = compute_indicators(df_stock)
                        if not stats: continue

                        close, sma20, atr = stats["close"], stats["sma20"], stats["atr"]
                        z1 = sma20 - (0.50 * atr)

                        stop_price = close - (1.50 * atr)
                        total_target_shares = int(math.floor((equity * Config.RISK_PCT) / (close - stop_price + 1e-9)))
                        tranche_shares = max(1, int(total_target_shares / 3.0))

                        if symbol not in active_positions and close <= z1 and len(active_positions) < Config.MAX_OPEN_POSITIONS:
                            trade_client.submit_order(MarketOrderRequest(symbol=symbol, qty=tranche_shares, side=OrderSide.BUY, time_in_force=TimeInForce.GTC))
                            active_positions[symbol] = {
                                "avg_entry": close, "total_shares": tranche_shares, "active_shares": tranche_shares,
                                "tranches_bought": 1, "virtual_stop": stop_price, "up_candles": 0, "bar_processed": False
                            }
                            log.info(f"🟢 [BOT 1] STANDARD DIP BOUGHT | {symbol} @ ${close:.2f}")
                            save_state()
                        elif symbol in active_positions:
                            manage_exits(symbol, stats, now_dt, current_time)
                except Exception: continue
            await asyncio.sleep(Config.POLLING_DELAY)
        except Exception as e:
            await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(core_loop())
