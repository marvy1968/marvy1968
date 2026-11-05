#!/usr/bin/env python3
"""
DYNAMIC ADAPTIVE CRYPTO ENGINE
==============================
Main Brain : ADX Regime Router + Regime-Adaptive TP Scaling
Engine A   : Trend Panel (EMA 9/21, MACD, PSAR) -> 2/3 Consensus
Engine B   : Reversion Panel (Dual RSI, Bollinger, Stochastic) -> 2/3 Consensus
Exits      : Dynamic ATR Trailing Stops + Break-Even Locks + Persistent Disk State
Pairs      : BTC/USD, ETH/USD, SOL/USD
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
import pandas as pd
import numpy as np
from pathlib import Path

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, CryptoSnapshotRequest
from alpaca.data.timeframe import TimeFrame

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# KEYS_MOVED_TO_ENV (2026-10-01): credentials are no longer in source. Values load from the
# process environment (systemd Environment=) or, as fallback, from /home/moonrider1968/.config/bots/crypto_momentum.env (chmod 600).
def _load_private_env(_p="/home/moonrider1968/.config/bots/crypto_momentum.env"):
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
data_client = CryptoHistoricalDataClient()

PAIRS = ["BTC/USD", "ETH/USD", "SOL/USD"]
TRADE_QTY = {"BTC/USD": 0.01, "ETH/USD": 0.1, "SOL/USD": 1.0}
STATE_FILE = Path("/home/moonrider1968/bots/killer_beast_sniper/crypto_dual_regime_state.json")

state = {}

def load_state():
    global state
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                logging.info(f"💾 Persistent state loaded: {state}")
        except Exception as e:
            logging.error(f"Error loading state file: {e}")

def save_state():
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving state file: {e}")

def get_crypto_bars(symbol):
    try:
        req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, limit=120)
        bars = data_client.get_crypto_bars(req)
        df = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol)
        return df
    except Exception as e:
        logging.error(f"Error fetching bars for {symbol}: {e}")
        return None

def get_live_price(symbol):
    try:
        req = CryptoSnapshotRequest(symbol_or_symbols=symbol)
        snap = data_client.get_crypto_snapshot(req)
        return snap[symbol].latest_trade.price
    except Exception as e:
        logging.error(f"Error fetching live price for {symbol}: {e}")
        return None

def compute_indicators(df):
    d = df.copy()

    # --- REGIME INDICATORS ---
    plus_di = d['high'].diff()
    minus_di = -d['low'].diff()
    plus_di = np.where((plus_di > minus_di) & (plus_di > 0), plus_di, 0)
    minus_di = np.where((minus_di > plus_di) & (minus_di > 0), minus_di, 0)
    
    tr0 = abs(d['high'] - d['low'])
    tr1 = abs(d['high'] - d['close'].shift(1))
    tr2 = abs(d['low'] - d['close'].shift(1))
    tr = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
    
    atr14 = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_pdi = 100 * (pd.Series(plus_di).ewm(alpha=1/14, adjust=False).mean() / atr14)
    minus_pdi = 100 * (pd.Series(minus_di).ewm(alpha=1/14, adjust=False).mean() / atr14)
    dx = 100 * (abs(plus_pdi - minus_pdi) / (plus_pdi + minus_pdi).replace(0, 1e-9))
    d['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()

    # --- TREND INDICATORS ---
    d['ema_fast'] = d['close'].ewm(span=9, adjust=False).mean()
    d['ema_slow'] = d['close'].ewm(span=21, adjust=False).mean()
    
    ema12 = d['close'].ewm(span=12, adjust=False).mean()
    ema26 = d['close'].ewm(span=26, adjust=False).mean()
    d['macd'] = ema12 - ema26
    d['macd_signal'] = d['macd'].ewm(span=9, adjust=False).mean()

    sar_vals = np.zeros(len(d))
    sar_vals[0] = d['low'].iloc[0]
    af = 0.02
    ep = d['high'].iloc[0]
    hp = d['high'].values
    lp = d['low'].values
    is_long = True
    for i in range(1, len(d)):
        if is_long:
            sar_vals[i] = sar_vals[i-1] + af * (ep - sar_vals[i-1])
            if lp[i] < sar_vals[i]:
                is_long = False
                sar_vals[i] = ep
                ep = lp[i]
                af = 0.02
            else:
                if hp[i] > ep:
                    ep = hp[i]
                    af = min(af + 0.02, 0.20)
        else:
            sar_vals[i] = sar_vals[i-1] + af * (ep - sar_vals[i-1])
            if hp[i] > sar_vals[i]:
                is_long = True
                sar_vals[i] = ep
                ep = hp[i]
                af = 0.02
            else:
                if lp[i] < ep:
                    ep = lp[i]
                    af = min(af + 0.02, 0.20)
    d['psar'] = sar_vals

    # --- MEAN REVERSION INDICATORS ---
    delta = d['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    rs7 = gain.rolling(7).mean() / loss.rolling(7).mean().replace(0, 1e-9)
    d['rsi_fast'] = 100 - (100 / (1 + rs7))
    rs14 = gain.rolling(14).mean() / loss.rolling(14).mean().replace(0, 1e-9)
    d['rsi_slow'] = 100 - (100 / (1 + rs14))

    d['bb_mid'] = d['close'].rolling(20).mean()
    d['bb_std'] = d['close'].rolling(20).std()
    d['bb_upper'] = d['bb_mid'] + (2 * d['bb_std'])
    d['bb_lower'] = d['bb_mid'] - (2 * d['bb_std'])

    low_8 = d['low'].rolling(8).min()
    high_8 = d['high'].rolling(8).max()
    d['stoch_k'] = 100 * ((d['close'] - low_8) / (high_8 - low_8).replace(0, 1e-9))
    d['stoch_d'] = d['stoch_k'].rolling(3).mean()

    d['atr'] = atr14
    return d

def manage_open_position(symbol, current_price, open_pos, atr):
    global state
    symbol_clean = symbol.replace("/", "")
    
    if symbol_clean not in state:
        entry = float(open_pos.avg_entry_price)
        pos_side = 'long' if float(open_pos.qty) > 0 else 'short'
        tp = entry + (3.0 * atr) if pos_side == 'long' else entry - (3.0 * atr)
        sl = entry - (1.5 * atr) if pos_side == 'long' else entry + (1.5 * atr)
        state[symbol_clean] = {
            'side': pos_side,
            'entry_price': entry,
            'tp_price': tp,
            'sl_price': sl,
            'high_water_mark': current_price
        }
        save_state()

    pos_state = state[symbol_clean]
    side = pos_state['side']
    entry = pos_state['entry_price']
    tp = pos_state['tp_price']
    sl = pos_state['sl_price']
    hwm = pos_state.get('high_water_mark', entry)

    # --- DYNAMIC TRAILING STOP & BREAK-EVEN LOGIC ---
    updated_sl = sl
    if side == 'long':
        if current_price > hwm:
            pos_state['high_water_mark'] = current_price
            hwm = current_price
        
        # Lock Break-Even once profit exceeds 1.0x ATR
        if (current_price - entry) >= (1.0 * atr) and sl < entry:
            updated_sl = entry + (0.1 * atr)
            logging.info(f"🔒 [{symbol}] BREAK-EVEN LOCKED @ ${updated_sl:.2f}")

        # Trail SL 1.5x ATR below High-Water Mark
        trail_sl = hwm - (1.5 * atr)
        if trail_sl > updated_sl:
            updated_sl = trail_sl
            logging.info(f"📈 [{symbol}] TRAILING STOP STEPPED UP @ ${updated_sl:.2f}")

    elif side == 'short':
        if current_price < hwm:
            pos_state['high_water_mark'] = current_price
            hwm = current_price

        # Lock Break-Even once profit exceeds 1.0x ATR
        if (entry - current_price) >= (1.0 * atr) and sl > entry:
            updated_sl = entry - (0.1 * atr)
            logging.info(f"🔒 [{symbol}] BREAK-EVEN LOCKED @ ${updated_sl:.2f}")

        # Trail SL 1.5x ATR above Low-Water Mark
        trail_sl = hwm + (1.5 * atr)
        if trail_sl < updated_sl:
            updated_sl = trail_sl
            logging.info(f"📉 [{symbol}] TRAILING STOP STEPPED DOWN @ ${updated_sl:.2f}")

    pos_state['sl_price'] = updated_sl
    save_state()

    logging.info(f"🛡️ [{symbol}] [{side.upper()}] | Live: ${current_price:.2f} | HWM: ${hwm:.2f} | TP: ${tp:.2f} | Dynamic SL: ${updated_sl:.2f}")

    close_trade = False
    reason = ""

    if side == 'long':
        if current_price >= tp: close_trade, reason = True, "DYNAMIC TAKE-PROFIT 🎯"
        elif current_price <= updated_sl: close_trade, reason = True, "DYNAMIC TRAILING STOP 🛑"
    elif side == 'short':
        if current_price <= tp: close_trade, reason = True, "DYNAMIC TAKE-PROFIT 🎯"
        elif current_price >= updated_sl: close_trade, reason = True, "DYNAMIC TRAILING STOP 🛑"

    if close_trade:
        logging.info(f"🚨 EXECUTING MARKET CLOSE [{symbol}] | Reason: {reason} @ ${current_price:.2f}")
        order_side = OrderSide.SELL if side == 'long' else OrderSide.BUY
        req = MarketOrderRequest(
            symbol=symbol,
            qty=abs(float(open_pos.qty)),
            side=order_side,
            time_in_force=TimeInForce.GTC
        )
        trading_client.submit_order(req)
        del state[symbol_clean]
        save_state()

def evaluate_symbol(symbol, positions):
    raw_df = get_crypto_bars(symbol)
    if raw_df is None or len(raw_df) < 55:
        return

    df = compute_indicators(raw_df)
    latest = df.iloc[-1]
    hist_price = latest['close']
    atr = latest['atr']
    adx = latest['adx']

    live_price = get_live_price(symbol)
    if live_price is None: live_price = hist_price

    open_pos = next((p for p in positions if p.symbol in [symbol.replace("/", ""), symbol]), None)
    if open_pos is not None:
        manage_open_position(symbol, live_price, open_pos, atr)
        return

    # MAIN BRAIN: REGIME ROUTER & DYNAMIC TP SCALING
    is_trending_regime = adx >= 25.0

    # Calculate Regime-Adaptive TP Multiplier based on ADX
    if adx >= 35.0:
        tp_mult = 4.5  # Strong Trend Expansion
    elif adx >= 25.0:
        tp_mult = 3.0  # Standard Trend
    else:
        tp_mult = 1.8  # Mean Reversion Scalp

    buy_votes = 0
    sell_votes = 0

    if is_trending_regime:
        if latest['ema_fast'] > latest['ema_slow']: buy_votes += 1
        else: sell_votes += 1

        if latest['macd'] > latest['macd_signal']: buy_votes += 1
        else: sell_votes += 1

        if hist_price > latest['psar']: buy_votes += 1
        else: sell_votes += 1

        regime_label = f"TRENDING (ADX: {adx:.1f} | TP Mult: {tp_mult}x)"
    else:
        if latest['rsi_fast'] < 30 or (latest['rsi_fast'] > latest['rsi_slow'] and latest['rsi_fast'] < 50): buy_votes += 1
        elif latest['rsi_fast'] > 70 or (latest['rsi_fast'] < latest['rsi_slow'] and latest['rsi_fast'] > 50): sell_votes += 1

        if hist_price <= latest['bb_lower']: buy_votes += 1
        elif hist_price >= latest['bb_upper']: sell_votes += 1

        if latest['stoch_k'] < 20 and latest['stoch_k'] > latest['stoch_d']: buy_votes += 1
        elif latest['stoch_k'] > 80 and latest['stoch_k'] < latest['stoch_d']: sell_votes += 1

        regime_label = f"RANGE-BOUND (ADX: {adx:.1f} | TP Mult: {tp_mult}x)"

    logging.info(f"Scan [{symbol}]: ${live_price:.2f} | Regime: {regime_label} | Buy: {buy_votes}/3 | Sell: {sell_votes}/3")

    symbol_clean = symbol.replace("/", "")
    if buy_votes >= 2:
        tp = round(live_price + (tp_mult * atr), 2)
        sl = round(live_price - (1.5 * atr), 2)
        logging.info(f"🚀 BUY SIGNAL [{symbol}] ({regime_label}) | Entry: ${live_price:.2f} | Dynamic TP: ${tp} | Initial SL: ${sl}")

        req = MarketOrderRequest(
            symbol=symbol,
            qty=TRADE_QTY[symbol],
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC
        )
        trading_client.submit_order(req)
        state[symbol_clean] = {
            'side': 'long',
            'entry_price': live_price,
            'tp_price': tp,
            'sl_price': sl,
            'high_water_mark': live_price
        }
        save_state()

    elif sell_votes >= 2:
        tp = round(live_price - (tp_mult * atr), 2)
        sl = round(live_price + (1.5 * atr), 2)
        logging.info(f"🚀 SHORT SIGNAL [{symbol}] ({regime_label}) | Entry: ${live_price:.2f} | Dynamic TP: ${tp} | Initial SL: ${sl}")

        req = MarketOrderRequest(
            symbol=symbol,
            qty=TRADE_QTY[symbol],
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC
        )
        trading_client.submit_order(req)
        state[symbol_clean] = {
            'side': 'short',
            'entry_price': live_price,
            'tp_price': tp,
            'sl_price': sl,
            'high_water_mark': live_price
        }
        save_state()

def main():
    logging.info("🟢 DYNAMIC ADAPTIVE CRYPTO ENGINE ONLINE (BTC, ETH, SOL)")
    load_state()
    while True:
        try:
            positions = trading_client.get_all_positions()
            for symbol in PAIRS:
                evaluate_symbol(symbol, positions)
        except Exception as e:
            logging.error(f"Engine Cycle Error: {e}")
        time.sleep(15)

if __name__ == "__main__":
    main()
