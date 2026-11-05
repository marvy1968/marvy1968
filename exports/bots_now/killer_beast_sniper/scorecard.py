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

import json
import os
from alpaca.trading.client import TradingClient

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"
STATE_FILE = "/home/moonrider1968/bots/killer_beast_sniper/regime_state.json"

client = TradingClient(API_KEY, SECRET_KEY, paper=True)

def show_scorecard():
    acct = client.get_account()
    positions = client.get_all_positions()
    
    equity = float(acct.equity)
    last_equity = float(acct.last_equity)
    pnl = equity - last_equity
    pnl_pct = (pnl / last_equity) * 100 if last_equity > 0 else 0
    
    regime = "UNKNOWN"
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                regime = state.get("regime", "UNKNOWN")
        except:
            pass

    print("==================================================")
    print("           LIVE BOT FLEET SCORECARD               ")
    print("==================================================")
    print(f" Macro Market Regime : {regime}")
    print(f" Account Equity     : ${equity:,.2f}")
    print(f" Day P&L            : ${pnl:,.2f} ({pnl_pct:+.2f}%)")
    print(f" Buying Power       : ${float(acct.buying_power):,.2f}")
    print(f" Active Positions   : {len(positions)} / 20 Max")
    print("--------------------------------------------------")
    
    if not positions:
        print(" No active positions held.")
    else:
        print(f"{'SYMBOL':<7} {'SIDE':<6} {'QTY':<5} {'ENTRY':<9} {'CURRENT':<9} {'UNREALIZED P&L'}")
        print("--------------------------------------------------")
        for p in positions:
            qty = float(p.qty)
            side = "SHORT" if qty < 0 else "LONG"
            unrealized = float(p.unrealized_pl)
            entry = float(p.avg_entry_price)
            curr = float(p.current_price)
            print(f"{p.symbol:<7} {side:<6} {abs(int(qty)):<5} ${entry:<8.2f} ${curr:<8.2f} ${unrealized:+,.2f}")
    print("==================================================")

if __name__ == "__main__":
    show_scorecard()
