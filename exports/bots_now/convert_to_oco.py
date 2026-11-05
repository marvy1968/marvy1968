#!/usr/bin/env python3
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    TakeProfitRequest,
    StopLossRequest
)
from alpaca.trading.enums import QueryOrderStatus, OrderSide, TimeInForce, OrderClass

env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

tc = TradingClient(API_KEY, API_SECRET, paper=True)

# Positions target and stop levels
OCO_CONFIG = {
    "APD": {"stop": 286.00, "target": 305.00},
    "LLY": {"stop": 1095.00, "target": 1175.00},
    "MA":  {"stop": 558.00, "target": 592.00},
    "MRK": {"stop": 141.00, "target": 151.00}
}

print("[+] Submitting native linked OCO orders...")
positions = tc.get_all_positions()
for p in positions:
    sym = p.symbol
    qty = int(float(p.qty))
    if sym in OCO_CONFIG:
        cfg = OCO_CONFIG[sym]
        try:
            oco_order = LimitOrderRequest(
                symbol=sym,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=cfg["target"]),
                stop_loss=StopLossRequest(stop_price=cfg["stop"])
            )
            tc.submit_order(oco_order)
            print(f"[✓] {sym} ({qty} shares): Attached OCO Bracket | TP: ${cfg['target']} | SL: ${cfg['stop']}")
        except Exception as e:
            print(f"[!] Failed to attach OCO for {sym}: {e}")

print("\n[✓] OCO submission complete.")
