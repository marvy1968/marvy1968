import os
import pandas as pd
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

UNIVERSE = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'CROX', 'CELH', 'WING', 'ONON', 'SMCI']
TRADE_ALLOCATION_USD = 1000.0

def run_live_scanner():
    api_key = os.getenv('APCA_API_KEY_ID') or os.getenv('API_KEY')
    secret_key = os.getenv('APCA_API_SECRET_KEY') or os.getenv('SECRET_KEY')
    
    if not api_key or not secret_key:
        print("Error: Alpaca API keys not found in environment variables.")
        return

    data_client = StockHistoricalDataClient(api_key, secret_key)
    trading_client = TradingClient(api_key, secret_key, paper=True)
    # OWNERSHIP_V1 (2026-10-01): shared paper account -> tag orders "sb-", skip busy symbols
    import sys, logging
    logging.basicConfig(level=logging.INFO)
    sys.path.insert(0, "/home/moonrider1968/bots")
    from bot_ownership import Ownership
    own = Ownership("scalp_bot_live", "sb-", key=api_key, secret=secret_key)
    
    print(f"--- RUNNING DAILY STRATEGY SCAN: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=400)
    
    request_params = StockBarsRequest(
        symbol_or_symbols=UNIVERSE,
        timeframe=TimeFrame.Day,
        start=start_date,
        end=end_date
    )
    
    try:
        bars = data_client.get_stock_bars(request_params)
        df = bars.df
        print(f"Data fetched successfully. DataFrame shape: {df.shape}")
    except Exception as e:
        print(f"Error fetching data from Alpaca: {e}")
        return
    
    if df.empty:
        print("Warning: Returned DataFrame is empty.")
        return

    for symbol in UNIVERSE:
        try:
            sym_df = df.loc[symbol].copy()
        except KeyError:
            print(f"Skipping {symbol}: Symbol not found in multi-index DataFrame.")
            continue
        except Exception as e:
            print(f"Error accessing data for {symbol}: {e}")
            continue
            
        if len(sym_df) < 380:
            print(f"Skipping {symbol}: Insufficient history ({len(sym_df)} bars)")
            continue
            
        sym_df['sma50'] = sym_df['close'].rolling(50).mean()
        
        window = sym_df.iloc[-365:-1]
        close_prices = window['close']
        
        buy_zone = close_prices.quantile(0.08)
        current_row = sym_df.iloc[-1]
        current_close = current_row['close']
        current_low = current_row['low']
        sma50 = current_row['sma50']
        
        print(f"Checked {symbol}: Close=${current_close:.2f} | Low=${current_low:.2f} | BuyZone8%=${buy_zone:.2f} | SMA50=${sma50:.2f}")
        
        if current_low <= buy_zone and current_close > sma50:
            print(f">>> SIGNAL TRIGGERED FOR {symbol}! Placing paper bracket order...")
            
            qty = round(TRADE_ALLOCATION_USD / current_close, 4)
            if qty < 0.0001:
                qty = 1
                
            entry_price = current_close
            target_price = round(entry_price * 1.045, 2)
            stop_loss_price = round(entry_price * 0.975, 2)
            
            bracket_order = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=target_price),
                stop_loss=StopLossRequest(stop_price=stop_loss_price)
            )
            
            try:
                response = own.entry({
                    "symbol": symbol, "qty": str(qty), "side": "buy", "type": "market",
                    "time_in_force": "day", "order_class": "bracket",
                    "take_profit": {"limit_price": f"{target_price:.2f}"},
                    "stop_loss": {"stop_price": f"{stop_loss_price:.2f}"}}, tag="en")
                if response is None:
                    print(f"Order for {symbol} skipped/rejected (see ownership log)")
                    continue
                print(f"Successfully placed paper order for {symbol}: {response.get('id')}")
            except Exception as e:
                print(f"Failed to submit order for {symbol}: {e}")
        else:
            print(f"No signal for {symbol}.")

if __name__ == '__main__':
    run_live_scanner()
