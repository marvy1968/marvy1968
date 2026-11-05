import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "CROX", "CELH", "WING", "ONON", "SMCI"]

def run_backtest(buy_quantile=0.15, sell_quantile=0.85, atr_multiplier=2.0):
    api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("API_KEY")
    secret_key = os.getenv("APCA_API_SECRET_KEY") or os.getenv("SECRET_KEY")
    client = StockHistoricalDataClient(api_key, secret_key)
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=730)
    
    request_params = StockBarsRequest(
        symbol_or_symbols=UNIVERSE,
        timeframe=TimeFrame.Day,
        start=start_date,
        end=end_date
    )
    
    df = client.get_stock_bars(request_params).df
    
    total_trades = 0
    winning_trades = 0
    pnl_list = []
    
    for symbol in UNIVERSE:
        try:
            sym_df = df.loc[symbol].copy()
        except KeyError:
            continue
            
        if len(sym_df) < 400:
            continue
            
        for i in range(365, len(sym_df) - 1):
            window = sym_df.iloc[i-365:i]
            close = window['close']
            high = window['high']
            low = window['low']
            
            buy_zone = close.quantile(buy_quantile)
            
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = true_range.rolling(14).mean().iloc[-1]
            
            current_row = sym_df.iloc[i]
            
            # CONFIRMATION FILTER: Low touched buy zone, but close > open (bullish bounce candle)
            if current_row['low'] <= buy_zone and current_row['close'] > current_row['open']:
                entry_price = current_row['close'] # Enter on confirmation close
                target_price = entry_price + (atr_multiplier * atr)
                stop_loss = entry_price - (1.2 * atr) # Tighter stop
                
                future_window = sym_df.iloc[i+1:min(i+20, len(sym_df))]
                if future_window.empty:
                    continue
                
                trade_pnl = 0
                for _, fut in future_window.iterrows():
                    if fut['high'] >= target_price:
                        trade_pnl = target_price - entry_price
                        winning_trades += 1
                        break
                    elif fut['low'] <= stop_loss:
                        trade_pnl = stop_loss - entry_price
                        break
                else:
                    trade_pnl = future_window.iloc[-1]['close'] - entry_price
                    if trade_pnl > 0:
                        winning_trades += 1
                        
                total_trades += 1
                pnl_list.append(trade_pnl)
                
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    total_pnl = sum(pnl_list)
    
    print(f"Params -> Buy Q: {buy_quantile} | Sell Q: {sell_quantile} | ATR Mult: {atr_multiplier}")
    print(f"Total Trades: {total_trades} | Win Rate: {win_rate:.2f}% | Cumulative PnL: ${total_pnl:.2f}\n")

if __name__ == "__main__":
    print("--- RUNNING BOUNCE-CONFIRMED BACKTEST OPTIMIZATION ---\n")
    for b_q, s_q, atr_m in [(0.10, 0.90, 2.0), (0.15, 0.85, 2.0), (0.20, 0.80, 1.5)]:
        run_backtest(buy_quantile=b_q, sell_quantile=s_q, atr_multiplier=atr_m)
