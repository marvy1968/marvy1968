#!/usr/bin/env python3
import numpy as np
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("UNIFIED_BEAST")

class Config:
    RANGE_ADX_MAX = 22.0      
    TREND_ADX_MIN = 22.0      
    BASE_RISK_PCT = 0.01      
    FAST_EMA = 9
    SLOW_EMA = 20

class OandaUnifiedBeast:
    def __init__(self):
        self.equity = 66158.39
        self.active_positions = {} # Structured as {symbol: []} to handle multiple concurrent positions safely
        
    def analyze_regime(self, df):
        close = df['close']
        high = df['high']
        low = df['low']
        
        df['ema_fast'] = close.ewm(span=Config.FAST_EMA, adjust=False).mean()
        df['ema_slow'] = close.ewm(span=Config.SLOW_EMA, adjust=False).mean()
        
        df['bb_mid'] = close.rolling(window=20).mean()
        df['bb_std'] = close.rolling(window=20).std()
        df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
        df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
        
        df['tr'] = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        df['atr'] = df['tr'].rolling(window=14).mean()
        df['adx'] = (abs(df['ema_fast'] - df['ema_slow']) / df['atr']) * 15.0
        df['adx'] = df['adx'].fillna(15.0).clip(5.0, 45.0)
        
        return df

    def run_internal_simulation(self, symbol, trend_bias="BULLISH"):
        log.info(f"🔮 Starting Internal Simulation Engine for {symbol} | Regime Focus: {trend_bias}")
        
        if symbol not in self.active_positions:
            self.active_positions[symbol] = []
            
        np.random.seed(42)
        base_price = 1.0850 if "USD" in symbol else 158.20
        ticks = np.random.normal(0, 0.0010, 100)
        
        if trend_bias == "BULLISH":
            ticks += 0.0004  
        elif trend_bias == "BEARISH":
            ticks -= 0.0004  
            
        prices = base_price + np.cumsum(ticks)
        
        df = pd.DataFrame({
            'high': prices + 0.0005,
            'low': prices - 0.0005,
            'close': prices
        })
        
        df = self.analyze_regime(df)
        trades_executed = 0
        pnl_accumulated = 0.0
        
        for i in range(25, len(df)):
            row = df.iloc[i]
            price = row['close']
            adx = row['adx']
            
            # Count active positions for this symbol
            symbol_pos_count = len(self.active_positions[symbol])
            
            # --- REGIME INTERLOCK BOARD ---
            if adx < Config.RANGE_ADX_MAX:
                if price <= row['bb_lower'] and symbol_pos_count == 0:
                    self.active_positions[symbol].append({'dir': 'BUY', 'entry': price, 'type': 'RANGE'})
                    log.info(f"🔄 [RANGE MATCH] {symbol} oversold at {price:.5f} (ADX: {adx:.1f}). Opening Mean-Reversion Long.")
                    trades_executed += 1
                elif price >= row['bb_upper'] and symbol_pos_count == 0:
                    self.active_positions[symbol].append({'dir': 'SELL', 'entry': price, 'type': 'RANGE'})
                    log.info(f"🔄 [RANGE MATCH] {symbol} overbought at {price:.5f} (ADX: {adx:.1f}). Opening Mean-Reversion Short.")
                    trades_executed += 1
                    
            elif adx >= Config.TREND_ADX_MIN:
                fast, slow = row['ema_fast'], row['ema_slow']
                
                if fast > slow and price <= fast and symbol_pos_count == 0:
                    self.active_positions[symbol].append({'dir': 'BUY', 'entry': price, 'type': 'BULL_SCALP'})
                    log.info(f"🚀 [🚀 BULL SCALP] {symbol} trend pullback to 9-EMA at {price:.5f} (ADX: {adx:.1f}). Sniping Continuation Long.")
                    trades_executed += 1
                elif fast < slow and price >= fast and symbol_pos_count == 0:
                    self.active_positions[symbol].append({'dir': 'SELL', 'entry': price, 'type': 'BEAR_SCALP'})
                    log.info(f"📉 [🐻 BEAR SCALP] {symbol} trend relief to 9-EMA at {price:.5f} (ADX: {adx:.1f}). Sniping Continuation Short.")
                    trades_executed += 1

            # --- DYNAMIC ASYMMETRIC VIRTUAL TARGET CLOSES ---
            retaining_positions = []
            for pos in self.active_positions[symbol]:
                entry = pos['entry']
                closed = False
                
                if pos['dir'] == 'BUY':
                    if price >= entry + 0.0015 or (pos['type'] == 'RANGE' and price >= row['bb_mid']):
                        log.info(f"💰 [VIRTUAL TP] {symbol} profit target cleared at {price:.5f}. Capital unlocked.")
                        pnl_accumulated += (price - entry) * 100000
                        closed = True
                elif pos['dir'] == 'SELL':
                    if price <= entry - 0.0015 or (pos['type'] == 'RANGE' and price <= row['bb_mid']):
                        log.info(f"💰 [VIRTUAL TP] {symbol} profit target cleared at {price:.5f}. Capital unlocked.")
                        pnl_accumulated += (entry - price) * 100000
                        closed = True
                        
                if not closed:
                    retaining_positions.append(pos)
            
            self.active_positions[symbol] = retaining_positions

        print(f"\n=== 📡 INTERNAL MATRIX SIMULATION REPORT ===")
        print(f"Asset Evaluated:          {symbol}")
        print(f"Total Framework Signals:  {trades_executed}")
        print(f"Simulated Gross PnL:     ${pnl_accumulated:.2f}")
        print(f"Remaining Local Footprint: {list(self.active_positions.keys())} -> {symbol}: {len(self.active_positions[symbol])} active")
        print("============================================\n")

if __name__ == "__main__":
    beast = OandaUnifiedBeast()
    beast.run_internal_simulation("EUR_USD", trend_bias="BULLISH")
    beast.run_internal_simulation("USD_JPY", trend_bias="RANGE")
