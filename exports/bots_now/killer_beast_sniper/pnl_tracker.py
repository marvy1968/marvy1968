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
import pandas as pd
from collections import defaultdict
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"
PAPER      = True

trade_client = TradingClient(API_KEY, SECRET_KEY, paper=PAPER)

def calculate_fifo_pnl(orders):
    # Filter out orders missing filled_at or filled_qty
    valid_orders = [
        o for o in orders 
        if o.filled_at is not None and o.filled_qty is not None and float(o.filled_qty) > 0
    ]
    
    # Sort valid orders chronologically
    sorted_orders = sorted(valid_orders, key=lambda x: x.filled_at)
    
    positions = defaultdict(list)  # symbol -> list of [qty, price]
    closed_trades = []

    for o in sorted_orders:
        sym = o.symbol
        side = o.side.value.lower()
        qty = float(o.filled_qty)
        price = float(o.filled_avg_price) if o.filled_avg_price else 0.0

        if price == 0.0:
            continue

        if side == 'buy':
            positions[sym].append([qty, price])
        elif side == 'sell' and positions[sym]:
            remaining_sell_qty = qty
            trade_pnl = 0.0
            cost_basis = 0.0

            while remaining_sell_qty > 0 and positions[sym]:
                buy_qty, buy_price = positions[sym][0]

                matched_qty = min(remaining_sell_qty, buy_qty)
                pnl = matched_qty * (price - buy_price)
                trade_pnl += pnl
                cost_basis += matched_qty * buy_price

                remaining_sell_qty -= matched_qty
                positions[sym][0][0] -= matched_qty

                if positions[sym][0][0] <= 1e-6:
                    positions[sym].pop(0)

            closed_trades.append({
                "symbol": sym,
                "pnl": trade_pnl,
                "cost_basis": cost_basis,
                "return_pct": (trade_pnl / cost_basis * 100) if cost_basis > 0 else 0.0,
                "closed_at": o.filled_at
            })

    return pd.DataFrame(closed_trades)

def generate_pnl_report():
    print("=" * 65)
    print("      📊 KILLER BEAST SNIPER — DETAILED P&L & PERFORMANCE      ")
    print("=" * 65)

    try:
        acct = trade_client.get_account()
        equity = float(acct.equity)
        cash = float(acct.cash)

        print(f"💰 Current Equity:    ${equity:,.2f}")
        print(f"💵 Available Cash:   ${cash:,.2f}")
        print("-" * 65)

        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=500)
        closed_orders = trade_client.get_orders(req)

        if not closed_orders:
            print("ℹ️ No closed orders found.")
            print("=" * 65)
            return

        df_pnl = calculate_fifo_pnl(closed_orders)

        if df_pnl.empty:
            print("ℹ️ No completed buy/sell matched trades found yet.")
            print("=" * 65)
            return

        total_trades = len(df_pnl)
        wins = df_pnl[df_pnl['pnl'] > 0]
        losses = df_pnl[df_pnl['pnl'] < 0]
        breakevens = df_pnl[df_pnl['pnl'] == 0]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0.0

        total_pnl = df_pnl['pnl'].sum()
        gross_profit = wins['pnl'].sum()
        gross_loss = abs(losses['pnl'].sum())

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

        avg_win = wins['pnl'].mean() if win_count > 0 else 0.0
        avg_loss = losses['pnl'].mean() if loss_count > 0 else 0.0

        pnl_color = "🟢" if total_pnl >= 0 else "🔴"

        print(f"🎯 Total Closed Trades:   {total_trades}")
        print(f"✅ Winning Trades:       {win_count} ({win_rate:.1f}%)")
        print(f"❌ Losing Trades:        {loss_count} ({100 - win_rate:.1f}%)")
        if len(breakevens) > 0:
            print(f"🛡️ Break-Even Trades:     {len(breakevens)}")
        print("-" * 65)
        print(f"{pnl_color} Total Realized P&L:   ${total_pnl:,.2f}")
        print(f"📈 Gross Profit:         ${gross_profit:,.2f}")
        print(f"📉 Gross Loss:           ${gross_loss:,.2f}")
        print(f"📊 Profit Factor:        {profit_factor:.2f}")
        print("-" * 65)
        print(f"🟢 Average Win:          ${avg_win:,.2f}")
        print(f"🔴 Average Loss:         ${avg_loss:,.2f}")
        print("=" * 65)

    except Exception as e:
        print(f"❌ Error compiling P&L metrics: {e}")

if __name__ == "__main__":
    generate_pnl_report()
