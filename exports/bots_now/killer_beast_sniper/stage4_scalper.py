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
import logging
import pandas as pd
import numpy as np

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import (
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("STAGE4_SHORT_SCALPER")

# --- 1. STAGE 4 REGIME DETECTION ---
def check_stage4_regime(df_1h: pd.DataFrame) -> bool:
    """
    Checks if a symbol is in a macro Stage 4 Downtrend:
    - 50 SMA < 200 SMA
    - Price < 50 SMA
    """
    if df_1h is None or len(df_1h) < 200:
        return False
    try:
        c = df_1h["close"]
        sma_50 = c.rolling(50).mean().iloc[-1]
        sma_200 = c.rolling(200).mean().iloc[-1]
        curr_close = c.iloc[-1]

        return bool((curr_close < sma_50) and (sma_50 < sma_200))
    except Exception as e:
        log.error(f"Stage 4 Regime Check Error: {e}")
        return False

# --- 2. VWAP & UPPER BAND CALCULATION ---
def calculate_vwap_and_bands(df_1m: pd.DataFrame) -> tuple:
    """
    Calculates intraday VWAP and standard deviation bands (2.5x upper band for short entries).
    """
    if df_1m is None or len(df_1m) < 20:
        return None, None, None

    try:
        typical_price = (df_1m["high"] + df_1m["low"] + df_1m["close"]) / 3.0
        pv = typical_price * df_1m["volume"]
        
        vwap = pv.sum() / (df_1m["volume"].sum() + 1e-9)
        std_dev = np.sqrt(((typical_price - vwap) ** 2).sum() / len(df_1m))

        upper_band = vwap + (2.5 * std_dev)
        lower_band = vwap - (2.5 * std_dev)

        return float(vwap), float(upper_band), float(lower_band)
    except Exception as e:
        log.error(f"VWAP Band Error: {e}")
        return None, None, None

# --- 3. SHORT BRACKET ORDER EXECUTION ---
def execute_short_order(
    trading_client: TradingClient,
    symbol: str,
    qty: float,
    entry_price: float,
    stop_loss_price: float,
    take_profit_price: float
):
    """
    Submits a SHORT market order with attached bracket stop-loss and take-profit exit targets.
    Note: For short orders, OrderSide is SELL, stop_loss is HIGHER than entry, take_profit is LOWER.
    """
    try:
        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,  # Sell to Open (Short)
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_loss_price, 2))
        )
        
        order = trading_client.submit_order(order_data)
        log.info(f"🔴 STAGE 4 SHORT EXECUTED | {symbol} | Qty: {qty} @ ~${entry_price:.2f} | SL: ${stop_loss_price:.2f} | TP: ${take_profit_price:.2f}")
        return order
    except Exception as e:
        log.error(f"Short Order Execution Failed for {symbol}: {e}")
        return None

if __name__ == "__main__":
    print("✅ Stage 4 Short Scalper Module Loaded Successfully.")
