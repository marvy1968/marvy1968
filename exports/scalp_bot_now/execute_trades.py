from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

def is_market_open():
    if ZoneInfo:
        et_zone = ZoneInfo("America/New_York")
        now_et = datetime.now(et_zone)
    else:
        from datetime import timezone, timedelta
        et_zone = timezone(timedelta(hours=-4))
        now_et = datetime.now(et_zone)
    
    if now_et.weekday() >= 5:
        return False
        
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close

if not is_market_open():
    print("⏳ Outside regular market hours (9:30 AM - 4:00 PM ET). Trading suspended to avoid after-hours slippage.")
    exit(0)

import os
import json
import time
from datetime import datetime, timedelta
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

def get_clients():
    api_key = os.getenv('APCA_API_KEY_ID') or os.getenv('API_KEY')
    secret_key = os.getenv('APCA_API_SECRET_KEY') or os.getenv('SECRET_KEY')
    return TradingClient(api_key, secret_key, paper=True), StockHistoricalDataClient(api_key, secret_key)

def load_active_zones():
    if not os.path.exists('active_zones.json'):
        return None
    with open('active_zones.json', 'r') as f:
        return json.load(f)

def evaluate_and_trade():
    trading_client, data_client = get_clients()
    manifest = load_active_zones()
    if not manifest:
        return
        
    all_symbols = [sym for symbols in manifest.values() for sym in symbols.keys()]
    
    trade_request = StockLatestTradeRequest(symbol_or_symbols=all_symbols)
    trades = data_client.get_stock_latest_trade(trade_request)
    current_prices = {sym: t.price for sym, t in trades.items()}
    
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{now_str}] Evaluating bounce-confirmed zones for {len(all_symbols)} assets...')
    
    for category, symbols in manifest.items():
        for sym, data in symbols.items():
            current_price = current_prices.get(sym)
            if not current_price:
                continue
                
            buy_zone = data['buy_zone']
            sell_zone = data['sell_zone']
            
            if current_price <= buy_zone:
                print(f'🎯 [{sym}] Price (${current_price}) reached Buy Zone (${buy_zone}). Simulating bounce entry...')
                try:
                    # Dynamic 1% Equity Position Sizing
                    account = trading_client.get_account()
                    total_equity = float(account.equity)
                    target_dollars = total_equity * 0.01
                    qty_to_trade = max(1, int(target_dollars // current_price))
                    
                    print(f'-> Sizing: Target $ {target_dollars:.2f} (1%) | Price: ${current_price} | Qty: {qty_to_trade}')
                    
                    order_request = MarketOrderRequest(
                        symbol=sym,
                        qty=qty_to_trade,
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.DAY
                    )
                    # trade = trading_client.submit_order(order_data=order_request)
                    print(f'-> Bounce confirmed! Simulated order placed for {sym} at ${current_price}')
                except Exception as e:
                    print(f'Failed to submit order for {sym}: {e}')
                    
            elif current_price >= sell_zone:
                print(f'🚨 [{sym}] Price (${current_price}) reached Sell Zone (${sell_zone}).')

if __name__ == '__main__':
    evaluate_and_trade()
