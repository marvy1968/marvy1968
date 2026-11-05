import sys
import numpy as np
from collections import defaultdict

sys.path.append("/home/moonrider1968/bots/killer_beast_sniper")
from market_regime import detect_market_regime

# ==============================================================================
# 🧠 10-EXPERT QUANTITATIVE SIGNAL ENGINE
# ==============================================================================

def calculate_sma(data, period):
    return float(np.mean(data[-period:])) if len(data) >= period else 0.0

def calculate_ema(data, period):
    if len(data) < period:
        return 0.0
    weights = np.exp(np.linspace(-1., 0., period))
    weights /= weights.sum()
    return float(np.convolve(data, weights, mode='full')[:len(data)][-1])

def evaluate_all_experts(symbol, candles, timeframe="1m"):
    """
    Evaluates 5 Scalper Experts and 5 Swing Experts against recent candle data.
    """
    if len(candles) < 30:
        return None

    closes = [c.get('c', c.get('close', 0)) for c in candles]
    highs  = [c.get('h', c.get('high', 0)) for c in candles]
    lows   = [c.get('l', c.get('low', 0)) for c in candles]
    
    current_price = closes[-1]
    regime, adx, chop = detect_market_regime(candles)
    
    signals = []

    # --------------------------------------------------------------------------
    # ⚡ 5 SCALPER EXPERTS (Micro Timeframes: 1m - 5m)
    # --------------------------------------------------------------------------
    
    # 1. MICRO_MOMENTUM
    if regime in ["TRENDING", "NEUTRAL"]:
        recent_range = highs[-1] - lows[-1]
        avg_range = np.mean([h - l for h, l in zip(highs[-10:-1], lows[-10:-1])])
        if recent_range > 1.5 * avg_range and current_price > closes[-2]:
            signals.append(("MICRO_MOMENTUM", "BUY", "SCALPER"))
        elif recent_range > 1.5 * avg_range and current_price < closes[-2]:
            signals.append(("MICRO_MOMENTUM", "SELL", "SCALPER"))

    # 2. RANGE_BREAKOUT
    if regime == "TRENDING":
        max_10 = max(highs[-11:-1])
        min_10 = min(lows[-11:-1])
        if current_price > max_10:
            signals.append(("RANGE_BREAKOUT", "BUY", "SCALPER"))
        elif current_price < min_10:
            signals.append(("RANGE_BREAKOUT", "SELL", "SCALPER"))

    # 3. VOL_SQUEEZE
    if regime == "RANGING":
        std_20 = np.std(closes[-20:])
        sma_20 = calculate_sma(closes, 20)
        if std_20 < (sma_20 * 0.002):  # Volatility Squeeze Compression
            if current_price > sma_20:
                signals.append(("VOL_SQUEEZE", "BUY", "SCALPER"))
            else:
                signals.append(("VOL_SQUEEZE", "SELL", "SCALPER"))

    # 4. VWAP_REVERSION
    if regime in ["RANGING", "NEUTRAL"]:
        vwap = calculate_sma(closes, 20)
        std_dev = np.std(closes[-20:])
        upper_band = vwap + (2.0 * std_dev)
        lower_band = vwap - (2.0 * std_dev)
        if current_price <= lower_band:
            signals.append(("VWAP_REVERSION", "BUY", "SCALPER"))
        elif current_price >= upper_band:
            signals.append(("VWAP_REVERSION", "SELL", "SCALPER"))

    # 5. EMA_VELOCITY_SCALP
    ema8 = calculate_ema(closes, 8)
    ema21 = calculate_ema(closes, 21)
    if regime == "TRENDING":
        if ema8 > ema21 and closes[-2] <= calculate_ema(closes[:-1], 8):
            signals.append(("EMA_VELOCITY_SCALP", "BUY", "SCALPER"))
        elif ema8 < ema21 and closes[-2] >= calculate_ema(closes[:-1], 8):
            signals.append(("EMA_VELOCITY_SCALP", "SELL", "SCALPER"))

    # --------------------------------------------------------------------------
    # 📈 5 SWING EXPERTS (Macro Timeframes: 1h - 4h)
    # --------------------------------------------------------------------------
    
    # 6. VELEZ_20EMA
    ema20 = calculate_ema(closes, 20)
    if regime == "TRENDING":
        candle_body = abs(closes[-1] - closes[-2])
        avg_body = np.mean([abs(closes[i] - closes[i-1]) for i in range(-10, -1)])
        if current_price > ema20 and candle_body > (1.8 * avg_body) and closes[-1] > closes[-2]:
            signals.append(("VELEZ_20EMA", "BUY", "SWING"))
        elif current_price < ema20 and candle_body > (1.8 * avg_body) and closes[-1] < closes[-2]:
            signals.append(("VELEZ_20EMA", "SELL", "SWING"))

    # 7. SUPERTREND_ATR
    if regime == "TRENDING":
        atr = np.mean([h - l for h, l in zip(highs[-14:], lows[-14:])])
        upper_st = calculate_sma(closes, 10) + (2.5 * atr)
        lower_st = calculate_sma(closes, 10) - (2.5 * atr)
        if current_price > upper_st:
            signals.append(("SUPERTREND_ATR", "BUY", "SWING"))
        elif current_price < lower_st:
            signals.append(("SUPERTREND_ATR", "SELL", "SWING"))

    # 8. MACD_DIVERGENCE
    if regime in ["RANGING", "NEUTRAL"]:
        fast_ema = calculate_ema(closes, 12)
        slow_ema = calculate_ema(closes, 26)
        macd_line = fast_ema - slow_ema
        if current_price < min(closes[-10:-1]) and macd_line > -0.05:
            signals.append(("MACD_DIVERGENCE", "BUY", "SWING"))

    # 9. STRUCTURE_RETEST
    if regime == "TRENDING":
        recent_high = max(highs[-20:])
        fib_618 = recent_high - (recent_high - min(lows[-20:])) * 0.382
        if abs(current_price - fib_618) / current_price < 0.0015:
            signals.append(("STRUCTURE_RETEST", "BUY", "SWING"))

    # 10. REGIME_ROTATION
    if regime == "TRENDING" and adx > 30.0:
        signals.append(("REGIME_ROTATION", "BUY" if closes[-1] > closes[-5] else "SELL", "SWING"))

    return {
        "symbol": symbol,
        "price": current_price,
        "regime": regime,
        "adx": adx,
        "chop": chop,
        "signals": signals
    }

if __name__ == "__main__":
    print("=" * 80)
    print("🤖 10-EXPERT MASTER ORCHESTRATOR INITIALIZED")
    print("=" * 80)
    
    # Generate Synthetic Test Data
    np.random.seed(42)
    fake_prices = 100 + np.cumsum(np.random.randn(50) * 0.5)
    test_candles = [{'h': p + 0.3, 'l': p - 0.3, 'c': p} for p in fake_prices]
    
    res = evaluate_all_experts("NVDA", test_candles, "1m")
    print(f"Asset: {res['symbol']} | Price: ${res['price']:.2f} | Regime: {res['regime']} (ADX: {res['adx']})")
    print("-" * 80)
    print(f"Active Fired Signals ({len(res['signals'])}):")
    for name, side, category in res['signals']:
        print(f"  • [{category}] Expert: {name:<20} | Side: {side}")
    print("=" * 80)
