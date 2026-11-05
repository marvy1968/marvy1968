

# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

# --- LINKED TO UNIFIED BRAIN ---
import sys
sys.path.append('/home/moonrider1968/bots/Unified_Brain')
from brain import assess_regime, decide_action
API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"

# -------------------------------
#!/usr/bin/env python3
"""
🚀 MASTER BEAST v49.19 CRYPTO-ONLY – ENHANCED CRYPTO RISK MANAGEMENT
"""
import time, logging, math, pandas as pd, numpy as np
from datetime import datetime
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, ClosePositionRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from river import linear_model, preprocessing, optim, metrics


# api removed('REDACTED_BROKER_KEY', '2u2XDxxT8us84n1aCJi7toVKoJUfu7Gi2Vpij2bgHvfJ', paper=True, base_url='https://paper-api.alpaca.markets')
crypto_client = CryptoHistoricalDataClient(API_KEY, SECRET_KEY)

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
log = logging.getLogger(__name__)

ALL_WATCH = ["BTC/USD","ETH/USD","SOL/USD","AVAX/USD","DOGE/USD"]

model = preprocessing.StandardScaler() | linear_model.LinearRegression(optimizer=optim.SGD(0.01))
mae_metric = metrics.MAE()
recent_rewards = []
hwm = {}
just_fired = {}
daily_start_equity = None
daily_start_pnl = None

MAX_OPEN = 8
DAILY_LOSS_CAP = 0.0075
MAX_PER_COIN_PCT = 0.025      # 2.5% max per coin
MAX_TOTAL_CRYPTO_PCT = 0.25    # 25% total crypto exposure
MIN_SIZE_PCT = 0.01
MAX_SIZE_PCT = 0.05
ATR_MULTIPLIER = 1.5          # Tighter for crypto
MAX_SPREAD_BPS = 8.0          # Spread guard

def get_active_symbols():
    try:
        return {p.symbol for p in api.get_all_positions()}
    except:
        return set()

def get_signals(df):
    if len(df) < 50:
        return 0.0, "NONE", 25.0
    df = df.ffill().bfill().fillna(0)
    c, o, h, l, v = df['close'], df['open'], df['high'], df['low'], df['volume']
    curr = c.iloc[-1]
    
    score = 0.0
    direction = "NONE"
    
    # Range Box
    box_high = h.rolling(30).max().iloc[-1]
    box_low = l.rolling(30).min().iloc[-1]
    if curr <= box_low * 1.015:
        score += 4.5
        direction = "LONG"
    elif curr >= box_high * 0.985:
        score += 4.5
        direction = "SHORT"
    
    # Velez Elephant + Weinstein Swing
    sma20 = c.rolling(20).mean().iloc[-1]
    sma50 = c.rolling(50).mean().iloc[-1]
    body = abs(c.iloc[-1] - o.iloc[-1])
    avg_body = abs(c - o).rolling(20).mean().iloc[-1]
    if body > avg_body * 1.12:
        score += 3.0
    if c.iloc[-1] > sma20 > sma50:
        score += 3.0
        if direction == "NONE": direction = "LONG"
    elif c.iloc[-1] < sma20 < sma50:
        score += 3.0
        if direction == "NONE": direction = "SHORT"
    
    # Momentum
    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs.fillna(0)))
    rsi_val = rsi.iloc[-1]
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    signal = macd.ewm(span=9).mean()
    hist = macd - signal
    if direction == "LONG":
        if rsi_val < 25: score += 2
        if hist.iloc[-1] > hist.iloc[-2]: score += 1
    elif direction == "SHORT":
        if rsi_val > 75: score += 2
        if hist.iloc[-1] < hist.iloc[-2]: score += 1

    # Volume Analysis
    avg_vol = v.rolling(20).mean().iloc[-1]
    current_vol = v.iloc[-1]
    vol_ratio = current_vol / (avg_vol + 1e-8)
    if vol_ratio > 1.5:
        score += 2.0
    elif vol_ratio < 0.6:
        score += 1.0
    if (direction == "LONG" and c.iloc[-1] > c.iloc[-2]) or (direction == "SHORT" and c.iloc[-1] < c.iloc[-2]):
        if vol_ratio > 1.2:
            score += 1.5

    # ADX for trend detection
    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    plus_dm = (h - h.shift(1)).where((h - h.shift(1)) > (l.shift(1) - l), 0)
    minus_dm = (l.shift(1) - l).where((l.shift(1) - l) > (h - h.shift(1)), 0)
    tr14 = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / tr14)
    minus_di = 100 * (minus_dm.rolling(14).mean() / tr14)
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
    adx = dx.rolling(14).mean().iloc[-1] if not pd.isna(dx.rolling(14).mean().iloc[-1]) else 25.0

    return score, direction, adx

log.info("🚀 MASTER BEAST v49.19 CRYPTO-ONLY – ENHANCED RISK MANAGEMENT (PER-COIN CAP + SPREAD GUARD + VOL CAP) | ARMED")

while True:
    try:
        acct = api.get_account()
        equity = float(acct.equity)
        cash = float(getattr(acct, 'cash', 0))
        
        if daily_start_equity is None:
            daily_start_equity = equity
            daily_start_pnl = equity
        daily_loss = (daily_start_equity - equity) / daily_start_equity
        if daily_loss >= DAILY_LOSS_CAP:
            log.warning("🛑 DAILY LOSS CAP HIT – PAUSING FOR 1 HOUR")
            time.sleep(3600)
            daily_start_equity = equity
            daily_start_pnl = equity
            continue

        if cash < 500:
            log.warning(f"🛑 LOW CASH (${cash:,.2f}) – SKIPPING ALL ENTRIES")
            time.sleep(60)
            continue
        available_bp = cash

        log.info(f"ACCOUNT DEBUG | equity={equity:,.2f} | cash={cash:,.2f} | usable_bp={available_bp:,.2f}")

        active_symbols = get_active_symbols()

        # Dynamic ATR Trail + Dynamic Stair-Step TP
        for p in api.get_all_positions():
            s = p.symbol
            curr = float(p.current_price)
            entry = float(p.avg_entry_price)
            side = 1 if p.side.value == 'long' else -1
            if s not in hwm:
                hwm[s] = curr
            if side == 1:
                hwm[s] = max(hwm[s], curr)
            else:
                hwm[s] = min(hwm[s], curr)
            
            try:
                req = CryptoBarsRequest(symbol_or_symbols=s, timeframe=TimeFrame.Minute, limit=100)
                bars = crypto_client.get_crypto_bars(req).df
                df = bars.xs(s) if isinstance(bars.index, pd.MultiIndex) else bars
                tr = pd.concat([(df['high'] - df['low']), (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()], axis=1).max(axis=1)
                atr = tr.rolling(14).mean().iloc[-1]
                trail_distance = ATR_MULTIPLIER * atr
                draw_from_peak = abs(hwm[s] - curr)
                if draw_from_peak >= trail_distance:
                    api.close_position(s)
                    log.info(f"🛡️ DYNAMIC ATR TRAIL HIT: {s} | ATR: {atr:.4f}")
                    hwm.pop(s, None)
                    just_fired.pop(s, None)
                    continue
            except:
                pass

            profit_pct = (curr - entry) / entry * side
            try:
                req = CryptoBarsRequest(symbol_or_symbols=s, timeframe=TimeFrame.Minute, limit=100)
                bars = crypto_client.get_crypto_bars(req).df
                df = bars.xs(s) if isinstance(bars.index, pd.MultiIndex) else bars
                _, _, adx = get_signals(df)
                step_size = 0.0020 if adx < 25 else 0.0040
            except:
                step_size = 0.0020
            stair_level = math.floor(profit_pct / step_size) * step_size
            if stair_level >= step_size:
                api.close_position(s)
                log.info(f"💰 DYNAMIC STAIR TP ({step_size*100:.2f}%): {s} | Profit: {profit_pct*100:.2f}% | Locked at {stair_level*100:.2f}%")
                hwm.pop(s, None)
                just_fired.pop(s, None)

        now = time.time()
        just_fired = {k: v for k, v in just_fired.items() if now - v < 300}

        if len(active_symbols) < MAX_OPEN:
            total_crypto_exposure = 0.0
            for s in ALL_WATCH:
                if s in active_symbols or s in just_fired:
                    continue
                try:
                    req = CryptoBarsRequest(symbol_or_symbols=s, timeframe=TimeFrame.Minute, limit=100)
                    bars = crypto_client.get_crypto_bars(req).df
                    df = bars.xs(s) if isinstance(bars.index, pd.MultiIndex) else bars
                    
                    # Spread guard
                    spread = (df['high'].iloc[-1] - df['low'].iloc[-1]) / df['close'].iloc[-1] * 10000
                    if spread > MAX_SPREAD_BPS:
                        continue
                    
                    # Volatility cap
                    atr = pd.concat([(df['high'] - df['low']), (df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()], axis=1).max(axis=1).rolling(14).mean().iloc[-1]
                    if atr / df['close'].iloc[-1] > 0.025:
                        continue
                    
                    confidence, direction, _ = get_signals(df)
                    if confidence >= 6.5:
                        price = df['close'].iloc[-1]
                        size_pct = MIN_SIZE_PCT + (min(confidence, 10) / 10.0) * (MAX_SIZE_PCT - MIN_SIZE_PCT)
                        qty = round((available_bp * size_pct) / price, 4)
                        
                        if qty > 0 and (qty * price) <= available_bp * 0.9 and (qty * price) <= equity * MAX_PER_COIN_PCT and total_crypto_exposure + (qty * price) <= equity * MAX_TOTAL_CRYPTO_PCT:
                            side = OrderSide.BUY if direction == "LONG" else OrderSide.SELL
                            api.submit_order(MarketOrderRequest(symbol=s, qty=qty, side=side, time_in_force=TimeInForce.GTC))
                            log.info(f"🔥 CRYPTO STRIKE: {direction} {s} | Confidence: {confidence:.1f}/10 | Size: {size_pct*100:.2f}% | Qty: {qty}")
                            just_fired[s] = now
                            hwm[s] = price
                            total_crypto_exposure += qty * price
                except Exception as e:
                    log.warning(f"Entry skipped {s}: {e}")
                    continue

        recent_rewards.append(equity)
        if len(recent_rewards) > 12:
            recent_rewards.pop(0)
        model.learn_one({'equity': equity}, equity)
        mae_metric.update(equity, equity)
        
        daily_pnl = equity - daily_start_pnl
        log.info(f"📊 HEARTBEAT | Equity: ${equity:,.2f} | Usable BP: ${available_bp:,.2f} | Daily PNL: ${daily_pnl:,.2f} | MAE: {mae_metric.get():.4f} | Active: {len(active_symbols)} | v49.19 Running")
        time.sleep(20)
        
    except Exception as e:
        log.error(f"ENGINE ERROR: {e}")
        time.sleep(10)

api = TradingClient('REDACTED_BROKER_KEY', '2u2XDxxT8us84n1aCJi7toVKoJUfu7Gi2Vpij2bgHvfJ', paper=True)
