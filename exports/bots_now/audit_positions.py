import os, sys
from pathlib import Path
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide

env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

tc = TradingClient(API_KEY, API_SECRET, paper=True)
positions = tc.get_all_positions()
orders = tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))

print("=" * 75)
print(f"{'SYMBOL':<8} | {'QTY':>5} | {'ENTRY':>9} | {'CURRENT':>9} | {'STOP':>9} | {'UNREALIZED PnL':>14}")
print("-" * 75)

for p in positions:
    sym = p.symbol
    entry = float(p.avg_entry_price)
    cur = float(p.current_price)
    qty = int(float(p.qty))
    pnl = float(p.unrealized_pl)
    
    stops = [float(o.stop_price) for o in orders if o.symbol == sym and o.side == OrderSide.SELL and o.stop_price is not None]
    stop_str = f"${stops[0]:.2f}" if stops else "None"
    
    print(f"{sym:<8} | {qty:>5} | ${entry:>8.2f} | ${cur:>8.2f} | {stop_str:>9} | ${pnl:>+13.2f}")

print("=" * 75)
