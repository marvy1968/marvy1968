#!/usr/bin/env python3
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, StopOrderRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide, TimeInForce

env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

tc = TradingClient(API_KEY, API_SECRET, paper=True)

# 1. Deduplicate Pending Open Orders
req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
open_orders = tc.get_orders(filter=req)
seen = set()

print("[1] Deduplicating pending orders...")
for o in open_orders:
    if o.symbol in seen:
        print(f"[-] Canceling duplicate pending order for {o.symbol} (ID: {o.id})")
        tc.cancel_order_by_id(o.id)
    else:
        seen.add(o.symbol)
        print(f"[+] Retaining single order: {o.side.name} {o.qty}x {o.symbol} (ID: {o.id})")

# 2. Attach GTC Stops and Targets to Unprotected Positions
POS_PARAMS = {
    "APD": {"stop": 286.00, "target": 305.00},
    "LLY": {"stop": 1095.00, "target": 1175.00},
    "MA":  {"stop": 558.00, "target": 592.00},
    "MRK": {"stop": 141.00, "target": 151.00}
}

print("\n[2] Checking and attaching GTC protective brackets to active positions...")
positions = tc.get_all_positions()
for p in positions:
    sym = p.symbol
    qty = int(float(p.qty))
    if sym in POS_PARAMS:
        cfg = POS_PARAMS[sym]
        try:
            # Re-attach GTC Stop Loss
            stop_order = StopOrderRequest(
                symbol=sym,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=cfg["stop"]
            )
            tc.submit_order(stop_order)
            print(f"[+] {sym} ({qty} shares): Attached GTC Stop-Loss @ ${cfg['stop']}")

            # Re-attach GTC Take-Profit
            tp_order = LimitOrderRequest(
                symbol=sym,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                limit_price=cfg["target"]
            )
            tc.submit_order(tp_order)
            print(f"[+] {sym} ({qty} shares): Attached GTC Take-Profit @ ${cfg['target']}")
        except Exception as e:
            print(f"[!] Failed to attach brackets for {sym}: {e}")

print("\n[✓] Cleanup and GTC re-attachment complete.")
