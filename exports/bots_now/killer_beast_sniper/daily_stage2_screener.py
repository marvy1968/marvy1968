#!/usr/bin/env python3
import json
import os
from dotenv import load_dotenv
import logging
import pandas as pd
from datetime import datetime, timezone, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").strip().lower() == "true"

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    raise RuntimeError(
        "Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env"
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("TIER1_WEINSTEIN_SCREENER")

API_KEY = ALPACA_API_KEY
SECRET_KEY = ALPACA_SECRET_KEY
BENCHMARK  = "SPY"

def get_sp500_tickers():
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        import requests
        from io import StringIO

        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 "
                    "Chrome/124.0 Safari/537.36"
                )
            },
            timeout=20,
        )
        response.raise_for_status()

        tables = pd.read_html(StringIO(response.text))
        df = tables[0]
        tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()
        log.info(f"📋 Fetched {len(tickers)} S&P 500 tickers from Wikipedia.")
        return tickers
    except Exception as e:
        log.warning(f"Could not scrape S&P 500 list ({e}). Using core fallback universe.")
        return [
            "AAPL","MSFT","NVDA","AVGO","ORCL","META","GOOGL","NFLX","AMZN","TSLA",
            "JPM","BAC","WFC","GS","LLY","UNH","JNJ","CAT","GE","XOM","CVX","COP",
            "AMD","QCOM","TXN","AMAT","MU","INTC","PANW","CRWD","SNPS","CDNS",
            "COST","WMT","HD","LOW","PG","KO","PEP","LIN","APD","FCX","NEM","NEE"
        ]

UNIVERSE = get_sp500_tickers()
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

def generate_daily_hotlist():
    log.info("🔎 INITIATING S&P 500 TIER 1 WEINSTEIN SCREENER (IEX Feed)...")
    start_date = datetime.now(timezone.utc) - timedelta(days=250)
    
    hotlist = []
    chunk_size = 50
    chunks = [UNIVERSE[i:i + chunk_size] for i in range(0, len(UNIVERSE), chunk_size)]

    # Get SPY benchmark data first
    try:
        spy_req = StockBarsRequest(
            symbol_or_symbols=[BENCHMARK],
            timeframe=TimeFrame(1, TimeFrameUnit.Day),
            start=start_date,
            feed=DataFeed.IEX
        )
        spy_res = data_client.get_stock_bars(spy_req)
        df_spy = spy_res.df.xs(BENCHMARK, level="symbol") if isinstance(spy_res.df.index, pd.MultiIndex) else spy_res.df
    except Exception as e:
        log.error(f"Failed to fetch SPY benchmark data: {e}")
        return

    # Process S&P 500 in 50-ticker sub-batches
    for idx, batch in enumerate(chunks, 1):
        try:
            req = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=start_date,
                feed=DataFeed.IEX
            )
            res = data_client.get_stock_bars(req)
            if res.df is None or res.df.empty: continue

            for sym in batch:
                try:
                    df = res.df.xs(sym, level="symbol") if isinstance(res.df.index, pd.MultiIndex) else res.df
                    if len(df) < 150: continue

                    c = df["close"]
                    sma150 = c.rolling(150).mean()
                    sma150_slope = sma150.diff(10).iloc[-1]

                    common_idx = c.index.intersection(df_spy.index)
                    raw_rs = (c.loc[common_idx] / df_spy["close"].loc[common_idx]) * 100
                    mansfield_rs = ((raw_rs / (raw_rs.rolling(50).mean() + 1e-9)) - 1) * 100

                    curr_close = c.iloc[-1]
                    curr_sma150 = sma150.iloc[-1]
                    curr_mrs = mansfield_rs.iloc[-1]

                    if (curr_close > curr_sma150) and (sma150_slope > 0) and (curr_mrs > 0):
                        hotlist.append(sym)
                        log.info(f"  ✅ CANDIDATE PASSED: {sym.ljust(6)} | Mansfield RS: +{curr_mrs:.2f}%")

                except Exception: continue

        except Exception as e:
            log.error(f"Batch {idx} error: {e}")
            continue

    hotlist_data = {
        "hotlist": hotlist,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(hotlist)
    }

    with open("stage2_hotlist.json", "w") as f:
        json.dump(hotlist_data, f, indent=4)

    log.info(f"🎯 S&P 500 SCREEN COMPLETE: {len(hotlist)} Stage 2 Leaders Saved to stage2_hotlist.json")

if __name__ == "__main__":
    generate_daily_hotlist()
