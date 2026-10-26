import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient

load_dotenv("alpaca.env")
key = os.getenv("ALPACA_API_KEY", "").strip()
sec = os.getenv("ALPACA_API_SECRET", "").strip()

print(f"Testing Key: {key[:5]}... | Secret: {sec[:5]}...")
try:
    client = TradingClient(key, sec, paper=True)
    acc = client.get_account()
    print(f"✅ SUCCESS! Connected to account: {acc.account_number}")
except Exception as e:
    print(f"❌ STILL UNAUTHORIZED: {e}")
