import os
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv

load_dotenv()
client = StockHistoricalDataClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))

sectors = {
    "Technology": ["AAPL","MSFT","NVDA","AVGO","AMD","ADBE","CSCO","INTC","QCOM","TXN"],
    "Consumer Discretionary": ["AMZN","TSLA","HD","MCD","NKE","LOW","SBUX","TJX","BKNG","ABNB"],
    "Communication": ["GOOGL","META","GOOG","NFLX","CMCSA","DIS","VZ","T","TMUS","CHTR"],
    "Financials": ["JPM","V","MA","BAC","WFC","GS","MS","AXP","BLK","C"],
    "Health Care": ["JNJ","UNH","LLY","MRK","ABBV","TMO","PFE","ABT","DHR","AMGN"],
    "Consumer Staples": ["PG","KO","PEP","WMT","COST","PM","MDLZ","CL","MO","EL"],
    "Energy": ["XOM","CVX","COP","EOG","SLB","MPC","PSX","VLO","WMB","OXY"],
    "Industrials": ["GE","CAT","HON","UNP","BA","RTX","ETN","DE","CSX","UPS"],
    "Utilities": ["NEE","SO","DUK","AEP","SRE","XEL","ED","PEG","WEC","ES"],
    "Real Estate": ["PLD","AMT","EQIX","SPG","CCI","PSA","O","EXR","CSGP","VICI"],
    "Materials": ["LIN","FCX","ECL","NEM","APD","SHW","DD","CTVA","IFF","NUE"]
}

def is_bullish_trend(symbol):
    try:
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=datetime.now() - timedelta(days=60))
        bars = client.get_stock_bars(req).df
        if len(bars) < 20: return False
        bars['sma200'] = bars['close'].rolling(200).mean()
        return bars['close'].iloc[-1] > bars['sma200'].iloc[-1]
    except:
        return False

print("=== SECTOR ANALYSIS (6/10 RULE) ===")
active_watchlist = []

for sector, stocks in sectors.items():
    bull_count = sum(1 for s in stocks if is_bullish_trend(s))
    print(f"{sector}: {bull_count}/10 bullish")
    if bull_count >= 6:
        print(f"✅ GREEN → Adding {sector} to watchlist")
        active_watchlist.extend(stocks)
    else:
        print(f"❌ RED → Skipping {sector}")

with open("active_watchlist.txt", "w") as f:
    f.write(",".join(active_watchlist))

print(f"\n✅ Watchlist saved with {len(active_watchlist)} stocks → active_watchlist.txt")
