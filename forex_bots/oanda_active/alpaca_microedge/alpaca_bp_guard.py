import os
import time
import alpaca_trade_api as tradeapi

# Cleaned Keys from your .env
API_KEY = "REDACTED".strip()
SECRET_KEY = "REDACTED".strip()

def get_connected_api():
    urls = ['https://paper-api.alpaca.markets', 'https://api.alpaca.markets']
    for url in urls:
        try:
            api = tradeapi.REST(API_KEY, SECRET_KEY, url)
            api.get_account()
            print(f"✅ Successfully connected to {url}")
            return api
        except Exception:
            continue
    return None

def clean_house(required_bp=20000):
    api = get_connected_api()
    if not api:
        print("❌ Error: Still Unauthorized. Please double-check your keys on the Alpaca Dashboard.")
        return

    try:
        account = api.get_account()
        current_bp = float(account.buying_power)
        print(f"💰 Current Buying Power: ${current_bp:,.2f}")

        # Step 1: Cancel open orders (These often tie up Buying Power)
        if current_bp < required_bp:
            print("🚫 Canceling open orders to free up Buying Power...")
            api.cancel_all_orders()
            time.sleep(2)
            account = api.get_account()
            current_bp = float(account.buying_power)

        # Step 2: Liquidate laggards if still low
        if current_bp < required_bp:
            print(f"⚠️ BP still low (${current_bp:,.2f}). Liquidating laggards...")
            positions = api.list_positions()
            laggards = sorted(positions, key=lambda x: float(x.unrealized_plpc))
            
            for pos in laggards:
                if float(pos.unrealized_plpc) < -0.005: # Sell if down > 0.5%
                    print(f"📉 Selling {pos.symbol} (P/L: {pos.unrealized_plpc:.2%})")
                    api.close_position(pos.symbol)
                    time.sleep(1)
                    if float(api.get_account().buying_power) >= required_bp:
                        break
        
        print(f"✨ Final Buying Power: ${float(api.get_account().buying_power):,.2f}")

    except Exception as e:
        print(f"⚠️ Execution Error: {e}")

if __name__ == "__main__":
    clean_house()
