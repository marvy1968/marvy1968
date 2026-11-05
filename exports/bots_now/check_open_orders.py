import os
from pathlib import Path
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

load_dotenv("/home/moonrider1968/bots/.env")
tc = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)

orders = tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
print(f"Total Open Orders Found: {len(orders)}")
for o in orders:
    print(f"Symbol: {o.symbol:<6} | Side: {o.side:<4} | Type: {o.type:<10} | Qty: {o.qty:<4} | Stop: {o.stop_price} | Limit: {o.limit_price}")
