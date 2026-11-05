#!/usr/bin/env python3
import os
import sys
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import StockBarsRequest, NewsRequest
from alpaca.data.timeframe import TimeFrame

load_dotenv("/home/moonrider1968/bots/.env")

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
news_client = NewsClient(API_KEY, SECRET_KEY)

ETF_SECTORS = {
    "XLK": "Technology",
    "XLC": "Communication",
    "XLY": "Consumer Discretionary",
    "XLF": "Financials",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLB": "Materials"
}

def scan_heatmap():
    print("=" * 80)
    print("S&P 500 11-SECTOR ETF HEATMAP & RANKING (Relative to SPY)")
    print("=" * 80)
    
    start = datetime.now(ZoneInfo("America/New_York")) - timedelta(days=6)
    symbols = list(ETF_SECTORS.keys()) + ["SPY"]
    
    bars = data_client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start
    )).df
    
    rows = []
    spy_ret = 0.0

    for sym in symbols:
        sub = bars.xs(sym, level='symbol') if isinstance(bars.index, pd.MultiIndex) else bars[bars['symbol'] == sym]
        if len(sub) >= 2:
            prev_c = float(sub.iloc[-2]['close'])
            curr_c = float(sub.iloc[-1]['close'])
            ret = ((curr_c - prev_c) / prev_c) * 100.0
            if sym == "SPY":
                spy_ret = ret
            else:
                rows.append({"ETF": sym, "Sector": ETF_SECTORS[sym], "Return_%": ret})

    df = pd.DataFrame(rows)
    df["Rel_SPY_%"] = df["Return_%"] - spy_ret
    df = df.sort_values(by="Rel_SPY_%", ascending=False).reset_index(drop=True)

    print(f"Benchmark SPY Daily Return: {spy_ret:+.2f}%\n")
    print(df.to_string(index=False, formatters={
        "Return_%": "{:+.2f}%".format,
        "Rel_SPY_%": "{:+.2f}%".format
    }))
    
    top_3 = df.head(3)["ETF"].tolist()
    print("\n" + "=" * 80)
    print(f"TOP 3 LEADING SECTORS ELIGIBLE FOR SWING ENTRIES: {top_3}")
    print("=" * 80)
    return top_3

def scan_news(top_etfs):
    sys.path.append("/home/moonrider1968/bots")
    from test_stocks_sector_breakdown import SECTORS
    
    candidate_tickers = []
    for sector_name, tickers in SECTORS.items():
        for etf in top_etfs:
            if f"({etf})" in sector_name:
                candidate_tickers.extend(tickers)
                
    syms_query = ",".join(candidate_tickers[:10])
    print(f"\nQuerying Alpaca News API for Candidates: {syms_query}...")
    
    req = NewsRequest(symbols=syms_query, limit=5)
    news_res = news_client.get_news(req)
    news_df = news_res.df
    
    if news_df.empty:
        print("No recent breaking news returned.")
        return

    print(f"Retrieved {len(news_df)} Recent Headlines:\n")
    for _, row in news_df.head(5).iterrows():
        created = pd.to_datetime(row['created_at']).strftime("%Y-%m-%d %H:%M EDT")
        symbols = row.get('symbols', [])
        headline = row.get('headline', 'N/A')
        source = row.get('source', 'Alpaca Feed')
        print(f"[{created}] [{symbols}] {headline}")
        print(f"  Source: {source}\n")
    print("=" * 80)

if __name__ == "__main__":
    top_etfs = scan_heatmap()
    scan_news(top_etfs)
