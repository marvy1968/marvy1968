import os
import time
import requests
import csv
from datetime import datetime

# Load Alpaca credentials
api_key = os.environ.get("APCA_API_KEY_ID")
api_secret = os.environ.get("APCA_API_SECRET_KEY")
base_url = os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")

# Fallback auto-discovery if needed
if not api_key or not api_secret:
    for file in os.listdir("."):
        if "env" in file or "config" in file or "bot" in file:
            try:
                with open(file, "r") as f:
                    for line in f:
                        if "API_KEY" in line and not api_key:
                            parts = line.replace("'", "").replace('"', "").strip().split("=")
                            if len(parts) > 1: api_key = parts[1].strip()
                        if "SECRET" in line and not api_secret:
                            parts = line.replace("'", "").replace('"', "").strip().split("=")
                            if len(parts) > 1: api_secret = parts[1].strip()
            except:
                pass

CSV_FILE = "trade_journal.csv"

def init_journal():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode="w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Symbol", "Side", "Qty", "Type", "Status", "Filled_Avg_Price"])

def log_recent_trades():
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret
    }
    try:
        # Fetch closed/filled orders
        response = requests.get(f"{base_url}/v2/orders?status=closed&limit=20", headers=headers)
        if response.status_code == 200:
            orders = response.json()
            init_journal()
            
            # Read existing entries to avoid duplicates
            existing_ids = set()
            if os.path.exists(CSV_FILE):
                with open(CSV_FILE, mode="r") as f:
                    reader = csv.reader(f)
                    next(reader, None) # skip header
                    # we can track by timestamp+symbol+side if needed, or just append new fills
            
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Polling Alpaca for completed trades...")
            
            with open(CSV_FILE, mode="a", newline="") as f:
                writer = csv.writer(f)
                for o in reversed(orders):
                    if o.get("status") == "filled":
                        writer.writerow([
                            o.get("filled_at") or o.get("created_at"),
                            o.get("symbol"),
                            o.get("side"),
                            o.get("qty"),
                            o.get("type"),
                            o.get("status"),
                            o.get("filled_avg_price")
                        ])
            print(f" -> Journal updated. Total checked: {len(orders)} orders.")
    except Exception as e:
        print(f"[ERROR] Failed to fetch trades: {e}")

if __name__ == "__main__":
    print("==================================================")
    print("ALPACA AUTOMATED TRADE JOURNAL DAEMON ACTIVE")
    print(f"Logging to: {os.path.abspath(CSV_FILE)}")
    print("==================================================")
    
    init_journal()
    while True:
        log_recent_trades()
        time.sleep(900)  # Check every 15 minutes
