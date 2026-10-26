import os
import json
import requests
from datetime import datetime, timedelta
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# --- CONFIGURATION ---
UNIVERSE = {
    "Large Caps": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"],
    "Mid Caps": ["CROX", "CELH", "WING", "ONON", "SMCI"],
    "Small Caps": ["IONQ", "LAES", "SOUN", "RGTI", "BBAI"]
}

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not found in environment. Skipping notification.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    if response.status_code != 200:
        print(f"Failed to send Telegram message: {response.text}")

def main():
    api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("API_KEY")
    secret_key = os.getenv("APCA_API_SECRET_KEY") or os.getenv("SECRET_KEY")
    client = StockHistoricalDataClient(api_key, secret_key)
    
    all_symbols = [sym for cat in UNIVERSE.values() for sym in cat]
    print(f"Fetching 365-day data matrix for {len(all_symbols)} assets...")
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=520) # Ensures 365 trading sessions
    
    request_params = StockBarsRequest(
        symbol_or_symbols=all_symbols,
        timeframe=TimeFrame.Day,
        start=start_date,
        end=end_date
    )
    
    bars = client.get_stock_bars(request_params)
    df = bars.df
    
    report_lines = ["📊 *MULTICAP ROLLING ZONE REPORT CARD* 📊\n*Window:* Rolling 365 Trading Days\n"]
    master_manifest = {}
    
    for category, symbols in UNIVERSE.items():
        report_lines.append(f"\n*{category}*")
        master_manifest[category] = {}
        
        for symbol in symbols:
            try:
                sym_df = df.loc[symbol].tail(365)
                if len(sym_df) < 50:
                    continue
                
                close = sym_df['close']
                high = sym_df['high']
                low = sym_df['low']
                volume = sym_df['volume']
                
                buy_zone = float(close.quantile(0.15))
                sell_zone = float(close.quantile(0.85))
                hvn = float((close * volume).sum() / volume.sum())
                
                tr1 = high - low
                tr2 = (high - close.shift(1)).abs()
                tr3 = (low - close.shift(1)).abs()
                atr = float(pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean().iloc[-1])
                curr_price = float(close.iloc[-1])
                
                tp_long = buy_zone + (2.0 * atr)
                
                master_manifest[category][symbol] = {
                    "price": round(curr_price, 2),
                    "buy_zone": round(buy_zone, 2),
                    "sell_zone": round(sell_zone, 2),
                    "target": round(tp_long, 2)
                }
                
                line = f"`{symbol:<5}` | P: `${curr_price:<6.2f}` | BZ: `${buy_zone:<6.2f}` | TP: `${tp_long:<6.2f}`"
                report_lines.append(line)
            except Exception as e:
                print(f"Error processing {symbol}: {e}")
                
    with open("active_zones.json", "w") as f:
        json.dump(master_manifest, f, indent=4)
        
    full_report = "\n".join(report_lines)
    if len(full_report) > 4000:
        full_report = full_report[:4000] + "\n...[truncated]"
        
    print(full_report)
    send_telegram_message(full_report)
    print("\nReport card successfully sent to Telegram and saved locally!")

if __name__ == "__main__":
    main()
