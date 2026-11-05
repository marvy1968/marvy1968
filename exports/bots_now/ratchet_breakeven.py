#!/usr/bin/env python3
"""
Dynamic Breakeven & Velez Low-of-Prior-Bar Trailing Stop Daemon
- If position reached +0.75R: Ratchets runner stop to Entry + $0.05.
- If runner is in profit and day's low > current stop: Trails stop to (Prior Day Low - $0.05).
"""
import os, sys
from pathlib import Path
from dotenv import load_dotenv
import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import ReplaceOrderRequest, GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide

env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

if not API_KEY or not API_SECRET:
    sys.exit(1)

tc = TradingClient(API_KEY, API_SECRET, paper=True)

# OWNERSHIP_V1 (2026-10-01): shared paper account. This helper serves the VM swing bot
# (trade_velez_weinstein_swing.py) and now ONLY ratchets stop legs of that bot's own
# bracket orders (vws- prefix, from its ownership ledger). Other bots' stops (ab-, sw-, mr-,
# hm-, untagged, ...) are never modified.
sys.path.insert(0, "/home/moonrider1968/bots")
from bot_ownership import Ownership, TERMINAL
OWN = Ownership("velez_weinstein_swing", "vws-", key=API_KEY, secret=API_SECRET)


def own_stop_legs():
    """Yield (symbol, leg_order_dict) for open stop legs of the swing bot's own brackets."""
    for key, rec in OWN.reconcile().items():
        if rec.get("side") != "long":
            continue
        for o in rec.get("orders", []):
            if o.get("role") != "entry" or o.get("done"):
                continue
            od = OWN.get_order(o["id"])
            for leg in (od or {}).get("legs") or []:
                cur, depth = leg, 0
                while cur and cur.get("status") == "replaced" and cur.get("replaced_by") and depth < 6:
                    cur = OWN.get_order(cur["replaced_by"]); depth += 1
                if cur and cur.get("status") not in TERMINAL and cur.get("side") == "sell" and cur.get("stop_price"):
                    yield rec.get("symbol", key), cur


def run_trail():
    positions = {p.symbol: p for p in tc.get_all_positions()}

    for sym, so in own_stop_legs():
        pos = positions.get(sym)
        if pos is None or float(pos.qty) <= 0:
            continue
        entry = float(pos.avg_entry_price)
        cur_price = float(pos.current_price)
        cur_stop = float(so["stop_price"])
        new_stop = cur_stop
        reason = ""

        # Rule 1: Breakeven Ratchet if position is up and stop is still below cost basis
        if cur_price > entry and cur_stop < entry:
            new_stop = round(entry + 0.05, 2)
            reason = "Breakeven Lock"

        # Rule 2: Velez Low Trail if stop is already at/above breakeven
        elif cur_stop >= entry:
            try:
                df = yf.download(sym, period="5d", interval="1d", progress=False)
                if len(df) >= 2:
                    prior_low = float(df['Low'].iloc[-2])
                    candidate_stop = round(prior_low - 0.05, 2)
                    # Stop only ratchets UP, never widens down
                    if candidate_stop > cur_stop and candidate_stop < cur_price:
                        new_stop = candidate_stop
                        reason = f"Velez Prior Bar Low Trail (${prior_low:.2f})"
            except Exception as e:
                print(f"[!] Error fetching daily low for {sym}: {e}")

        if new_stop > cur_stop and new_stop < cur_price:
            st, data = OWN._req("PATCH", "/orders/" + so["id"],
                                body={"stop_price": f"{new_stop:.2f}", "client_order_id": OWN.new_coid("rt")})
            if st in (200, 201):
                print(f"[+] RATCHETED {sym}: Stop moved from ${cur_stop:.2f} -> ${new_stop:.2f} [{reason}]")
            else:
                print(f"[!] Failed to update stop for {sym}: HTTP {st} {str(data)[:200]}")

if __name__ == "__main__":
    run_trail()
