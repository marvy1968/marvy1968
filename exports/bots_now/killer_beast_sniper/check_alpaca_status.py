
# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import sys
import requests

sys.path.append("/home/moonrider1968/bots/killer_beast_sniper")
from alpaca_paper_executor import HEADERS, ALPACA_PAPER_URL

def main():
    # 1. Account Info
    r_acc = requests.get(f"{ALPACA_PAPER_URL}/v2/account", headers=HEADERS)
    if r_acc.status_code == 200:
        acc = r_acc.json()
        eq = float(acc.get("equity", 0))
        bp = float(acc.get("buying_power", 0))
        print("=" * 85)
        print(f"💰 ACCOUNT SUMMARY | Equity: ${eq:,.2f} | Buying Power: ${bp:,.2f}")
        print("=" * 85)
    else:
        print(f"❌ Failed to fetch account info: {r_acc.status_code} {r_acc.text}")

    # 2. Open Positions
    r_pos = requests.get(f"{ALPACA_PAPER_URL}/v2/positions", headers=HEADERS)
    if r_pos.status_code == 200:
        positions = r_pos.json()
        print(f"\n📊 OPEN POSITIONS ({len(positions)} Total):")
        print("-" * 85)
        if positions:
            print(f"{'Symbol':<8} | {'Qty':>8} | {'Entry Price':>12} | {'Current Price':>14} | {'Unrealized P/L':>15}")
            print("-" * 85)
            for p in positions:
                sym = p.get("symbol", "")
                qty = p.get("qty", "0")
                entry = float(p.get("avg_entry_price", 0))
                curr = float(p.get("current_price", 0))
                upl = float(p.get("unrealized_pl", 0))
                print(f"{sym:<8} | {qty:>8} | ${entry:>11.2f} | ${curr:>13.2f} | ${upl:>14.2f}")
        else:
            print("No open positions currently held.")

    # 3. Open Orders
    r_ord = requests.get(f"{ALPACA_PAPER_URL}/v2/orders?status=open&nested=true", headers=HEADERS)
    if r_ord.status_code == 200:
        orders = r_ord.json()
        print(f"\n📑 OPEN ORDERS ({len(orders)} Total):")
        print("-" * 85)
        if orders:
            print(f"{'Symbol':<8} | {'Side':<5} | {'Qty':>6} | {'Type':<10} | {'Status':<12} | {'Submitted At':<20}")
            print("-" * 85)
            for o in orders:
                sym = o.get("symbol", "")
                side = str(o.get("side", "")).upper()
                qty = o.get("qty", "0")
                typ = o.get("type", "")
                stat = o.get("status", "")
                sub = str(o.get("submitted_at", ""))[:19]
                print(f"{sym:<8} | {side:<5} | {qty:>6} | {typ:<10} | {stat:<12} | {sub:<20}")
        else:
            print("No pending or open orders in queue.")
    print("=" * 85)

if __name__ == "__main__":
    main()
