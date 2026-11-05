#!/usr/bin/env python3
"""
Valeze Multi-Strategy Production Audit & Live Monitor
- Tracks: Account Equity, Drawdown Headroom, Open Positions, Live PnL,
          Active Working Bracket Orders, Fill Slippage, and Systemd Service Health.
"""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

# Load Alpaca API Keys
env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

if not API_KEY or not API_SECRET:
    print("[!] Error: Alpaca API credentials not found in /home/moonrider1968/bots/.env")
    sys.exit(1)

tc = TradingClient(API_KEY, API_SECRET, paper=True)

def get_systemd_status(unit_name):
    try:
        res = subprocess.run(
            ["systemctl", "is-active", unit_name],
            capture_output=True, text=True, check=False
        )
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN"

def run_report():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 80)
    print(f"       VALEZE2 BOT SYSTEM STATUS & AUDIT REPORT | {now_str}")
    print("=" * 80)

    # 1. Daemon & Timer Infrastructure Check
    scalper_status = get_systemd_status("alpaca-velez-scalper.service")
    swing_timer_status = get_systemd_status("alpaca-swing-daily.timer")
    print(f"[DAEMONS] Scalper Service: {scalper_status.upper():10s} | Swing Timer: {swing_timer_status.upper()}")
    print("-" * 80)

    # 2. Account & Margin Health
    try:
        account = tc.get_account()
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        cash = float(account.cash)
        buying_power = float(account.buying_power)
        daily_dd_pct = ((equity - last_equity) / last_equity) * 100.0

        print("[ACCOUNT HEALTH]")
        print(f"  Current Equity:     ${equity:>12,.2f}  |  Starting Day Equity: ${last_equity:>12,.2f}")
        print(f"  Cash Balance:       ${cash:>12,.2f}  |  Buying Power:        ${buying_power:>12,.2f}")
        print(f"  Intraday PnL / DD:  {daily_dd_pct:>+11.2f}%  |  Circuit Breaker Cap:  -1.00%")
        
        if daily_dd_pct <= -1.00:
            print("  [!] WARNING: Intraday drawdown cap breached! Circuit breaker triggered.")
        else:
            buffer_pct = 1.00 + daily_dd_pct
            print(f"  Circuit Headroom:    {buffer_pct:>10.2f}% buffer remaining before hard liquidation.")
    except Exception as e:
        print(f"[!] Failed to fetch account status: {e}")
    print("-" * 80)

    # 3. Open Positions & Real-Time Performance
    try:
        positions = tc.get_all_positions()
        print(f"[OPEN POSITIONS: {len(positions)} / 10 Active Slots]")
        if len(positions) == 0:
            print("  No open positions currently active.")
        else:
            print(f"  {'SYMBOL':<6} {'QTY':>5} {'ENTRY PRICE':>12} {'CURRENT':>10} {'UNREALIZED PnL':>16} {'PnL %':>9}")
            print("  " + "-" * 62)
            total_unrealized = 0.0
            for p in positions:
                sym = p.symbol
                qty = int(float(p.qty))
                entry = float(p.avg_entry_price)
                curr = float(p.current_price)
                pnl = float(p.unrealized_pl)
                pnl_pct = float(p.unrealized_plpc) * 100.0
                total_unrealized += pnl
                print(f"  {sym:<6} {qty:>5d} ${entry:>11.2f} ${curr:>9.2f} ${pnl:>+15.2f} {pnl_pct:>+8.2f}%")
            print("  " + "-" * 62)
            print(f"  Total Open Book PnL: ${total_unrealized:>+14.2f}")
    except Exception as e:
        print(f"[!] Failed to fetch positions: {e}")
    print("-" * 80)

    # 4. Working & Queued Orders (Active Bracket Monitoring)
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = tc.get_orders(filter=req)
        print(f"[ACTIVE ORDERS IN BROKER QUEUE: {len(orders)}]")
        if len(orders) == 0:
            print("  No working orders in broker queue.")
        else:
            print(f"  {'SYMBOL':<6} {'SIDE':<5} {'QTY':>5} {'TYPE':<7} {'STATUS':<9} {'CLASS':<8} {'SUBMITTED (UTC)':<20}")
            print("  " + "-" * 68)
            for o in orders:
                created = o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "N/A"
                print(f"  {o.symbol:<6} {o.side.name:<5} {int(float(o.qty)):>5d} {o.order_type.name:<7} {o.status.name:<9} {o.order_class.name:<8} {created:<20}")
    except Exception as e:
        print(f"[!] Failed to fetch orders: {e}")
    print("-" * 80)

    # 5. Recent Fills (Last 5 Executed Trades)
    try:
        closed_req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=5)
        recent_orders = tc.get_orders(filter=closed_req)
        print("[LAST 5 COMPLETED / EXECUTED TRADES]")
        if len(recent_orders) == 0:
            print("  No recent trade fills recorded.")
        else:
            for ro in recent_orders:
                fill_price = f"${float(ro.filled_avg_price):.2f}" if ro.filled_avg_price else "CANCELED/UNFILLED"
                filled_at = ro.filled_at.strftime("%Y-%m-%d %H:%M:%S") if ro.filled_at else "N/A"
                print(f"  {ro.symbol:<6} {ro.side.name:<5} {ro.filled_qty or ro.qty} shares @ {fill_price:<12} | Status: {ro.status.name:<8} | Time: {filled_at}")
    except Exception as e:
        print(f"[!] Failed to fetch recent executions: {e}")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    run_report()
