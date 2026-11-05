import os
import requests

APCA_KEY = (os.getenv("APCA_API_KEY_ID") or "").strip()
APCA_SECRET = (os.getenv("APCA_API_SECRET_KEY") or "").strip()
BASE_URL = "https://paper-api.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID": APCA_KEY,
    "APCA-API-SECRET-KEY": APCA_SECRET
}

def main():
    print("=" * 80)
    print("📈 ALPACA PAPER ACCOUNT POSITIONS & PORTFOLIO SUMMARY")
    print("=" * 80)

    # 1. Fetch Account Information
    acc_res = requests.get(f"{BASE_URL}/account", headers=HEADERS)
    if acc_res.status_code != 200:
        print(f"❌ Failed to fetch account data: {acc_res.text}")
        return

    acc = acc_res.json()
    equity = float(acc.get("equity", 0))
    buying_power = float(acc.get("buying_power", 0))
    cash = float(acc.get("cash", 0))

    print(f"💳 Equity: ${equity:,.2f} | Cash: ${cash:,.2f} | Buying Power: ${buying_power:,.2f}")
    print("-" * 80)

    # 2. Fetch Open Positions
    pos_res = requests.get(f"{BASE_URL}/positions", headers=HEADERS)
    if pos_res.status_code != 200:
        print(f"❌ Failed to fetch positions: {pos_res.text}")
        return

    positions = pos_res.json()
    if not positions:
        print("ℹ️ No open positions found.")
        print("=" * 80)
        return

    total_market_value = 0.0
    total_unrealized_pnl = 0.0

    print(f"{'SYMBOL':<8} {'SIDE':<6} {'QTY':<8} {'AVG ENTRY':<12} {'MARKET VAL':<14} {'UNREALIZED PnL':<15}")
    print("-" * 80)

    for p in positions:
        symbol = p.get("symbol")
        qty = float(p.get("qty", 0))
        side = p.get("side", "").upper()
        entry_price = float(p.get("avg_entry_price", 0))
        market_value = float(p.get("market_value", 0))
        unrealized_pnl = float(p.get("unrealized_pl", 0))

        total_market_value += abs(market_value)
        total_unrealized_pnl += unrealized_pnl

        pnl_str = f"${unrealized_pnl:,.2f}"
        if unrealized_pnl > 0:
            pnl_str = f"+{pnl_str}"

        print(f"{symbol:<8} {side:<6} {qty:<8.0f} ${entry_price:<11.2f} ${market_value:<13.2f} {pnl_str:<15}")

    print("-" * 80)
    print(f"📊 Total Active Capital Allocated: ${total_market_value:,.2f}")
    print(f"💰 Total Unrealized Portfolio PnL : ${total_unrealized_pnl:,.2f}")
    print("=" * 80)

if __name__ == "__main__":
    main()
