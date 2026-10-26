import os
from dotenv import load_dotenv

print("--- DIAGNOSTIC ROUTINE ---")
load_dotenv("oanda.env")
token1 = os.getenv("OANDA_ACCESS_TOKEN") or ""
acc1 = os.getenv("OANDA_ACCOUNT_ID") or ""
print(f"From oanda.env       -> Account: {acc1} | Token Length: {len(token1)} chars | Starts with: {token1[:5]}")

load_dotenv("oanda_velez.env", override=True)
token2 = os.getenv("OANDA_ACCESS_TOKEN") or ""
acc2 = os.getenv("OANDA_ACCOUNT_ID") or ""
print(f"From oanda_velez.env -> Account: {acc2} | Token Length: {len(token2)} chars | Starts with: {token2[:5]}")
