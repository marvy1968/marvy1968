#!/usr/bin/env python3

# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import os
import sys
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"
PAPER      = True

trade_client = TradingClient(API_KEY, SECRET_KEY, paper=PAPER)
data_client  = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# Top 10 Leaders in Each of the 11 S&P 500 GICS Sectors (110 Tickers)
SECTOR_UNIVERSE = {
    "Information Technology": ["NVDA", "AAPL", "MSFT", "AVGO", "AMD", "QCOM", "AMAT", "ORCL", "INTC", "CSCO"],
    "Financials":             ["JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "C", "AXP", "SCHW"],
    "Health Care":             ["LLY", "UNH", "JNJ", "ABBV", "MRK", "ABT", "TMO", "AMGN", "PFE", "CVS"],
    "Consumer Discretionary":  ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "TJX", "ORLY"],
    "Communication Services":  ["GOOGL", "META", "NFLX", "TMUS", "VZ", "CMCSA", "DIS", "T", "EA", "WBD"],
    "Industrials":             ["GE", "CAT", "UNP", "RTX", "HON", "DE", "LMT", "BA", "GEV", "EMR"],
    "Consumer Staples":        ["WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "CL", "MDLZ", "TGT"],
    "Energy":                  ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HAL"],
    "Utilities":               ["NEE", "DUK", "SO", "AEP", "SRE", "D", "EXC", "PEG", "ED", "CEG"],
    "Real Estate":             ["PLD", "AMT", "EQIX", "WELL", "SPG", "DLR", "CCI", "O", "PSA", "VICI"],
    "Materials":               ["LIN", "SHW", "APD", "FCX", "NEM", "ECL", "CTVA", "DOW", "DD", "ALB"]
}

class SectorNewsDipEngine:
    def __init__(self, risk_pct=0.01):
        self.risk_pct = risk_pct

    def compute_indicators(self, df):
        df = df.copy()
        df['sma20']  = df['close'].rolling(20).mean()
        df['sma200'] = df['close'].rolling(200).mean()
        
        # 20 SMA Slope
        df['sma20_slope'] = ((df['sma20'] - df['sma20'].shift(5)) / (df['sma20'].shift(5) + 1e-9)) * 100

        # Bollinger Bands (20, 2std)
        df['std20'] = df['close'].rolling(20).std()
        df['bb_lower'] = df['sma20'] - (2.0 * df['std20'])

        # Connors 2-Period RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(2).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(2).mean()
        rs = gain / (loss + 1e-9)
        df['rsi2'] = 100 - (100 / (1 + rs))

        # ATR (14)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        df['atr'] = tr.rolling(14).mean()

        # Session VWAP
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (typical_price * df['volume']).cumsum() / (df['volume'].cumsum() + 1e-9)
        df['vol_sma20'] = df['volume'].rolling(20).mean()

        return df

    def scan_ticker(self, symbol, sector):
        start_date = datetime.now(timezone.utc) - timedelta(days=30)
        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame(15, TimeFrameUnit.Minute),
            start=start_date,
            feed=DataFeed.IEX
        )
        try:
            res = data_client.get_stock_bars(req)
            df = res.df.xs(symbol, level='symbol') if isinstance(res.df.index, pd.MultiIndex) else res.df
        except Exception:
            return None

        if len(df) < 200:
            return None

        df = self.compute_indicators(df)
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # 1. Macro Stage 2 Guard
        is_stage2 = (curr['close'] > curr['sma200']) and (curr['sma20_slope'] > 0.0)
        if not is_stage2:
            return None

        # 2. Panic Selling Exhaustion (RSI(2) < 10 OR Touch Lower Bollinger Band)
        is_oversold = (curr['rsi2'] < 10.0) or (curr['low'] <= curr['bb_lower'])
        is_volume_spike = curr['volume'] > (1.8 * curr['vol_sma20'])

        # 3. Candlestick Reversal Pattern
        body_size = abs(curr['close'] - curr['open'])
        lower_wick = min(curr['open'], curr['close']) - curr['low']
        is_bottom_tail = lower_wick >= (2.0 * max(body_size, 0.01))
        is_engulfing = (curr['close'] > curr['open']) and (curr['close'] > prev['high'])

        has_reversal = is_bottom_tail or is_engulfing

        # 4. VWAP Reclaim Entry
        vwap_reclaim = (curr['close'] > curr['vwap']) and (prev['close'] <= prev['vwap'])

        if is_oversold and is_volume_spike and has_reversal and vwap_reclaim:
            stop_loss = curr['low'] - (0.5 * curr['atr'])
            return {
                "symbol": symbol,
                "sector": sector,
                "entry_price": round(curr['close'], 2),
                "stop_loss": round(stop_loss, 2),
                "rsi2": round(curr['rsi2'], 2),
                "pattern": "BOTTOMING_TAIL" if is_bottom_tail else "ENGULFING"
            }
        return None

    def execute_order(self, setup):
        try:
            acct = trade_client.get_account()
            equity = float(acct.equity)
            risk_amt = equity * self.risk_pct
            risk_per_share = max(0.10, setup['entry_price'] - setup['stop_loss'])
            qty = int(risk_amt / risk_per_share)

            if qty <= 0: return

            logging.info(f"🔥 DIP BUY TRIGGERED: {setup['symbol']} ({setup['sector']})")
            logging.info(f"   Buy {qty} shares @ ${setup['entry_price']} | Stop: ${setup['stop_loss']} | Pattern: {setup['pattern']}")

            order_data = MarketOrderRequest(
                symbol=setup['symbol'],
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.GTC
            )
            order = trade_client.submit_order(order_data)
            logging.info(f"✅ Order Placed: ID {order.id}")

        except Exception as e:
            logging.error(f"❌ Order failed for {setup['symbol']}: {e}")

if __name__ == "__main__":
    logging.info("==========================================================")
    logging.info("   🚀 SCANNING 110 S&P 500 SECTOR LEADERS FOR DIP REBOUNDS  ")
    logging.info("==========================================================")
    
    engine = SectorNewsDipEngine(risk_pct=0.01)
    total_found = 0

    for sector, tickers in SECTOR_UNIVERSE.items():
        logging.info(f"🔍 Scanning Sector: {sector} ({len(tickers)} tickers)...")
        for sym in tickers:
            setup = engine.scan_ticker(sym, sector)
            if setup:
                total_found += 1
                engine.execute_order(setup)

    if total_found == 0:
        logging.info("ℹ️ No multi-factor news dip setups confirmed across all 11 sectors right now.")
