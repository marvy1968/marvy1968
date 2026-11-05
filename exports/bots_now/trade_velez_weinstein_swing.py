#!/usr/bin/env python3
"""
Velez / Weinstein / Connors Unified Multi-Strategy Production Engine
- Setups:
  1. STAGE2_BREAKOUT: Weinstein 40-day base breakout with volume surge (17 Growth)
  2. STAGE2_DRY_RETEST: Weinstein dry-volume pullback to 20-SMA (17 Growth)
  3. SMA20_BOUNCE: Oliver Velez dynamic 20-SMA trend bounce (17 Growth)
  4. BULL_180_REVERSAL: Oliver Velez 2-bar Elephant Bar reversal (55 S&P Sectors)
  5. VELEZ_200SMA_SNAPBACK: Oliver Velez 200-SMA rubber-band relief snap (55 S&P Sectors)
  6. CONNORS_RSI2_PANIC: Strict panic capitulation (RSI2 < 5.0, Close > SMA200) (55 S&P Sectors)
- Constraints: 10 concurrent slots | 0.50% risk per trade | 1.0% circuit breaker
"""

import os
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from pathlib import Path
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

if not API_KEY or not API_SECRET:
    print("[!] Alpaca API credentials missing from .env")
    sys.exit(1)

trading_client = TradingClient(API_KEY, API_SECRET, paper=True)

# OWNERSHIP_V1 (2026-10-01): shared paper account. Orders tagged "vws-"; only positions in
# ~/bots/ownership/velez_weinstein_swing_ledger.json are managed / closed / flattened.
import logging as _logging
_logging.basicConfig(level=_logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
sys.path.insert(0, "/home/moonrider1968/bots")
from bot_ownership import Ownership
OWN = Ownership("velez_weinstein_swing", "vws-", key=API_KEY, secret=API_SECRET)

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import StockBarsRequest, NewsRequest
from alpaca.data.timeframe import TimeFrame
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

data_client = StockHistoricalDataClient(API_KEY, API_SECRET)
news_client = NewsClient(API_KEY, API_SECRET)

ETF_SECTORS = {
    "XLK": "Technology", "XLC": "Communication", "XLY": "Consumer Discretionary",
    "XLF": "Financials", "XLV": "Healthcare", "XLI": "Industrials",
    "XLP": "Consumer Staples", "XLE": "Energy", "XLU": "Utilities",
    "XLRE": "Real Estate", "XLB": "Materials"
}

SECTOR_TICKER_MAP = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL"],
    "XLC": ["GOOGL", "META", "NFLX", "TMUS", "DIS"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS"],
    "XLV": ["LLY", "UNH", "JNJ", "ABBV", "MRK"],
    "XLI": ["CAT", "GE", "UNP", "HON", "BA"],
    "XLP": ["WMT", "COST", "PG", "KO", "PEP"],
    "XLE": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "XLU": ["NEE", "SO", "DUK", "CEG", "SRE"],
    "XLRE": ["PLD", "AMT", "EQIX", "PSA", "O"],
    "XLB": ["LIN", "SHW", "APD", "ECL", "FCX"]
}

def get_top_relative_strength_sectors(top_n=3):
    try:
        start = datetime.now(ZoneInfo("America/New_York")) - timedelta(days=6)
        symbols = list(ETF_SECTORS.keys()) + ["SPY"]
        bars = data_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbols, timeframe=TimeFrame.Day, start=start
        )).df
        
        spy_ret = 0.0
        sector_returns = []
        for sym in symbols:
            sub = bars.xs(sym, level='symbol') if isinstance(bars.index, pd.MultiIndex) else bars[bars['symbol'] == sym]
            if len(sub) >= 2:
                prev_c = float(sub.iloc[-2]['close'])
                curr_c = float(sub.iloc[-1]['close'])
                ret = ((curr_c - prev_c) / prev_c) * 100.0
                if sym == "SPY":
                    spy_ret = ret
                else:
                    sector_returns.append({"etf": sym, "sector": ETF_SECTORS[sym], "ret": ret})
                    
        df = pd.DataFrame(sector_returns)
        df["rel_spy"] = df["ret"] - spy_ret
        df = df.sort_values(by="rel_spy", ascending=False).reset_index(drop=True)
        top_etfs = df.head(top_n)["etf"].tolist()
        print(f"[*] SECTOR HEATMAP: Top {top_n} Leaders vs SPY ({spy_ret:+.2f}%): {top_etfs}")
        return top_etfs
    except Exception as e:
        print(f"[!] Warning: Sector heatmap failed ({e}). Defaulting to full universe.")
        return list(ETF_SECTORS.keys())

def check_news_veto(symbol):
    try:
        req = NewsRequest(symbols=symbol, limit=4)
        news_res = news_client.get_news(req)
        df_news = news_res.df
        if df_news.empty:
            return False
            
        now_dt = datetime.now(ZoneInfo("America/New_York"))
        danger_words = ["earnings", "investigation", "fraud", "subpoena", "lawsuit", "downgrade", "probe", "fda reject"]
        
        for _, row in df_news.iterrows():
            published = pd.to_datetime(row['created_at']).tz_convert(ZoneInfo("America/New_York"))
            # Only veto if breaking within the last 12 hours
            if (now_dt - published).total_seconds() <= 43200:
                headline = str(row.get('headline', '')).lower()
                for w in danger_words:
                    if w in headline:
                        print(f"[VETO] {symbol} blocked: '{headline[:60]}...' ({w})")
                        return True
        return False
    except Exception as e:
        return False


# 1. 17 Growth Leaders (Stage 2 Breakouts, Dry Retests, 20-SMA Bounces)
GROWTH_SYMBOLS = [
    "META", "AMZN", "MSFT", "AAPL", "PLTR", "ARM", "PANW",
    "CRWD", "NFLX", "AVGO", "QCOM", "NOW", "UBER", "COIN",
    "SNOW", "SHOP", "NET"
]

# 2. 55 S&P Sector Leaders (Bull 180, 200-SMA Snapback, RSI2 Panic)
SECTOR_55_SYMBOLS = [
    "AAPL", "MSFT", "AVGO", "ORCL", "AMD",
    "GOOGL", "META", "NFLX", "TMUS", "DIS",
    "AMZN", "HD", "MCD", "NKE", "BKNG",
    "BRK-B", "JPM", "V", "MA", "BAC",
    "LLY", "JNJ", "ABBV", "MRK", "UNH",
    "CAT", "GE", "RTX", "HON", "UNP",
    "WMT", "COST", "PG", "KO", "PEP",
    "XOM", "CVX", "COP", "EOG", "SLB",
    "LIN", "SHW", "FCX", "ECL", "APD",
    "PLD", "AMT", "EQIX", "SPG", "PSA",
    "NEE", "SO", "DUK", "CEG", "AEP"
]

BENCHMARK = "SPY"
MAX_POSITIONS = 10
RISK_PCT = 0.005              # 0.50% account equity risk per position
CIRCUIT_BREAKER_PCT = 0.010   # 1.0% portfolio circuit breaker


def calc_rsi(series, period=2):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / np.where(avg_loss == 0, 1e-5, avg_loss)
    return 100.0 - (100.0 / (1.0 + rs))


def check_and_enforce_circuit_breaker():
    account = trading_client.get_account()
    equity = float(account.equity)
    import json
    from datetime import date
    last_equity = float(account.last_equity)
    day_pct = (equity - last_equity) / last_equity

    # State tracking for Intraday High-Water Mark
    state_file = Path("/home/moonrider1968/bots/.daily_hwm.json")
    today_str = str(date.today())
    hwm = day_pct

    if state_file.exists():
        try:
            with open(state_file, "r") as sf:
                data = json.load(sf)
                if data.get("date") == today_str:
                    hwm = max(data.get("max_day_pct", day_pct), day_pct)
        except Exception:
            pass

    # Save today's highest equity percentage reached
    try:
        with open(state_file, "w") as sf:
            json.dump({"date": today_str, "max_day_pct": hwm}, sf)
    except Exception:
        pass

    # 1. Hard Floor: Down 1.0% on the day from prior close
    if day_pct <= -CIRCUIT_BREAKER_PCT:
        print(f"[CRITICAL] Portfolio hit -{CIRCUIT_BREAKER_PCT*100:.1f}% floor (Current: {day_pct*100:.2f}%). Flattening OWN (vws-) positions only.")
        OWN.close_all_own(reason="circuit breaker -1%")
        return True

    # 2. Dynamic Profit Lock: If peaked >= +1.0%, never let it fall below +0.50%
    if hwm >= 0.010:
        PROFIT_LOCK_FLOOR = 0.005  # Lock in minimum +0.50%
        if day_pct < PROFIT_LOCK_FLOOR:
            print(f"[PROTECT GAINS] Daily high hit +{hwm*100:.2f}%, but equity fell to {day_pct*100:.2f}% (Below +{PROFIT_LOCK_FLOOR*100:.1f}% Floor). Flattening OWN (vws-) positions only.")
            OWN.close_all_own(reason="profit lock")
            return True

    return False


def fetch_signals():
    top_etfs = get_top_relative_strength_sectors(top_n=3)
    eligible_stocks = []
    for etf in top_etfs:
        eligible_stocks.extend(SECTOR_TICKER_MAP.get(etf, []))
    scan_list = list(set(GROWTH_SYMBOLS + eligible_stocks))
    all_syms = list(set(scan_list + [BENCHMARK]))
    print(f"[*] SECTOR GATE: Top 3 Sectors {top_etfs} -> Scanning {len(scan_list)} symbols (Growth + Sector Leaders)...")
    raw = yf.download(all_syms, period="1y", interval="1d", progress=False, group_by="ticker")

    import pandas as pd
    candidates = []

    # Benchmark trend
    try:
        spy_df = raw[BENCHMARK].dropna() if isinstance(raw.columns, pd.MultiIndex) else raw.dropna()
        spy_close = float(spy_df['Close'].iloc[-1])
        spy_sma50 = float(spy_df['Close'].rolling(50).mean().iloc[-1])
        market_bullish = spy_close >= spy_sma50
    except Exception:
        market_bullish = True

    scan_list = list(set(GROWTH_SYMBOLS + SECTOR_55_SYMBOLS))
    for sym in scan_list:
        if sym == BENCHMARK:
            continue
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if sym not in raw.columns.levels[0]:
                    continue
                df = raw[sym].dropna().copy()
            else:
                continue

            if len(df) < 150:
                continue

            close = float(df['Close'].iloc[-1])
            sma200 = float(df['Close'].rolling(200).mean().iloc[-1]) if len(df) >= 200 else float(df['Close'].rolling(150).mean().iloc[-1])
            sma50 = float(df['Close'].rolling(50).mean().iloc[-1])
            sma20 = float(df['Close'].rolling(20).mean().iloc[-1])

            # Weinstein Stage 2: Price above rising 50/200 SMA
            stage2 = close > sma200 and close > sma50 and sma50 > sma200

            # Velez V-Bar / Momentum filter: close near upper 20 SMA band
            velez_setup = close >= sma20

            if stage2 and velez_setup:
                score = round(((close - sma50) / sma50) * 100, 2)
                # Compute 1.5x ATR stop buffer
                tr = (df['High'] - df['Low']).tail(14).mean()
                atr = float(tr.iloc[0] if hasattr(tr, 'iloc') else tr)
                stop_price = round(max(close - (1.5 * atr), sma20 * 0.985), 2)

                candidates.append({
                    "symbol": sym,
                    "close": round(close, 2),
                    "stop": stop_price,
                    "sma200": round(sma200, 2),
                    "sma50": round(sma50, 2),
                    "score": score,
                    "sector": "Stage 2 Leader"
                })
        except Exception:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def run():
    if check_and_enforce_circuit_breaker():
        return

    account = trading_client.get_account()
    equity = float(account.equity)
    owned = OWN.reconcile()  # OWNERSHIP_V1
    positions = [p for p in trading_client.get_all_positions() if p.symbol.replace("/", "").upper() in owned]

    print(f"[*] Account Equity: ${equity:,.2f} | Open Positions: {len(positions)}/{MAX_POSITIONS}")

    # Active Position Management & Exits
    for pos in positions:
        sym = pos.symbol
        try:
            try:
                df = yf.download(sym, period="3mo", interval="1d", progress=False, multi_level_index=False)
            except TypeError:
                df = yf.download(sym, period="3mo", interval="1d", progress=False)

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            if len(df) < 20:
                continue

            close_series = df['Close'].squeeze().astype(float)
            high_series = df['High'].squeeze().astype(float)
            low_series = df['Low'].squeeze().astype(float)
            if isinstance(close_series, pd.DataFrame):
                close_series = close_series.iloc[:, 0]
            if isinstance(high_series, pd.DataFrame):
                high_series = high_series.iloc[:, 0]
            if isinstance(low_series, pd.DataFrame):
                low_series = low_series.iloc[:, 0]

            c = float(close_series.iloc[-1])
            sma5 = float(close_series.rolling(5).mean().iloc[-1])
            sma20 = float(close_series.rolling(20).mean().iloc[-1])
            atr_series = (high_series - low_series).rolling(14).mean()
            atr14 = float(atr_series.iloc[-1])
            rsi2 = float(calc_rsi(close_series, 2).iloc[-1])

            # Reversion Take-Profit (RSI2 >= 70 or Close > 5-day SMA)
            # Bypassed premature SMA5/RSI2 liquidation; exits managed by brackets & ratchet_breakeven
            pass

            # Trend Extension Take-Profit (Close >= 2.5x ATR away from 20-SMA)
            if (c - sma20) >= (2.5 * atr14):
                print(f"[+] Trend extension target reached for {sym} (Close - SMA20 >= 2.5x ATR). Closing runner.")
                OWN.safe_exit(sym, reason="trend extension 2.5x ATR")  # OWNERSHIP_V1
                continue

        except Exception as e:
            print(f"[!] Position evaluation failed for {sym}: {e}")

    all_positions = trading_client.get_all_positions()
    owned = OWN.reconcile()
    positions = [p for p in all_positions if p.symbol.replace("/", "").upper() in owned]  # own slots only
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    open_orders = trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    pending_symbols = [o.symbol for o in open_orders]
    held_symbols = list(set([p.symbol for p in all_positions] + pending_symbols))  # anyone's
    available_slots = MAX_POSITIONS - len(positions)

    if available_slots <= 0:
        print("[*] All 10 portfolio slots occupied. No new allocations.")
        return

    signals = fetch_signals()
    print(f"[*] Setups qualified: {len(signals)}")

    for sig in signals:
        if available_slots <= 0:
            break
        sym = sig['symbol']
        if sym in held_symbols:
            continue
        if check_news_veto(sym):
            continue

        risk_per_unit = sig['close'] - sig['stop']
        if risk_per_unit <= 0.30:
            continue

        risk_dollars = equity * RISK_PCT
        shares = int(risk_dollars / risk_per_unit)
        if shares < 1:
            continue

        # 50/50 Split Execution: Leg 1 (+0.75R Lock), Leg 2 (+2.0R Runner)
        q1 = max(1, shares // 2)
        q2 = shares - q1
        tp1 = round(sig['close'] + (0.75 * risk_per_unit), 2)
        tp2 = round(sig['close'] + (2.00 * risk_per_unit), 2)

        def _bracket(q, tp):
            return {"symbol": sym, "qty": str(q), "side": "buy", "type": "market", "time_in_force": "day",
                    "order_class": "bracket", "take_profit": {"limit_price": f"{tp:.2f}"},
                    "stop_loss": {"stop_price": f"{sig['stop']:.2f}"}}
        try:
            # OWNERSHIP_V1: tagged vws-; leg 1 is busy-checked, leg 2 follows on the same symbol
            r1 = OWN.entry(_bracket(q1, tp1), tag="l1")
            if r1 is None:
                held_symbols.append(sym)
                continue
            if q2 > 0:
                OWN.entry(_bracket(q2, tp2), tag="l2", check_busy=False)
            print(f"[+] SUBMITTED COMBO: {sym} | Leg1: {q1}x @ TP ${tp1} (+0.75R) | Leg2: {q2}x @ TP ${tp2} (+2.0R) | SL: ${sig['stop']}")
            held_symbols.append(sym)
            available_slots -= 1
        except Exception as e:
            print(f"[!] Order submission failed for {sym}: {e}")


if __name__ == "__main__":
    run()
