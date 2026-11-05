#!/usr/bin/env python3
"""
HIGH-ACCURACY UN-BOTTLENECKED STOCK DAEMON
==========================================
Filters:
 - Session VWAP Trend Alignment
 - 9/21 Fast EMA Crossover Trigger
 - RVOL > 1.2 Volume Confirmation
 - ADX > 20 Active Trend Strength Guard
"""


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
import sys
import time
import json
import logging
import pytz
from datetime import datetime
import pandas as pd
import numpy as np

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/home/moonrider1968/bots/killer_beast_sniper/top_down_daemon.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

# KEYS_MOVED_TO_ENV (2026-10-01): credentials are no longer in source. Values load from the
# process environment (systemd Environment=) or, as fallback, from /home/moonrider1968/.config/bots/top_down_stock.env (chmod 600).
def _load_private_env(_p="/home/moonrider1968/.config/bots/top_down_stock.env"):
    import os as _os
    try:
        for _l in open(_p):
            _l=_l.strip()
            if _l and not _l.startswith("#") and "=" in _l:
                _k,_v=_l.split("=",1); _os.environ.setdefault(_k.strip(), _v.strip())
    except FileNotFoundError:
        pass
_load_private_env()
API_KEY = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

UNIVERSE = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "AVGO", "META", "TSLA", "LLY", "JPM"]
MAX_DAILY_LOSS_PCT = 2.0
POSITION_TYPES = {}

def check_daily_loss_circuit_breaker():
    try:
        account = trading_client.get_account()
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        pnl_pct = ((equity - last_equity) / last_equity) * 100 if last_equity > 0 else 0.0
        if pnl_pct <= -MAX_DAILY_LOSS_PCT:
            logging.warning(f"🛑 CIRCUIT BREAKER: Daily PnL at {pnl_pct:.2f}% (Limit: -{MAX_DAILY_LOSS_PCT}%)")
            return False
        return True
    except Exception as e:
        logging.error(f"Error checking daily loss limit: {e}")
        return True

def get_market_status():
    et_tz = pytz.timezone('US/Eastern')
    now = datetime.now(et_tz)
    if now.weekday() >= 5:
        return {"is_rth": False, "can_enter": False, "must_exit_scalps": False}
        
    start_rth = now.replace(hour=9, minute=30, second=0, microsecond=0)
    entry_allowed = now.replace(hour=9, minute=45, second=0, microsecond=0)
    exit_scalps = now.replace(hour=15, minute=50, second=0, microsecond=0)
    end_rth = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    is_rth = start_rth <= now <= end_rth
    can_enter = entry_allowed <= now <= exit_scalps
    must_exit_scalps = now >= exit_scalps and is_rth
    
    return {"is_rth": is_rth, "can_enter": can_enter, "must_exit_scalps": must_exit_scalps}

def get_stock_bars(symbol, timeframe, limit=100):
    try:
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=timeframe, limit=limit)
        bars = data_client.get_stock_bars(req)
        df = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol)
        return df
    except Exception as e:
        logging.error(f"Error fetching bars for {symbol}: {e}")
        return None

def calculate_indicators(df_15m):
    df = df_15m.copy()
    
    # Relative Volume (RVOL)
    vol_sma = df['volume'].rolling(20).mean()
    df['rvol'] = df['volume'] / vol_sma.replace(0, 1e-9)
    
    # Fast / Slow Moving Averages
    df['ema_fast'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # Session VWAP
    df['date'] = pd.to_datetime(df.index).date
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    tp_vol = typical_price * df['volume']
    df['cum_tp_vol'] = tp_vol.groupby(df['date']).cumsum()
    df['cum_vol'] = df['volume'].groupby(df['date']).cumsum()
    df['vwap'] = df['cum_tp_vol'] / df['cum_vol']
    
    # ATR & ADX
    tr0 = abs(df['high'] - df['low'])
    tr1 = abs(df['high'] - df['close'].shift(1))
    tr2 = abs(df['low'] - df['close'].shift(1))
    tr = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
    df['atr'] = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean()
    
    up_move = df['high'] - df['high'].shift(1)
    down_move = df['low'].shift(1) - df['low']
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    atr_smooth = df['atr'].replace(0, 1e-9)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr_smooth)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr_smooth)
    
    denom = (plus_di + minus_di).replace(0, 1e-9)
    dx = 100 * (abs(plus_di - minus_di) / denom)
    df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean().fillna(20.0)
    
    return df

def evaluate_setup(symbol):
    m15_df = get_stock_bars(symbol, TimeFrame.Minute, limit=100)
    if m15_df is None or len(m15_df) < 50:
        return {"signal": "NO_TRADE", "reason": "Insufficient candles"}
        
    df = calculate_indicators(m15_df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    current_price = latest['close']
    atr = latest['atr']
    adx_val = latest['adx']
    rvol_val = latest['rvol']
    
    ema_bullish = (latest['ema_fast'] > latest['ema_slow']) and (prev['ema_fast'] <= prev['ema_slow'])
    ema_bearish = (latest['ema_fast'] < latest['ema_slow']) and (prev['ema_fast'] >= prev['ema_slow'])
    above_vwap = current_price > latest['vwap']
    below_vwap = current_price < latest['vwap']
    
    # Multi-Factor Confluence (High Accuracy + Active Flow)
    is_valid_volume = rvol_val >= 1.2
    is_trending = adx_val >= 20.0
    
    if ema_bullish and above_vwap and is_valid_volume and is_trending:
        return {
            "signal": "BUY_LONG",
            "trade_mode": "SCALP",
            "price": current_price,
            "risk_pct": 1.5,
            "stop_loss": round(current_price - (1.5 * atr), 2),
            "take_profit": round(current_price + (3.0 * atr), 2)
        }

    elif ema_bearish and below_vwap and is_valid_volume and is_trending:
        return {
            "signal": "SELL_SHORT",
            "trade_mode": "SCALP",
            "price": current_price,
            "risk_pct": 1.5,
            "stop_loss": round(current_price + (1.5 * atr), 2),
            "take_profit": round(current_price - (3.0 * atr), 2)
        }

    return {"signal": "NO_TRADE", "reason": "Awaiting Confluence"}

def export_signal_log(signals):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"/home/moonrider1968/bots/killer_beast_sniper/signals_{timestamp}.json"
    try:
        with open(filename, "w") as f:
            json.dump({"timestamp": datetime.now().isoformat(), "signals": signals}, f, indent=2)
    except Exception as e:
        logging.error(f"Error exporting signal log: {e}")

def main():
    logging.info("==================================================")
    logging.info("HIGH-ACCURACY UN-BOTTLENECKED STOCK DAEMON ONLINE")
    logging.info("==================================================")
    
    while True:
        status = get_market_status()
        
        if not status["is_rth"]:
            logging.info("Outside Market Hours. Sleeping 60s...")
            time.sleep(60)
            continue

        try:
            if not check_daily_loss_circuit_breaker():
                time.sleep(300)
                continue

            positions = trading_client.get_all_positions()
            open_symbols = [p.symbol for p in positions]
            
            if status["must_exit_scalps"]:
                for pos in positions:
                    if POSITION_TYPES.get(pos.symbol) == "SCALP":
                        logging.info(f"🌆 3:50 PM ET Reached: Closing Intraday Scalp for {pos.symbol}...")
                        trading_client.close_position(pos.symbol)
                        POSITION_TYPES.pop(pos.symbol, None)
            
            if status["can_enter"]:
                buying_power = float(trading_client.get_account().buying_power)
                signal_audit = []
                
                for symbol in UNIVERSE:
                    if symbol in open_symbols:
                        continue
                        
                    setup = evaluate_setup(symbol)
                    signal = setup.get("signal")
                    signal_audit.append({"symbol": symbol, "setup": setup})
                    
                    if signal in ["BUY_LONG", "SELL_SHORT"]:
                        price = setup["price"]
                        stop_loss = setup["stop_loss"]
                        take_profit = setup["take_profit"]
                        trade_mode = setup["trade_mode"]
                        
                        risk_amt = buying_power * (setup["risk_pct"] / 100.0)
                        qty = max(1, int(risk_amt / price))
                        
                        side = OrderSide.BUY if signal == "BUY_LONG" else OrderSide.SELL
                        req = MarketOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=side,
                            time_in_force=TimeInForce.GTC,
                            order_class=OrderClass.BRACKET,
                            take_profit=TakeProfitRequest(limit_price=take_profit),
                            stop_loss=StopLossRequest(stop_price=stop_loss)
                        )
                        
                        trading_client.submit_order(req)
                        POSITION_TYPES[symbol] = trade_mode
                        logging.info(f"🚀 EXECUTED {signal} for {symbol} | Qty: {qty} @ ${price:.2f} | SL: ${stop_loss} | TP: ${take_profit}")
                
                export_signal_log(signal_audit)
                        
        except Exception as e:
            logging.error(f"Error in stock daemon loop: {e}")
            
        time.sleep(60)

if __name__ == "__main__":
    main()
