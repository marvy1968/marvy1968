#!/bin/bash

export OANDA_ACCOUNT_ID="REDACTED_OANDA_ACCOUNT"
export OANDA_API_TOKEN = "REDACTED"
export OANDA_URL="https://api-fxpractice.oanda.com"

/home/moonrider1968/bots/oanda_active/venv/bin/python -c '
import os, requests
account_id = os.getenv("OANDA_ACCOUNT_ID")
token = os.getenv("OANDA_API_TOKEN")
url = os.getenv("OANDA_URL")

headers = {"Authorization": f"Bearer {token}"}
resp = requests.get(f"{url}/v3/accounts/{account_id}/summary", headers=headers, timeout=10)
print(f"Oanda API Test Status Code: {resp.status_code}")
if resp.status_code == 200:
    acc = resp.json().get("account", {})
    bal = float(acc.get("balance", 0))
    curr = acc.get("currency")
    trades = acc.get("openTradeCount")
    print(f"[SUCCESS] Connected to Oanda Practice Account!")
    print(f"  Balance : ${bal:,.2f}")
    print(f"  Currency: {curr}")
    print(f"  Open Trades: {trades}")
else:
    print(f"[ERROR] Connection failed: {resp.text}")
'
