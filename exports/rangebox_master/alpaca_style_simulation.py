#!/usr/bin/env python3
import numpy as np
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("ALPACA_SIM")

class Config:
    RANGE_ADX_MAX = 22.0
    TREND_ADX_MIN = 22.0
    FAST_EMA = 9
    SLOW_EMA = 20

class AlpacaStyleSimulation:
    def __init__(self):
        # Emulating a standard retail trading account balance footprint
        self.cash = 100000.0
        self.portfolio_value = 100000.0
        # Alpaca API tracking format: { symbol: {"qty": float, "avg_entry": float, "side": str} }
        self.alpaca_positions = {} 

    def run_simulation(self, symbol, trend_bias="RANGE"):
        log.info(f"🔮 Initializing Alpaca-Style Order Simulation for {symbol} | Profile: {trend_bias}")
        
        np.random.seed(101)
        base_price = 150.00 if "USD" in symbol else 1.1000
        ticks = np.random.normal(0, 0.40, 100)
        
        if trend_bias == "BEARISH":
            ticks -= 0.15
        elif trend_bias == "BULLISH":
            ticks += 0.15
            
        prices = base_price + np.cumsum(ticks)
        df = pd.DataFrame({'high': prices + 0.20, 'low': prices - 0.20, 'close': prices})
        
        # Overlay structural logic mathematical indicators
        close = df['close']
        df['ema_fast'] = close.ewm(span=Config.FAST_EMA, adjust=False).mean()
        df['ema_slow'] = close.ewm(span=Config.SLOW_EMA, adjust=False).mean()
        df['bb_mid'] = close.rolling(window=20).mean()
        df['bb_std'] = close.rolling(window=20).std()
        df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
        df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
        df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - close.shift(1)), abs(df['low'] - close.shift(1))))
        df['atr'] = df['tr'].rolling(window=14).mean()
        df['adx'] = (abs(df['ema_fast'] - df['ema_slow']) / df['atr']) * 12.0
        df['adx'] = df['adx'].fillna(15.0).clip(5.0, 45.0)

        trades = 0
        pnl = 0.0

        for i in range(25, len(df)):
            row = df.iloc[i]
            price = row['close']
            adx = row['adx']
            has_position = symbol in self.alpaca_positions

            # --- ALPACA ORDER GATEWAY SUBMISSION SIMULATION ---
            if adx < Config.RANGE_ADX_MAX and not has_position:
                if price <= row['bb_lower']:
                    # Emulate an Alpaca SDK Market Order payload logic array
                    self.alpaca_positions[symbol] = {"qty": 100.0, "avg_entry": price, "side": "long"}
                    log.info(f"📦 [ALPACA SUBMIT] buy 100.0 shares {symbol} at market. Executed @ ${price:.2f} (ADX: {adx:.1f})")
                    trades += 1
                elif price >= row['bb_upper']:
                    self.alpaca_positions[symbol] = {"qty": 100.0, "avg_entry": price, "side": "short"}
                    log.info(f"📦 [ALPACA SUBMIT] sell_short 100.0 shares {symbol} at market. Executed @ ${price:.2f} (ADX: {adx:.1f})")
                    trades += 1

            # --- ALPACA TARGET UNWIND MATRIX ---
            if has_position:
                pos = self.alpaca_positions[symbol]
                entry = pos['avg_entry']
                side = pos['side']
                closed = False

                if side == "long":
                    if price >= entry + 1.25 or price >= row['bb_mid']:
                        log.info(f"💰 [ALPACA TP] Target hit. Closing long position for {symbol} @ ${price:.2f}")
                        pnl += (price - entry) * pos['qty']
                        closed = True
                elif side == "short":
                    if price <= entry - 1.25 or price <= row['bb_mid']:
                        log.info(f"💰 [ALPACA TP] Target hit. Closing short position for {symbol} @ ${price:.2f}")
                        pnl += (entry - price) * pos['qty']
                        closed = True

                if closed:
                    del self.alpaca_positions[symbol]

        print(f"\n=== 🇺🇸 ALPACA MATRIX COMPLIANCE REPORT ===")
        print(f"Asset Verified:           {symbol}")
        print(f"Alpaca Order Signals:     {trades}")
        print(f"Calculated Simulation PnL: ${pnl:.2f}")
        print(f"Active Live Footprint:    {self.alpaca_positions}")
        print("===========================================\n")

if __name__ == "__main__":
    sim = AlpacaStyleSimulation()
    sim.run_simulation("AAPL", trend_bias="RANGE")
    sim.run_simulation("NVDA", trend_bias="BULLISH")
